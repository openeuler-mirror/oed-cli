#!/usr/bin/env bash
# oed one-line installer.
#
# Installs the `oed` CLI (pip) and wires the `oed-mcp` MCP server into your
# agent client's configuration, so any MCP-capable agent can drive every
# openEuler Infra service. The MCP server itself is `npx -y oed-mcp`
# (Node >= 18); it bootstraps the `oed` CLI automatically if pip is available.
#
#   curl -fsSL https://raw.gitcode.com/openeuler/oed-cli/raw/master/install.sh | bash             # auto-detect
#   curl -fsSL https://raw.gitcode.com/openeuler/oed-cli/raw/master/install.sh | bash -s cursor   # pick a client
#   bash install.sh list
#   bash install.sh --uninstall cursor
#
# No secrets are handled here: most gateway services work anonymously, and
# `oed login` / `oed ag login` are interactive by design.
set -euo pipefail

MCP_NPM="oed-mcp"
MCP_ARGS='["-y","oed-mcp"]'

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m ✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m !\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m ✗\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
oed installer — install the oed CLI, then register the oed-mcp MCP server
with one agent client so it can call every openEuler Infra service.

Usage:
  install.sh [client]        install CLI + register client (no arg = auto-detect)
  install.sh list            list supported clients
  install.sh --uninstall [client]

Clients:
  claude       Claude Code          claude mcp add oed -- npx -y oed-mcp
  cursor       Cursor               ~/.cursor/mcp.json
  trae         Trae (intl)          ~/.trae/mcp.json
  trae-cn      Trae 中国版           ~/.trae-cn/mcp.json
  codex        Codex CLI            ~/.codex/config.toml
  dsh          dsh                  dsh plugin add oed-mcp
  gemini       Gemini CLI           gemini extensions install Hourunze1997/oed-mcp
  qwen         Qwen Code            qwen extensions install Hourunze1997/oed-mcp
  cli-only     只安装 CLI，不改任何客户端配置

Most gateway services (CVE, packages, forum, mailman, search) work
anonymously. Services that need identity: run `oed login` (openEuler oneid)
or `oed ag login` (AtomGit) afterwards — interactively, by you.
EOF
}

have() { command -v "$1" >/dev/null 2>&1; }

install_cli() {
  if have oed; then
    ok "oed CLI already installed: $(oed --version 2>/dev/null | head -1 || echo 'oed')"
    return
  fi
  info "Installing oed CLI via pip..."
  if have pipx; then
    pipx install oed-cli
  elif have uv; then
    uv tool install oed-cli
  elif have pip3; then
    pip3 install --user oed-cli
  elif have pip; then
    pip install --user oed-cli
  else
    die "No pip/pipx/uv found. Install Python 3.10+ first, then re-run."
  fi
  have oed || die "pip finished but 'oed' is not on PATH — check ~/.local/bin is on PATH, then re-run."
  ok "Installed $(oed --version 2>/dev/null | head -1)"
}

check_node() {
  if have npx; then return; fi
  warn "npx not found — the oed-mcp MCP server needs Node.js >= 18 (the CLI itself does not)."
  warn "Install Node from https://nodejs.org, then re-run this step."
}

backup() { # backup <file>
  [ -f "$1" ] && cp "$1" "$1.oed-backup" && info "Backup written: $1.oed-backup"
  return 0
}

# register_json <mcp.json-path>  — merge {"mcpServers":{"oed":{npx...}}} into a JSON config
register_json() {
  local f="$1"
  backup "$f"
  python3 - "$f" "$MCP_NPM" "$MCP_ARGS" <<'PY'
import json, os, sys
path, npm, args = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
data = {}
if os.path.exists(path) and os.path.getsize(path) > 0:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as e:
        sys.exit(f"✗ {path} is not valid JSON ({e}). Fix or remove it and re-run — a backup was just written to {path}.oed-backup.")
srv = data.setdefault("mcpServers", {})
srv["oed"] = {"command": "npx", "args": args}
os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
with open(path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
PY
  ok "Registered oed MCP server in $f"
}

unregister_json() {
  local f="$1"
  [ -f "$f" ] || { warn "No $f — nothing to do"; return 0; }
  python3 - "$f" "$MCP_NPM" <<'PY'
import json, os, sys
path, npm = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as fh:
    data = json.load(fh)
removed = data.get("mcpServers", {}).pop("oed", None)
if removed is not None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(f" ✓ Removed oed from {path}")
else:
    print(f" ! oed not found in {path}")
PY
}

register_claude() {
  if have claude; then
    if claude mcp get oed >/dev/null 2>&1; then
      ok "Claude Code already has an 'oed' MCP server (verify: claude mcp get oed)"
      return
    fi
    # --scope user: register globally; the default local scope would bind the
    # server to the current directory only.
    claude mcp add -s user oed -- npx -y "$MCP_NPM" &&
      ok "Registered globally: claude mcp add -s user oed -- npx -y $MCP_NPM"
  else
    warn "claude CLI not found. In Claude Code run manually:"
    printf '      claude mcp add -s user oed -- npx -y %s\n' "$MCP_NPM"
  fi
}

# dsh is profile-based: every subcommand needs --profile <name>. Pick the
# only profile automatically; with several, ask via DSH_PROFILE=<name>.
register_dsh() {
  local prof="${DSH_PROFILE:-}"
  if [ -z "$prof" ]; then
    local dir="$HOME/.dsh/profiles"
    if [ -d "$dir" ]; then
      local count
      count=$(ls -1 "$dir" 2>/dev/null | grep -c . || true)
      if [ "$count" -eq 1 ]; then
        prof=$(ls -1 "$dir" | head -1)
        info "Using the only dsh profile: $prof"
      elif [ "$count" -gt 1 ]; then
        warn "Multiple dsh profiles found: $(ls -1 "$dir" | tr '\n' ' ')"
        warn "Re-run with one: DSH_PROFILE=<name> bash install.sh dsh"
        return 0
      fi
    fi
  fi
  if [ -n "$prof" ]; then
    dsh plugin --profile "$prof" add "$MCP_NPM" &&
      ok "Added $MCP_NPM as a dsh plugin in profile '$prof'"
  else
    warn "No dsh profile found. Manual: dsh plugin --profile <name> add $MCP_NPM"
  fi
}

register_codex() {
  local f="${CODEX_HOME:-$HOME/.codex}/config.toml"
  backup "$f"
  mkdir -p "$(dirname "$f")"
  if [ -f "$f" ] && grep -q 'mcp_servers\.oed' "$f"; then
    ok "Codex already has an [mcp_servers.oed] entry ($f)"
    return
  fi
  cat >>"$f" <<EOF

[mcp_servers.oed]
command = "npx"
args = ["-y", "$MCP_NPM"]
EOF
  ok "Appended [mcp_servers.oed] to $f"
}

unregister_codex() {
  local f="${CODEX_HOME:-$HOME/.codex}/config.toml"
  [ -f "$f" ] || { warn "No $f — nothing to do"; return 0; }
  backup "$f"
  python3 - "$f" <<'PY'
import sys
path = sys.argv[1]
lines = open(path, encoding="utf-8").read().splitlines(keepends=True)
out, skipping = [], False
for line in lines:
    s = line.strip()
    if s == "[mcp_servers.oed]":
        skipping = True
        if out and out[-1].strip() == "":
            out.pop()  # drop the blank line that separated the block
        continue
    if skipping:
        if s.startswith("["):  # next TOML section — stop skipping
            skipping = False
            out.append(line)
        continue
    out.append(line)
open(path, "w", encoding="utf-8").write("".join(out))
print(" ✓ Removed [mcp_servers.oed] block" if "".join(out) != "".join(lines) else f" ! oed not found in {path}")
PY
}

do_register() {
  case "$1" in
    claude)    register_claude ;;
    cursor)    register_json "$HOME/.cursor/mcp.json" ;;
    trae)      register_json "$HOME/.trae/mcp.json" ;;
    trae-cn)   register_json "$HOME/.trae-cn/mcp.json" ;;
    codex)     register_codex ;;
    dsh)       register_dsh ;;
    gemini)    if have gemini; then gemini extensions install "Hourunze1997/$MCP_NPM"; else warn "gemini CLI not found. Manual: gemini extensions install Hourunze1997/$MCP_NPM"; fi ;;
    qwen)      if have qwen; then qwen extensions install "Hourunze1997/$MCP_NPM"; else warn "qwen CLI not found. Manual: qwen extensions install Hourunze1997/$MCP_NPM"; fi ;;
    cli-only)  ok "Skipping client configuration." ;;
    *)         die "Unknown client '$1'. Run: $0 list" ;;
  esac
}

do_unregister() {
  case "$1" in
    claude)    if have claude; then claude mcp remove -s user oed 2>/dev/null || claude mcp remove oed 2>/dev/null || warn "'oed' not found in Claude Code config"; else warn "claude CLI not found"; fi ;;
    cursor)    unregister_json "$HOME/.cursor/mcp.json" ;;
    trae)      unregister_json "$HOME/.trae/mcp.json" ;;
    trae-cn)   unregister_json "$HOME/.trae-cn/mcp.json" ;;
    codex)     unregister_codex ;;
    dsh)       warn "Remove manually: dsh plugin --profile <name> remove $MCP_NPM (check the profile's bundle patch)." ;;
    gemini)    warn "Remove manually: gemini extensions list, then uninstall the oed extension." ;;
    qwen)      warn "Remove manually: qwen extensions list, then uninstall the oed extension." ;;
    cli-only)  ok "Nothing to unregister." ;;
    *)         die "Unknown client '$1'. Run: $0 list" ;;
  esac
}

detect_client() {
  for c in claude cursor dsh gemini qwen codex; do
    if have "$c"; then echo "$c"; return 0; fi
  done
  for d in "$HOME/.cursor" "$HOME/.codex" "$HOME/.trae" "$HOME/.trae-cn"; do
    if [ -d "$d" ]; then basename "$d" | sed 's/^\.//'; return 0; fi
  done
  echo ""
}

main() {
  local action="register"
  local client=""
  while [ $# -gt 0 ]; do
    case "$1" in
      -h|--help)  usage; exit 0 ;;
      list)       usage; exit 0 ;;
      --uninstall) action="unregister"; shift ;;
      -*)         die "Unknown option $1";;
      *)          client="$1"; shift ;;
    esac
  done

  echo ""
  echo "  oed — one CLI for every openEuler Infra service"
  echo "  https://gitcode.com/openeuler/oed-cli"
  echo ""

  [ -n "$client" ] || { client="$(detect_client)"; }
  [ -n "$client" ] || { warn "No agent client detected — installing the CLI only."; warn "Re-run with a client name to register MCP: $0 <claude|cursor|trae|trae-cn|codex|dsh|gemini|qwen>"; client="cli-only"; }

  info "Target client: $client"
  if [ "$action" = "register" ]; then
    install_cli
    check_node
    do_register "$client"
  else
    do_unregister "$client"
  fi

  echo ""
  if [ "$action" = "register" ] && [ "$client" != "cli-only" ]; then
    ok "Done. Restart your client, then ask it: 查一下 CVE-2019-10082 在 openEuler 的安全公告"
  else
    ok "Done. Verify with: oed --version"
  fi
  echo ""
}

main "$@"

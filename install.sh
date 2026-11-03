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
  gemini       Gemini CLI           gemini extensions install https://github.com/Hourunze1997/oed-mcp
  qwen         Qwen Code            qwen extensions install https://github.com/Hourunze1997/oed-mcp:oed
  openclaw     OpenClaw             openclaw mcp add oed -- npx -y oed-mcp
  hermes       Hermes Agent         ~/.hermes/config.yaml
  kimi         Kimi Code CLI        ~/.kimi-code/mcp.json
  vscode       VS Code (Copilot)    code --add-mcp oed
  copilot      Copilot CLI          copilot mcp add oed -- npx -y oed-mcp
  antigravity  Antigravity          ~/.gemini/antigravity/mcp_config.json
  pi           Pi coding agent      pi mcp add oed -- npx -y oed-mcp
  nanobot      Nanobot              ~/.nanobot/config.json (tools.mcpServers)
  kiro         Kiro CLI / IDE       ~/.kiro/settings/mcp.json
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

# OpenClaw keeps MCP servers under mcp.servers in ~/.openclaw/openclaw.json.
# Old versions ship 'mcp show' but no 'mcp add', and some builds exit 0 on
# unknown subcommands, so probing --help lies — attempt the real add, then
# write the config ourselves (same shape the CLI reads) as the fallback.
register_openclaw() {
  local f="$HOME/.openclaw/openclaw.json"
  if have openclaw && openclaw mcp show oed >/dev/null 2>&1; then
    ok "OpenClaw already has an 'oed' MCP server (verify: openclaw mcp show oed)"
    return
  fi
  if [ -f "$f" ] && grep -q '"oed"' "$f"; then
    ok "OpenClaw already has an 'oed' MCP server ($f)"
    return
  fi
  if have openclaw && openclaw mcp add oed --command npx --arg -y --arg "$MCP_NPM" 2>/dev/null; then
    ok "Registered: openclaw mcp add oed --command npx --arg -y --arg $MCP_NPM"
  else
    backup "$f"
    mkdir -p "$(dirname "$f")"
    python3 - "$f" "$MCP_ARGS" <<'PY'
import json, os, sys
path, args = sys.argv[1], json.loads(sys.argv[2])
data = {}
if os.path.exists(path) and os.path.getsize(path) > 0:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as e:
        sys.exit(f"✗ {path} is not valid JSON ({e}). Fix or remove it and re-run — a backup was just written to {path}.oed-backup.")
data.setdefault("mcp", {}).setdefault("servers", {})["oed"] = {"command": "npx", "args": args}
os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
with open(path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
PY
    ok "Registered oed MCP server in $f (mcp.servers — openclaw mcp show oed to verify)"
  fi
}

unregister_openclaw() {
  local f="$HOME/.openclaw/openclaw.json"
  [ -f "$f" ] || { warn "No $f — nothing to do"; return 0; }
  python3 - "$f" <<'PY'
import json, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    data = json.load(fh)
removed = data.get("mcp", {}).get("servers", {}).pop("oed", None)
if removed is not None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(f" ✓ Removed oed from {path}")
else:
    print(f" ! oed not found in {path}")
PY
}

# Hermes Agent has no generic CLI add command — its MCP servers are a YAML
# mapping under top-level mcp_servers in ~/.hermes/config.yaml. Edit the file
# directly (line-scan, no PyYAML dependency), same spirit as register_codex.
register_hermes() {
  local f="$HOME/.hermes/config.yaml"
  backup "$f"
  mkdir -p "$(dirname "$f")"
  python3 - "$f" "$MCP_NPM" <<'PY'
import os, sys
path, npm = sys.argv[1], sys.argv[2]
child = ["  oed:", '    command: "npx"', f'    args: ["-y", "{npm}"]']
lines = []
if os.path.exists(path) and os.path.getsize(path) > 0:
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().rstrip("\n").splitlines()

idx, inline = None, ""
for i, ln in enumerate(lines):
    if ln.startswith("mcp_servers"):
        idx, inline = i, ln.split(":", 1)[1].strip() if ":" in ln else ""
        break

registered = False
if idx is not None and not inline:
    for ln in lines[idx + 1:]:
        if ln and not ln.startswith(" "):
            break
        if ln.strip() == "oed:":
            registered = True
            break

if registered:
    print(f" ✓ Hermes already has mcp_servers.oed ({path})")
    sys.exit(0)

if idx is None:
    out = lines + ["", "mcp_servers:"] + child if lines else ["mcp_servers:"] + child
elif inline == "":
    out = lines[:idx + 1] + child + lines[idx + 1:]
elif inline == "{}":
    out = lines[:idx] + ["mcp_servers:"] + child + lines[idx + 1:]
else:
    sys.exit(f" ✗ {path} has an inline 'mcp_servers: {inline}' I can't merge — add the oed block manually, a backup was written to {path}.oed-backup")

with open(path, "w", encoding="utf-8") as fh:
    fh.write("\n".join(out) + "\n")
print(f" ✓ Registered oed MCP server in {path}")
PY
  ok "Hermes Agent picks it up on the next hermes chat (or /reload-mcp)"
}

unregister_hermes() {
  local f="$HOME/.hermes/config.yaml"
  [ -f "$f" ] || { warn "No $f — nothing to do"; return 0; }
  backup "$f"
  python3 - "$f" <<'PY'
import sys
path = sys.argv[1]
lines = open(path, encoding="utf-8").read().splitlines()
out, section, in_oed, removed = [], False, False, False
for ln in lines:
    if in_oed:
        if ln.strip() and len(ln) - len(ln.lstrip(" ")) < 4:
            in_oed = False  # next sibling / next top-level key: stop skipping
        else:
            continue
    if ln == "mcp_servers:":
        section = True
    elif section and ln and not ln.startswith(" "):
        section = False
    if section and (ln.startswith("  oed:") or ln.strip() == "oed:"):
        in_oed, removed = True, True
        continue
    out.append(ln)
if removed:
    open(path, "w", encoding="utf-8").write("\n".join(out) + "\n")
    print(f" ✓ Removed mcp_servers.oed block from {path}")
else:
    print(f" ! oed not found in {path}")
PY
}

# VS Code ships an official --add-mcp flag (writes user-scope config); no
# reliable cross-platform user mcp.json path to pre-check, so always run it —
# adding the same name twice just overwrites the entry.
register_vscode() {
  if have code; then
    code --add-mcp "{\"name\":\"oed\",\"command\":\"npx\",\"args\":[\"-y\",\"$MCP_NPM\"]}" &&
      ok "Registered oed MCP server (user scope) via code --add-mcp"
  else
    warn "code CLI not found. In VS Code: Command Palette → 'MCP: Add Server', or manually:"
    printf '      code --add-mcp '"'"'{"name":"oed","command":"npx","args":["-y","%s"]}'"'"'\n' "$MCP_NPM"
  fi
}

register_copilot_cli() {
  local f="$HOME/.copilot/mcp-config.json"
  if [ -f "$f" ] && grep -q '"oed"' "$f"; then
    ok "Copilot CLI already has an 'oed' MCP server ($f)"
    return
  fi
  if have copilot && copilot mcp add oed -- npx -y "$MCP_NPM" 2>/dev/null; then
    ok "Registered: copilot mcp add oed -- npx -y $MCP_NPM"
  else
    register_json "$f"
  fi
}

register_pi() {
  local f="$HOME/.pi/agent/mcp.json"
  if [ -f "$f" ] && grep -q '"oed"' "$f"; then
    ok "Pi already has an 'oed' MCP server ($f)"
    return
  fi
  if have pi && pi mcp add oed -- npx -y "$MCP_NPM" 2>/dev/null; then
    ok "Registered: pi mcp add oed -- npx -y $MCP_NPM"
  else
    register_json "$f"
  fi
}

# Gemini CLI errors on a duplicate install ("already installed", exit 1), so
# check the extension list first. Source must be a full git URL or local path
# — owner/repo shorthand fails with "Install source not found" (verified on
# gemini 0.66.0); --consent skips the interactive third-party-code prompt.
register_gemini() {
  if ! have gemini; then
    warn "gemini CLI not found. Manual: gemini extensions install https://github.com/Hourunze1997/$MCP_NPM"
    return 0
  fi
  # gemini prints the listing to stderr, not stdout — hence 2>&1.
  if gemini extensions list 2>&1 | grep -q '✓ oed'; then
    ok "Gemini CLI already has the oed extension (verify: gemini extensions list)"
    return 0
  fi
  gemini extensions install "https://github.com/Hourunze1997/$MCP_NPM" --consent &&
    ok "Installed the oed extension (verify: gemini extensions list)"
}

# Qwen Code resolves sources in its own order: a bare name goes to its npm
# channel (needs qwen-extension.json inside the package — npm 0.2.0 lacks it),
# and a plain git URL of our repo trips an interactive claude-marketplace
# picker (the repo also ships .claude-plugin/marketplace.json), so use the
# non-interactive url:plugin form. Failed installs exit 0 — verify via list.
register_qwen() {
  if ! have qwen; then
    warn "qwen CLI not found. Manual: qwen extensions install https://github.com/Hourunze1997/$MCP_NPM:oed"
    return 0
  fi
  if qwen extensions list 2>&1 | grep -q '✓ oed'; then
    ok "Qwen Code already has the oed extension (verify: qwen extensions list)"
    return 0
  fi
  qwen extensions install "$MCP_NPM" --consent >/dev/null 2>&1 || true
  if qwen extensions list 2>&1 | grep -q '✓ oed'; then
    ok "Registered via the npm channel: qwen extensions install $MCP_NPM"
    return 0
  fi
  qwen extensions install "https://github.com/Hourunze1997/$MCP_NPM:oed" --consent >/dev/null 2>&1 || true
  if qwen extensions list 2>&1 | grep -q '✓ oed'; then
    ok "Registered via the git channel: qwen extensions install https://github.com/Hourunze1997/$MCP_NPM:oed"
  else
    warn "qwen extensions install failed silently (it exits 0 on errors). Manual:"
    printf '      qwen extensions install https://github.com/Hourunze1997/%s:oed\n' "$MCP_NPM"
  fi
}

# Nanobot nests MCP servers under tools.mcpServers in ~/.nanobot/config.json,
# not at the top level — needs its own merge, register_json would miss it.
register_nanobot() {
  local f="$HOME/.nanobot/config.json"
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
data.setdefault("tools", {}).setdefault("mcpServers", {})["oed"] = {"command": "npx", "args": args}
os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
with open(path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
PY
  ok "Registered oed MCP server in $f (nanobot reads tools.mcpServers)"
}

unregister_nanobot() {
  local f="$HOME/.nanobot/config.json"
  [ -f "$f" ] || { warn "No $f — nothing to do"; return 0; }
  python3 - "$f" <<'PY'
import json, sys
path = sys.argv[1]
data = json.load(open(path, encoding="utf-8"))
removed = data.get("tools", {}).get("mcpServers", {}).pop("oed", None)
if removed is not None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(f" ✓ Removed oed from {path}")
else:
    print(f" ! oed not found in {path}")
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
    openclaw)  register_openclaw ;;
    hermes)    register_hermes ;;
    kimi)      register_json "${KIMI_CODE_HOME:-$HOME/.kimi-code}/mcp.json" ;;
    vscode)    register_vscode ;;
    copilot)   register_copilot_cli ;;
    antigravity) register_json "$HOME/.gemini/antigravity/mcp_config.json" ;;
    pi)        register_pi ;;
    nanobot)   register_nanobot ;;
    kiro)      register_json "$HOME/.kiro/settings/mcp.json" ;;
    gemini)    register_gemini ;;
    qwen)      register_qwen ;;
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
    openclaw)  unregister_openclaw ;;
    hermes)    unregister_hermes ;;
    kimi)      unregister_json "${KIMI_CODE_HOME:-$HOME/.kimi-code}/mcp.json" ;;
    vscode)    for f in "$HOME/.config/Code/User/mcp.json" "$HOME/Library/Application Support/Code/User/mcp.json"; do
                 if [ -f "$f" ]; then unregister_json "$f"; fi
               done ;;
    copilot)   unregister_json "$HOME/.copilot/mcp-config.json" ;;
    antigravity) unregister_json "$HOME/.gemini/antigravity/mcp_config.json" ;;
    pi)        unregister_json "$HOME/.pi/agent/mcp.json" ;;
    nanobot)   unregister_nanobot ;;
    kiro)      unregister_json "$HOME/.kiro/settings/mcp.json" ;;
    gemini)    if have gemini; then gemini extensions uninstall oed && ok "Removed the oed extension from Gemini CLI" || warn "'oed' extension not found in Gemini CLI"; else warn "gemini CLI not found"; fi ;;
    qwen)      if have qwen; then qwen extensions uninstall oed && ok "Removed the oed extension from Qwen Code" || warn "'oed' extension not found in Qwen Code"; else warn "qwen CLI not found"; fi ;;
    cli-only)  ok "Nothing to unregister." ;;
    *)         die "Unknown client '$1'. Run: $0 list" ;;
  esac
}

detect_client() {
  for c in claude cursor dsh gemini qwen codex openclaw kimi hermes copilot pi nanobot; do
    if have "$c"; then echo "$c"; return 0; fi
  done
  if have code; then echo vscode; return 0; fi
  if have kiro-cli; then echo kiro; return 0; fi
  for d in "$HOME/.cursor" "$HOME/.codex" "$HOME/.trae" "$HOME/.trae-cn" "$HOME/.openclaw" "$HOME/.hermes" "$HOME/.gemini/antigravity" "$HOME/.kiro" "$HOME/.nanobot" "$HOME/.pi"; do
    if [ -d "$d" ]; then basename "$d" | sed 's/^\.//'; return 0; fi
  done
  if [ -d "${KIMI_CODE_HOME:-$HOME/.kimi-code}" ]; then echo kimi; return 0; fi
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
  [ -n "$client" ] || { warn "No agent client detected — installing the CLI only."; warn "Re-run with a client name to register MCP: $0 <claude|cursor|trae|trae-cn|codex|dsh|gemini|qwen|openclaw|hermes|kimi|vscode|copilot|antigravity|pi|nanobot|kiro>"; client="cli-only"; }

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

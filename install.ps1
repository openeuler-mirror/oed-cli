<#
.SYNOPSIS
  Windows port of the openEuler oed-cli install.sh.
  Installs the `oed` CLI (pip) and wires the `oed-mcp` MCP server into one
  agent client's config, so any MCP-capable agent can drive every openEuler
  Infra service. The MCP server itself is `npx -y oed-mcp` (Node >= 18);
  on native Windows it is launched via `cmd /c "npx -y oed-mcp"` (quoted).
  The repo's agent skills (.claude/skills/*) are installed into each client's
  Agent-Skills directory (~/.claude\skills for Claude Code, ~\.dsh\skills for
  dsh; receipt-tracked), mirroring install.sh.

.EXAMPLE
  .\install-oed.ps1                 # auto-detect client
  .\install-oed.ps1 cursor          # pick a client
  .\install-oed.ps1 -List
  .\install-oed.ps1 -Uninstall cursor
#>
param(
  [Parameter(Position = 0)][string]$Client = '',
  [switch]$Uninstall,
  [switch]$List
)

$ErrorActionPreference = 'Stop'

$NpmPkg  = 'oed-mcp'
# Native Windows clients cannot spawn npx directly (it is npx.cmd, and Node
# blocks spawning .cmd without a shell) - every MCP entry uses cmd /c.
$McpEntry = [PSCustomObject]@{ command = 'cmd'; args = @('/c', 'npx -y oed-mcp') }

$KimiHome  = if ($env:KIMI_CODE_HOME) { $env:KIMI_CODE_HOME } else { Join-Path $env:USERPROFILE '.kimi-code' }
$CodexToml = if ($env:CODEX_HOME)     { Join-Path $env:CODEX_HOME 'config.toml' } else { Join-Path $env:USERPROFILE '.codex\config.toml' }
$OedRepo       = if ($env:OED_REPO) { $env:OED_REPO } else { 'https://gitcode.com/openeuler/oed-cli.git' }
$SkillsDir      = Join-Path $env:USERPROFILE '.claude\skills'
$OpencodeConfig = Join-Path $env:USERPROFILE '.config\opencode\opencode.json'
# OpenCode's MCP entry has its own shape (mcp.<name>.{type,command,enabled}) -
# not the mcpServers mapping most other clients use.
$OpencodeEntry = [PSCustomObject]@{ type = 'local'; command = @('cmd', '/c', 'npx -y oed-mcp'); enabled = $true }

$NoBom = New-Object System.Text.UTF8Encoding($false)

function info($m) { Write-Host '==> ' -ForegroundColor Blue -NoNewline; Write-Host $m -ForegroundColor Cyan }
function ok($m)   { Write-Host ' + ' -ForegroundColor Green -NoNewline; Write-Host $m }
function warn($m) { Write-Host ' ! ' -ForegroundColor Yellow -NoNewline; Write-Host $m }
function die($m)  { [Console]::Error.WriteLine(' x ' + $m); exit 1 }

function have([string]$c) { [bool](Get-Command $c -ErrorAction SilentlyContinue) }

function backup([string]$f) {
  if (Test-Path $f) { Copy-Item $f "$f.oed-backup" -Force; info "Backup written: $f.oed-backup" }
}

# Run a native command with stderr swallowed (PS 5.1 turns redirected stderr
# into a throwing ErrorRecord when $ErrorActionPreference is 'Stop').
function Invoke-Quiet([scriptblock]$Cmd) {
  $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try { & $Cmd 2>$null | Out-Null; return $LASTEXITCODE } finally { $ErrorActionPreference = $prev }
}

function Write-TextNoBom([string]$path, [string]$text) {
  $dir = Split-Path $path -Parent
  if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
  [System.IO.File]::WriteAllText($path, $text, $NoBom)
}

function Add-UserPath([string]$dir) {
  if (-not $dir -or -not (Test-Path $dir)) { return }
  $user = [Environment]::GetEnvironmentVariable('Path', 'User')
  $list = @($user -split ';' | Where-Object { $_ })
  if ($list -notcontains $dir) {
    [Environment]::SetEnvironmentVariable('Path', (($list + $dir) -join ';'), 'User')
    ok "Added to user PATH: $dir (takes effect in new terminals)"
  }
  # Refresh the current process too, via the env-block API with the exact-case
  # name: on Unix PowerShell $env:Path is a separate variable from the real
  # PATH, and native-command discovery only consults the real one.
  $sep = [IO.Path]::PathSeparator
  $cur = [string][Environment]::GetEnvironmentVariable('PATH', 'Process')
  if ((($cur -split [regex]::Escape($sep))) -notcontains $dir) {
    [Environment]::SetEnvironmentVariable('PATH', "$cur$sep$dir", 'Process')
  }
}

# ---------------- JSON merge / remove (array-safe serializer) ----------------

# PS 5.1's built-in ConvertTo-Json collapses single-element arrays into
# scalars, which would corrupt unrelated servers already present in the
# user's config - so the parsed config is serialized by hand instead of
# relying on ConvertTo-Json. The serializer reads children inline and only
# ever returns strings: PowerShell unrolls a 1-element collection returned
# from a function into its single element (verified by test), so collections
# must never cross a function-output boundary here. Argument binding DOES
# keep arrays intact, which is what the recursion relies on.

function Escape-Json([string]$s) {
  $sb = New-Object System.Text.StringBuilder
  foreach ($ch in $s.ToCharArray()) {
    if ($ch -eq '"') { [void]$sb.Append('\"') }
    elseif ($ch -eq '\') { [void]$sb.Append('\\') }
    elseif ($ch -eq "`b") { [void]$sb.Append('\b') }
    elseif ($ch -eq "`f") { [void]$sb.Append('\f') }
    elseif ($ch -eq "`n") { [void]$sb.Append('\n') }
    elseif ($ch -eq "`r") { [void]$sb.Append('\r') }
    elseif ($ch -eq "`t") { [void]$sb.Append('\t') }
    elseif ([int]$ch -lt 32) { [void]$sb.Append(('\u{0:x4}' -f [int]$ch)) }
    else { [void]$sb.Append($ch) }
  }
  return $sb.ToString()
}

function ConvertTo-JsonPlain($o) {
  if ($null -eq $o) { return 'null' }
  if ($o -is [bool]) { if ($o) { return 'true' } else { return 'false' } }
  if ($o -is [int] -or $o -is [long] -or $o -is [double] -or $o -is [single] -or $o -is [decimal]) {
    return $o.ToString([System.Globalization.CultureInfo]::InvariantCulture)
  }
  if ($o -is [string]) { return '"' + (Escape-Json $o) + '"' }
  if ($o -is [System.Collections.IDictionary]) {
    $parts = @()
    foreach ($k in $o.Keys) { $parts += '"' + (Escape-Json "$k") + '":' + (ConvertTo-JsonPlain $o[$k]) }
    return '{' + ($parts -join ',') + '}'
  }
  if ($o -is [PSCustomObject]) {
    $parts = @()
    foreach ($p in $o.PSObject.Properties) { $parts += '"' + (Escape-Json $p.Name) + '":' + (ConvertTo-JsonPlain $p.Value) }
    return '{' + ($parts -join ',') + '}'
  }
  if ($o -is [System.Collections.IEnumerable]) {
    $parts = @()
    foreach ($e in $o) { $parts += ConvertTo-JsonPlain $e }
    return '[' + ($parts -join ',') + ']'
  }
  return '"' + (Escape-Json ([string]$o)) + '"'
}

function Get-RootObj([object]$obj, [string[]]$keys) {
  foreach ($k in $keys) {
    $prop = $obj.PSObject.Properties[$k]
    if (-not $prop) { $obj | Add-Member NoteProperty $k (New-Object PSObject) }
    elseif ($null -eq $prop.Value) { $prop.Value = New-Object PSObject }
    $obj = $obj.$k
  }
  return $obj
}

# Set-JsonServer <path> <root keys, outermost first> [entry]  e.g. @('tools','mcpServers')
# $entry defaults to the mcpServers shape; OpenCode passes its own object.
function Set-JsonServer([string]$path, [string[]]$rootKeys, $entry = $McpEntry) {
  backup $path
  $data = New-Object PSObject
  if ((Test-Path $path) -and ((Get-Item $path).Length -gt 0)) {
    try { $data = Get-Content $path -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { die "$path is not valid JSON ($($_.Exception.Message)). Fix or remove it and re-run - a backup was written to $path.oed-backup." }
  }
  $srv = Get-RootObj $data $rootKeys
  if ($srv.PSObject.Properties['oed']) { $srv.PSObject.Properties['oed'].Value = $entry }
  else { $srv | Add-Member NoteProperty 'oed' $entry }
  Write-TextNoBom $path ((ConvertTo-JsonPlain $data) + "`n")
  ok "Registered oed MCP server in $path"
}

function Remove-JsonServer([string]$path, [string[]]$rootKeys) {
  if (-not (Test-Path $path)) { warn "No $path - nothing to do"; return }
  backup $path
  try { $data = Get-Content $path -Raw -Encoding UTF8 | ConvertFrom-Json }
  catch { die "$path is not valid JSON ($($_.Exception.Message)). A backup was written to $path.oed-backup." }
  $obj = $data
  foreach ($k in $rootKeys) {
    if ($obj -and $obj.PSObject.Properties[$k]) { $obj = $obj.$k } else { $obj = $null; break }
  }
  if ($obj -and $obj.PSObject.Properties['oed']) {
    $obj.PSObject.Properties.Remove('oed')
    Write-TextNoBom $path ((ConvertTo-JsonPlain $data) + "`n")
    ok "Removed oed from $path"
  } else { warn "oed not found in $path" }
}

# ---------------- CLI-installed clients ----------------

function Install-Cli {
  if (have oed) {
    $v = $null
    try { $v = (oed --version 2>$null | Select-Object -First 1) } catch {}
    ok "oed CLI already installed: $(if ($v) { $v } else { 'oed' })"
    return
  }
  info 'Installing oed CLI via pip ...'
  # os-keyring-windows extra: DPAPI / Credential Manager backend, needed on
  # Windows for `oed login` / `oed ag login` to store tokens (see README).
  $spec = 'oed-cli[os-keyring-windows]'
  if (have pipx) {
    Invoke-Quiet { pipx install $spec }
    Invoke-Quiet { pipx ensurepath }
  }
  elseif (have uv) {
    Invoke-Quiet { uv tool install $spec }
  }
  elseif (have pip3) {
    Invoke-Quiet { pip3 install --user $spec }
  }
  elseif (have pip) {
    Invoke-Quiet { pip install --user $spec }
  }
  else { die 'No pip/pipx/uv found. Install Python 3.10+ first (winget install Python.Python.3.12), then re-run.' }
  # PATH handling is unconditional and idempotent: pipx/uv shim into
  # ~/.local/bin, `pip install --user` into %APPDATA%\Python\Python3XX\Scripts.
  # Both helpers no-op when the directory is missing or already on PATH, so
  # every install route ends with the same deterministic PATH treatment.
  Add-UserPath (Join-Path $env:USERPROFILE '.local\bin')
  Add-PipUserScripts
  if (have oed) {
    $v = $null
    try { $v = (oed --version 2>$null | Select-Object -First 1) } catch {}
    ok "Installed $(if ($v) { $v } else { 'oed' })"
  } else {
    die "'oed' still not on PATH. A NEW terminal is needed for the updated PATH; if it is still missing there, run 'where.exe oed' and re-run this script."
  }
}

function Add-PipUserScripts {
  $scripts = $null
  foreach ($c in 'python', 'py', 'python3') {
    if (have $c) {
      try { $scripts = (& $c -c "import sysconfig;print(sysconfig.get_path('scripts','nt_user'))" 2>$null) } catch { $scripts = $null }
      if ($LASTEXITCODE -eq 0 -and $scripts) { break } else { $scripts = $null }
    }
  }
  if (-not $scripts) {
    $g = Get-ChildItem (Join-Path $env:APPDATA 'Python\Python*\Scripts') -ErrorAction SilentlyContinue |
         Select-Object -Last 1 -ExpandProperty FullName
    if ($g) { $scripts = $g }
  }
  Add-UserPath $scripts
}

function Check-Node {
  if (have npx) { return }
  warn 'npx not found - the oed-mcp MCP server needs Node.js >= 18 (the CLI itself does not).'
  warn 'Install Node from https://nodejs.org (or: winget install OpenJS.NodeJS.LTS), then re-run this step.'
}

# ---------------- per-client register / unregister ----------------

function Test-ClaudeHasOed {
  # `claude mcp get oed` exits non-zero on some older CLI versions even when
  # the server exists, which makes a re-run's duplicate-add look like a
  # failure. Cross-check with `claude mcp list` before calling it a failure.
  $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try { $out = (claude mcp list 2>&1 | Out-String) } finally { $ErrorActionPreference = $prev }
  return ($out -match '\boed\b')
}

function Register-Claude {
  if (have claude) {
    if ((Invoke-Quiet { claude mcp get oed }) -eq 0 -or (Test-ClaudeHasOed)) {
      ok "Claude Code already has an 'oed' MCP server (verify: claude mcp get oed)"; return
    }
    # -s user: register globally; default local scope binds to cwd only.
    # 'npx -y oed-mcp' is passed as ONE argument (quoted) - cmd then runs it
    # as a single command line, which is the robust form across claude builds.
    if ((Invoke-Quiet { claude mcp add -s user oed -- cmd /c 'npx -y oed-mcp' }) -eq 0) {
      ok "Registered globally: claude mcp add -s user oed -- cmd /c `"npx -y $NpmPkg`""
    } elseif (Test-ClaudeHasOed) {
      ok "Claude Code already has an 'oed' MCP server (add reported a duplicate - nothing to do)"
    } else {
      # Some claude versions (e.g. 2.1.39 on Windows) reject or mis-handle the
      # CLI form. ~/.claude.json IS the user-scope store `claude mcp add`
      # writes to - merge the server there directly, preserving everything else.
      Set-JsonServer (Join-Path $env:USERPROFILE '.claude.json') @('mcpServers')
      if (Test-ClaudeHasOed) {
        ok "Registered oed MCP server in ~/.claude.json (this claude version rejects 'claude mcp add')"
      } else {
        warn 'claude mcp add failed - run it manually inside Claude Code:'
        Write-Host "      claude mcp add -s user oed -- cmd /c `"npx -y $NpmPkg`""
      }
    }
  } else {
    warn 'claude CLI not found. Inside Claude Code run manually:'
    Write-Host "      claude mcp add -s user oed -- cmd /c `"npx -y $NpmPkg`""
  }
}

function Unregister-Claude {
  if (-not (have claude)) { warn 'claude CLI not found'; return }
  $rc = Invoke-Quiet { claude mcp remove -s user oed }
  if ($rc -ne 0) { $rc = Invoke-Quiet { claude mcp remove oed } }
  if ($rc -eq 0) { ok "Removed 'oed' from Claude Code" } else { warn "'oed' not found in Claude Code config" }
}

function Register-Codex {
  backup $CodexToml
  $dir = Split-Path $CodexToml -Parent
  if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
  if ((Test-Path $CodexToml) -and (Select-String -Path $CodexToml -Pattern 'mcp_servers\.oed' -Quiet)) {
    ok "Codex already has an [mcp_servers.oed] entry ($CodexToml)"; return
  }
  $block = "`n[mcp_servers.oed]`ncommand = `"cmd`"`nargs = [`"/c`", `"npx -y $NpmPkg`"]`n"
  [System.IO.File]::AppendAllText($CodexToml, $block, $NoBom)
  ok "Appended [mcp_servers.oed] to $CodexToml"
}

function Unregister-Codex {
  if (-not (Test-Path $CodexToml)) { warn "No $CodexToml - nothing to do"; return }
  backup $CodexToml
  $lines = [System.IO.File]::ReadAllLines($CodexToml)
  $out = New-Object System.Collections.Generic.List[string]
  $skipping = $false
  foreach ($ln in $lines) {
    $s = $ln.Trim()
    if ($s -eq '[mcp_servers.oed]') {
      $skipping = $true
      if ($out.Count -gt 0 -and $out[$out.Count - 1].Trim() -eq '') { $out.RemoveAt($out.Count - 1) }
      continue
    }
    if ($skipping) {
      if ($s.StartsWith('[')) { $skipping = $false; $out.Add($ln) }  # next TOML section
      continue
    }
    $out.Add($ln)
  }
  [System.IO.File]::WriteAllLines($CodexToml, $out, $NoBom)
  if ($out.Count -ne $lines.Count) { ok 'Removed [mcp_servers.oed] block' } else { warn "oed not found in $CodexToml" }
}

# Slice with PS range safety (returns @() when the range is empty).
function Slice([object[]]$arr, [int]$from, [int]$to) {
  if ($arr.Count -eq 0 -or $from -gt $to -or $from -ge $arr.Count -or $to -lt 0) { return @() }
  if ($from -lt 0) { $from = 0 }
  if ($to -ge $arr.Count) { $to = $arr.Count - 1 }
  return @($arr[$from..$to])
}

# Hermes has no CLI add command - its MCP servers are a YAML mapping under
# top-level `mcp_servers` in ~/.hermes/config.yaml. Line-scan merge, same as
# install.sh (no YAML dependency).
function Register-Hermes {
  $f = Join-Path $env:USERPROFILE '.hermes\config.yaml'
  backup $f
  $lines = @()
  if ((Test-Path $f) -and ((Get-Item $f).Length -gt 0)) { $lines = [System.IO.File]::ReadAllLines($f) }
  $child = @('  oed:', '    command: "cmd"', '    args: ["/c", "npx -y oed-mcp"]')

  $idx = $null; $inline = ''
  for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i].StartsWith('mcp_servers')) {
      $idx = $i
      $pos = $lines[$i].IndexOf(':')
      if ($pos -ge 0) { $inline = $lines[$i].Substring($pos + 1).Trim() }
      break
    }
  }
  $registered = $false
  if ($null -ne $idx -and $inline -eq '') {
    foreach ($ln in (Slice $lines ($idx + 1) ($lines.Count - 1))) {
      if ($ln -and -not $ln.StartsWith(' ')) { break }
      if ($ln.Trim() -eq 'oed:') { $registered = $true; break }
    }
  }
  if ($registered) { ok "Hermes already has mcp_servers.oed ($f)"; return }

  if ($null -eq $idx) {
    if ($lines.Count -gt 0) { $out = @($lines) + @('') + @('mcp_servers:') + $child }
    else { $out = @('mcp_servers:') + $child }
  }
  elseif ($inline -eq '') {
    $out = @(Slice $lines 0 $idx) + $child + @(Slice $lines ($idx + 1) ($lines.Count - 1))
  }
  elseif ($inline -eq '{}') {
    $out = @(Slice $lines 0 ($idx - 1)) + @('mcp_servers:') + $child + @(Slice $lines ($idx + 1) ($lines.Count - 1))
  }
  else { die "$f has an inline 'mcp_servers: $inline' I can't merge - add the oed block manually, a backup was written to $f.oed-backup" }

  Write-TextNoBom $f (($out -join "`n") + "`n")
  ok "Registered oed MCP server in $f"
  ok 'Hermes Agent picks it up on the next hermes chat (or /reload-mcp)'
}

function Unregister-Hermes {
  $f = Join-Path $env:USERPROFILE '.hermes\config.yaml'
  if (-not (Test-Path $f)) { warn "No $f - nothing to do"; return }
  backup $f
  $lines = [System.IO.File]::ReadAllLines($f)
  $out = New-Object System.Collections.Generic.List[string]
  $section = $false; $inOed = $false; $removed = $false
  foreach ($ln in $lines) {
    if ($inOed) {
      if ($ln.Trim() -and ($ln.Length - $ln.TrimStart(' ').Length) -lt 4) { $inOed = $false }  # next sibling
      else { continue }
    }
    if ($ln -eq 'mcp_servers:') { $section = $true }
    elseif ($section -and $ln -and -not $ln.StartsWith(' ')) { $section = $false }
    if ($section -and ($ln.StartsWith('  oed:') -or $ln.Trim() -eq 'oed:')) { $inOed = $true; $removed = $true; continue }
    $out.Add($ln)
  }
  if ($removed) {
    [System.IO.File]::WriteAllLines($f, $out, $NoBom)
    ok "Removed mcp_servers.oed block from $f"
  } else { warn "oed not found in $f" }
}

function Register-Openclaw {
  $f = Join-Path $env:USERPROFILE '.openclaw\openclaw.json'
  if ((have openclaw) -and ((Invoke-Quiet { openclaw mcp show oed }) -eq 0)) {
    ok "OpenClaw already has an 'oed' MCP server (verify: openclaw mcp show oed)"; return
  }
  if ((Test-Path $f) -and (Select-String -Path $f -Pattern '"oed"' -Quiet)) {
    ok "OpenClaw already has an 'oed' MCP server ($f)"; return
  }
  if ((have openclaw) -and ((Invoke-Quiet { openclaw mcp add oed --command cmd --arg /c --arg 'npx -y oed-mcp' }) -eq 0)) {
    ok 'Registered: openclaw mcp add oed --command cmd --arg /c --arg "npx -y oed-mcp"'
  } else {
    Set-JsonServer $f @('mcp', 'servers')   # OpenClaw keeps servers under mcp.servers
    ok "Registered oed MCP server in $f (mcp.servers - verify: openclaw mcp show oed)"
  }
}

# OpenCode reads MCP servers from the "mcp" key of opencode.json - its own
# entry shape (type / command array / enabled), hence the dedicated object.
function Register-Opencode {
  Set-JsonServer $OpencodeConfig @('mcp') $OpencodeEntry
}

# VS Code: the user-scope mcp.json path IS reliable on Windows, so write it
# directly; fall back to the official `code --add-mcp` when no profile dir exists.
function Register-Vscode {
  $targets = @(
    (Join-Path $env:APPDATA 'Code\User\mcp.json'),
    (Join-Path $env:APPDATA 'Code - Insiders\User\mcp.json'),
    (Join-Path $env:APPDATA 'VSCodium\User\mcp.json')
  ) | Where-Object { Test-Path (Split-Path $_ -Parent) }
  if ($targets) {
    foreach ($f in $targets) { Set-JsonServer $f @('servers') }
    return
  }
  if (have code) {
    # single-quoted literal with \" - survives PS 5.1 native arg quoting
    Invoke-Quiet { code --add-mcp '{\"name\":\"oed\",\"command\":\"cmd\",\"args\":[\"/c\",\"npx -y oed-mcp\"]}' }
    if ($LASTEXITCODE -eq 0) { ok 'Registered oed MCP server (user scope) via code --add-mcp'; return }
  }
  warn "code CLI not found. In VS Code: Command Palette -> 'MCP: Add Server', or manually:"
  Write-Host '      code --add-mcp "{""name"":""oed"",""command"":""cmd"",""args"":[""/c"",""npx -y oed-mcp""]}"'
}

# shared shape for copilot / pi: probe existing config, try the CLI, then JSON
function Register-CliOrJson([string]$exe, [string]$file, [string]$rootKey) {
  if ((Test-Path $file) -and (Select-String -Path $file -Pattern '"oed"' -Quiet)) {
    ok "'$exe' already has an 'oed' MCP server ($file)"; return
  }
  if (have $exe) {
    $rc = Invoke-Quiet { & $exe mcp add oed -- cmd /c 'npx -y oed-mcp' }
    if ($rc -eq 0) { ok "Registered: $exe mcp add oed -- cmd /c `"npx -y $NpmPkg`""; return }
  }
  Set-JsonServer $file @($rootKey)
}

function Register-Gemini {
  if (-not (have gemini)) { warn "gemini CLI not found. Manual: gemini extensions install https://github.com/Hourunze1997/$NpmPkg"; return }
  $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try { $list = (gemini extensions list 2>&1 | Out-String) } finally { $ErrorActionPreference = $prev }
  if ($list -match 'oed') { ok 'Gemini CLI already has the oed extension (verify: gemini extensions list)'; return }
  if ((Invoke-Quiet { gemini extensions install "https://github.com/Hourunze1997/$NpmPkg" --consent }) -eq 0) {
    ok 'Installed the oed extension (verify: gemini extensions list)'
  } else { warn 'gemini extensions install failed. Run it manually with the URL above.' }
}

function Register-Qwen {
  if (-not (have qwen)) { warn "qwen CLI not found. Manual: qwen extensions install https://github.com/Hourunze1997/$NpmPkg`:oed"; return }
  $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try { $list = (qwen extensions list 2>&1 | Out-String) } finally { $ErrorActionPreference = $prev }
  if ($list -match 'oed') { ok 'Qwen Code already has the oed extension (verify: qwen extensions list)'; return }
  # Qwen exits 0 even on failures - always verify via the list afterwards.
  Invoke-Quiet { qwen extensions install $NpmPkg --consent }
  $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try { $list = (qwen extensions list 2>&1 | Out-String) } finally { $ErrorActionPreference = $prev }
  if ($list -match 'oed') { ok "Registered via the npm channel: qwen extensions install $NpmPkg"; return }
  Invoke-Quiet { qwen extensions install "https://github.com/Hourunze1997/$NpmPkg`:oed" --consent }
  $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try { $list = (qwen extensions list 2>&1 | Out-String) } finally { $ErrorActionPreference = $prev }
  if ($list -match 'oed') { ok "Registered via the git channel: qwen extensions install https://github.com/Hourunze1997/$NpmPkg`:oed" }
  else {
    warn 'qwen extensions install failed silently (it exits 0 on errors). Manual:'
    Write-Host "      qwen extensions install https://github.com/Hourunze1997/$NpmPkg`:oed"
  }
}

function Register-Dsh {
  # dsh is profile-based: pick the only profile automatically; with several,
  # ask via $env:DSH_PROFILE.
  $prof = $env:DSH_PROFILE
  if (-not $prof) {
    $dir = Join-Path $env:USERPROFILE '.dsh\profiles'
    if (Test-Path $dir) {
      $names = @(Get-ChildItem $dir -ErrorAction SilentlyContinue | ForEach-Object { $_.Name })
      if ($names.Count -eq 1) { $prof = $names[0]; info "Using the only dsh profile: $prof" }
      elseif ($names.Count -gt 1) {
        warn "Multiple dsh profiles found: $($names -join ' ')"
        warn "Re-run with one: `$env:DSH_PROFILE='<name>'; .\install-oed.ps1 dsh"
        return
      }
    }
  }
  if ($prof) {
    if ((Invoke-Quiet { dsh plugin --profile $prof add oed-mcp }) -eq 0) {
      ok "Added oed-mcp as a dsh plugin in profile '$prof'"
    } else { warn 'dsh plugin add failed - run it manually.' }
  } else { warn "No dsh profile found. Manual: dsh plugin --profile <name> add $NpmPkg" }
}

# ---------------- agent skills (parity with install.sh) ----------------

# Where each client actually reads Agent Skills from - verified in each
# tool's code / --help (Nov 2026): ~/.agents\skills is the cross-agent
# standard shared by OpenClaw, Kimi Code, VS Code (Copilot) and Copilot CLI;
# hermes only reads its native dir; Claude Code and dsh keep theirs.
function Get-SkillDirs([string]$c) {
  switch ($c) {
    'claude' { return @($SkillsDir) }
    'dsh'    { $dshHome = if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $env:USERPROFILE '.dsh' }
               return @(Join-Path $dshHome 'skills') }
    'hermes' { $h = if ($env:HERMES_HOME) { $env:HERMES_HOME } else { Join-Path $env:USERPROFILE '.hermes' }
               return @(Join-Path $h 'skills') }
    { $_ -in 'openclaw', 'kimi', 'vscode', 'copilot' } {
               return @(Join-Path $env:USERPROFILE '.agents\skills') }
    default  { return @() }
  }
}

# Copy the repo's agent skills (.claude/skills/*) into the skills directory
# given as $dest. A sparse clone enumerates whatever skill directories exist
# upstream - skills added to the repo later are picked up by simply
# re-running this script. Each destination gets its own .oed-installed
# receipt so -Uninstall only removes what WE put there: the user's own
# skills are never touched. Best effort - a failure here never blocks the
# CLI or MCP install.
function Install-Skills([string]$dest) {
  if (-not (have git)) { warn 'git not found - skipping skills (CLI and MCP are unaffected)'; return }
  $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ([System.IO.Path]::GetRandomFileName())
  if ((Invoke-Quiet { git clone --depth 1 --filter=blob:none --sparse --quiet $OedRepo $tmp }) -ne 0 -or
      (Invoke-Quiet { git -C $tmp sparse-checkout set .claude/skills }) -ne 0) {
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
    warn "Could not fetch $OedRepo - skipping skills (CLI and MCP are unaffected)"
    return
  }
  New-Item -ItemType Directory -Path $dest -Force | Out-Null
  $receiptPath = Join-Path $dest '.oed-installed'
  $receipt = @()
  if (Test-Path $receiptPath) { $receipt = @(Get-Content $receiptPath -ErrorAction SilentlyContinue) }
  $installed = @()
  foreach ($d in @(Get-ChildItem (Join-Path $tmp '.claude\skills') -Directory -ErrorAction SilentlyContinue)) {
    $name = $d.Name
    $target = Join-Path $dest $name
    if ((Test-Path $target) -and ($receipt -notcontains $name)) {
      warn "skills/$name exists in $dest but was not installed by oed - leaving it alone"
      continue
    }
    if (Test-Path $target) { Remove-Item $target -Recurse -Force }
    Copy-Item $d.FullName $target -Recurse -Force
    if ($receipt -notcontains $name) { $receipt += $name }
    $installed += $name
  }
  Remove-Item $tmp -Recurse -Force
  if ($installed.Count -gt 0) {
    Set-Content -Path $receiptPath -Value $receipt -Encoding ASCII
    ok "Skills installed to $dest :$($installed -join ' ')"
  } else {
    ok "Skills already present at $dest"
  }
}

function Uninstall-Skills([string]$dest) {
  # Remove exactly the skills this script installed (tracked in the receipt);
  # anything else living in the directory stays untouched.
  $receiptPath = Join-Path $dest '.oed-installed'
  if (-not (Test-Path $receiptPath)) { return }
  $removed = @()
  foreach ($name in @(Get-Content $receiptPath -ErrorAction SilentlyContinue)) {
    if (-not $name) { continue }
    $target = Join-Path $dest $name
    if (Test-Path $target) { Remove-Item $target -Recurse -Force; $removed += $name }
  }
  Remove-Item $receiptPath -Force
  if ($removed.Count -gt 0) { ok "Removed skills from $dest :$($removed -join ' ')" }
}

# ---------------- dispatch ----------------

function Do-Register([string]$c) {
  switch ($c) {
    'claude'      { Register-Claude }
    'cursor'      { Set-JsonServer (Join-Path $env:USERPROFILE '.cursor\mcp.json') @('mcpServers') }
    'trae'        { Set-JsonServer (Join-Path $env:APPDATA 'Trae\User\mcp.json') @('mcpServers') }
    'trae-cn'     { Set-JsonServer (Join-Path $env:APPDATA 'Trae CN\User\mcp.json') @('mcpServers')
                    Set-JsonServer (Join-Path $env:APPDATA 'Trae-CN\User\mcp.json') @('mcpServers') }
    'codex'       { Register-Codex }
    'dsh'         { Register-Dsh }
    'gemini'      { Register-Gemini }
    'qwen'        { Register-Qwen }
    'openclaw'    { Register-Openclaw }
    'opencode'    { Register-Opencode }
    'hermes'      { Register-Hermes }
    'kimi'        { Set-JsonServer (Join-Path $KimiHome 'mcp.json') @('mcpServers') }
    'vscode'      { Register-Vscode }
    'copilot'     { Register-CliOrJson 'copilot' (Join-Path $env:USERPROFILE '.copilot\mcp-config.json') 'mcpServers' }
    'antigravity' { Set-JsonServer (Join-Path $env:USERPROFILE '.gemini\antigravity\mcp_config.json') @('mcpServers') }
    'pi'          { Register-CliOrJson 'pi' (Join-Path $env:USERPROFILE '.pi\agent\mcp.json') 'mcpServers' }
    'nanobot'     { Set-JsonServer (Join-Path $env:USERPROFILE '.nanobot\config.json') @('tools', 'mcpServers') }
    'kiro'        { Set-JsonServer (Join-Path $env:USERPROFILE '.kiro\settings\mcp.json') @('mcpServers') }
    'cli-only'    { ok 'Skipping client configuration.' }
    default       { die "Unknown client '$c'. Run: .\install-oed.ps1 -List" }
  }
}

function Do-Unregister([string]$c) {
  switch ($c) {
    'claude'      { Unregister-Claude }
    'cursor'      { Remove-JsonServer (Join-Path $env:USERPROFILE '.cursor\mcp.json') @('mcpServers') }
    'trae'        { Remove-JsonServer (Join-Path $env:APPDATA 'Trae\User\mcp.json') @('mcpServers')
                    Remove-JsonServer (Join-Path $env:USERPROFILE '.trae\mcp.json') @('mcpServers') }
    'trae-cn'     { Remove-JsonServer (Join-Path $env:APPDATA 'Trae CN\User\mcp.json') @('mcpServers')
                    Remove-JsonServer (Join-Path $env:APPDATA 'Trae-CN\User\mcp.json') @('mcpServers')
                    Remove-JsonServer (Join-Path $env:USERPROFILE '.trae-cn\mcp.json') @('mcpServers') }
    'codex'       { Unregister-Codex }
    'dsh'         { warn "Remove manually: dsh plugin --profile <name> remove $NpmPkg (check the profile's bundle patch)." }
    'openclaw'    { Remove-JsonServer (Join-Path $env:USERPROFILE '.openclaw\openclaw.json') @('mcp', 'servers') }
    'opencode'    { Remove-JsonServer $OpencodeConfig @('mcp') }
    'hermes'      { Unregister-Hermes }
    'kimi'        { Remove-JsonServer (Join-Path $KimiHome 'mcp.json') @('mcpServers') }
    'vscode'      { foreach ($f in @((Join-Path $env:APPDATA 'Code\User\mcp.json'),
                                     (Join-Path $env:APPDATA 'Code - Insiders\User\mcp.json'),
                                     (Join-Path $env:APPDATA 'VSCodium\User\mcp.json'))) {
                      if (Test-Path (Split-Path $f -Parent)) { Remove-JsonServer $f @('servers') } } }
    'copilot'     { Remove-JsonServer (Join-Path $env:USERPROFILE '.copilot\mcp-config.json') @('mcpServers') }
    'antigravity' { Remove-JsonServer (Join-Path $env:USERPROFILE '.gemini\antigravity\mcp_config.json') @('mcpServers') }
    'pi'          { Remove-JsonServer (Join-Path $env:USERPROFILE '.pi\agent\mcp.json') @('mcpServers') }
    'nanobot'     { Remove-JsonServer (Join-Path $env:USERPROFILE '.nanobot\config.json') @('tools', 'mcpServers') }
    'kiro'        { Remove-JsonServer (Join-Path $env:USERPROFILE '.kiro\settings\mcp.json') @('mcpServers') }
    'gemini'      { if (have gemini) {
                      if ((Invoke-Quiet { gemini extensions uninstall oed }) -eq 0) { ok 'Removed the oed extension from Gemini CLI' }
                      else { warn "'oed' extension not found in Gemini CLI" }
                    } else { warn 'gemini CLI not found' } }
    'qwen'        { if (have qwen) {
                      if ((Invoke-Quiet { qwen extensions uninstall oed }) -eq 0) { ok 'Removed the oed extension from Qwen Code' }
                      else { warn "'oed' extension not found in Qwen Code" }
                    } else { warn 'qwen CLI not found' } }
    'cli-only'    { ok 'Nothing to unregister.' }
    default       { die "Unknown client '$c'. Run: .\install-oed.ps1 -List" }
  }
}

function Detect-Client {
  foreach ($c in 'claude', 'cursor', 'dsh', 'gemini', 'qwen', 'codex', 'openclaw', 'opencode', 'kimi', 'hermes', 'copilot', 'pi', 'nanobot') {
    if (have $c) { return $c }
  }
  if (have code) { return 'vscode' }
  if (have 'kiro-cli') { return 'kiro' }
  foreach ($d in 'Trae CN', 'Trae-CN') { if (Test-Path (Join-Path $env:APPDATA $d)) { return 'trae-cn' } }
  if (Test-Path (Join-Path $env:APPDATA 'Trae')) { return 'trae' }
  foreach ($m in @(@('.cursor', 'cursor'), @('.codex', 'codex'), @('.trae', 'trae'), @('.trae-cn', 'trae-cn'),
                   @('.openclaw', 'openclaw'), @('.hermes', 'hermes'), @('.gemini\antigravity', 'antigravity'),
                   @('.kiro', 'kiro'), @('.nanobot', 'nanobot'), @('.pi', 'pi'))) {
    if (Test-Path (Join-Path $env:USERPROFILE $m[0])) { return $m[1] }
  }
  if (Test-Path (Join-Path $env:USERPROFILE '.config\opencode')) { return 'opencode' }
  if (Test-Path $KimiHome) { return 'kimi' }
  return ''
}

function Print-Usage {
  Write-Host @'
oed installer (Windows) - install the oed CLI, then register the oed-mcp
MCP server with one agent client so it can call every openEuler Infra service.

Usage:
  .\install-oed.ps1 [client]      install CLI + register client (no arg = auto-detect)
  .\install-oed.ps1 -List         list supported clients
  .\install-oed.ps1 -Uninstall [client]

Clients:
  claude       Claude Code          claude mcp add -s user oed -- cmd /c "npx -y oed-mcp"
  cursor       Cursor               %USERPROFILE%\.cursor\mcp.json
  trae         Trae (intl)          %APPDATA%\Trae\User\mcp.json
  trae-cn      Trae (China)         %APPDATA%\Trae CN|Trae-CN\User\mcp.json
  codex        Codex CLI            %USERPROFILE%\.codex\config.toml
  dsh          dsh                  dsh plugin --profile <name> add oed-mcp
  gemini       Gemini CLI           gemini extensions install https://github.com/Hourunze1997/oed-mcp
  qwen         Qwen Code            qwen extensions install https://github.com/Hourunze1997/oed-mcp:oed
  openclaw     OpenClaw             %USERPROFILE%\.openclaw\openclaw.json (mcp.servers)
  opencode     OpenCode             %USERPROFILE%\.config\opencode\opencode.json (mcp)
  hermes       Hermes Agent         %USERPROFILE%\.hermes\config.yaml
  kimi         Kimi Code CLI        %USERPROFILE%\.kimi-code\mcp.json
  vscode       VS Code (Copilot)    %APPDATA%\Code\User\mcp.json
  copilot      Copilot CLI          %USERPROFILE%\.copilot\mcp-config.json
  antigravity  Antigravity          %USERPROFILE%\.gemini\antigravity\mcp_config.json
  pi           Pi coding agent      %USERPROFILE%\.pi\agent\mcp.json
  nanobot      Nanobot              %USERPROFILE%\.nanobot\config.json (tools.mcpServers)
  kiro         Kiro CLI / IDE       %USERPROFILE%\.kiro\settings\mcp.json
  cli-only     install the CLI only, touch no client config

The repo's agent skills (.claude/skills/*) are installed into the
Agent-Skills directories the chosen client reads - Claude Code:
~/.claude/skills, dsh: ~/.dsh/skills, hermes: ~/.hermes/skills, and the
cross-agent standard ~/.agents/skills (shared by OpenClaw, Kimi Code,
VS Code, Copilot CLI). Clients without skills support skip skills.
Re-run this script to pick up skills added upstream. A receipt
(.oed-installed) tracks what was installed - your own skills are never
touched.

Most gateway services (CVE, packages, forum, mailman, search) work
anonymously. Services that need identity: run `oed login` (openEuler oneid)
or `oed ag login` (AtomGit) afterwards - interactively, by you.
'@
}

# ---------------- main ----------------

if ($List -or $Client -eq 'list') { Print-Usage; exit 0 }

Write-Host ''
Write-Host '  oed - one CLI for every openEuler Infra service'
Write-Host '  https://gitcode.com/openeuler/oed-cli'
Write-Host ''

if (-not $Client) { $Client = Detect-Client }
if (-not $Client) {
  warn 'No agent client detected - installing the CLI only.'
  warn 'Re-run with a client name to register MCP: .\install-oed.ps1 <claude|cursor|trae|trae-cn|codex|dsh|gemini|qwen|openclaw|opencode|hermes|kimi|vscode|copilot|antigravity|pi|nanobot|kiro>'
  $Client = 'cli-only'
}

info "Target client: $Client"
if (-not $Uninstall) {
  Install-Cli
  Check-Node
  Do-Register $Client
  # cli-only promises not to touch any client configuration - ~/.claude\skills
  # is Claude Code's turf, so it stays out of that path too.
  if ($Client -ne 'cli-only') {
    $skillDirs = @(Get-SkillDirs $Client)
    if ($skillDirs.Count -eq 0) {
      info "Client '$Client' has no Agent-Skills directory - skipping skills (MCP registration is unaffected)."
    } else {
      foreach ($d in $skillDirs) { Install-Skills $d }
    }
  }
} else {
  Do-Unregister $Client
  foreach ($d in @(Get-SkillDirs $Client)) { Uninstall-Skills $d }
}

Write-Host ''
if (-not $Uninstall -and $Client -ne 'cli-only') {
  ok 'Done. Restart your client, then ask it: what is the openEuler security notice for CVE-2019-10082?'
} else {
  ok 'Done. Verify with: oed --version'
}
Write-Host ''

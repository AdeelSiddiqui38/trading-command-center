# =====================================================================
#  BRAIN trading setup (Windows) - run once.
#  * Stops the old bot windows (one copy of everything from now on)
#  * Moves trading-paperbot, Vibe-Trading, trading-signal-agent and the
#    Command Center into Documents\BRAIN (old paths keep working via
#    junctions, so nothing that points at them breaks)
#  * Installs the background hub (Documents\BRAIN\trading-hub)
#  * Replaces the Desktop launchers with ONE icon:
#      "Adeel's Trading Command Center"  -> starts bots hidden + opens app
#  Paper trading only. Safe to run again.
# =====================================================================
$ErrorActionPreference = 'Continue'
$H      = $env:USERPROFILE
$BRAIN  = Join-Path $H 'Documents\BRAIN'
$HUB    = Join-Path $BRAIN 'trading-hub'
$ARCH   = Join-Path $BRAIN 'trading-archive'
$REPO   = Join-Path $BRAIN 'trading-command-center'
$REPO_URL = 'https://github.com/AdeelSiddiqui38/trading-command-center.git'
$REPORT = Join-Path $H 'Desktop\BRAIN-setup-report.txt'
New-Item -ItemType Directory -Force -Path $BRAIN, $HUB, $ARCH, (Join-Path $ARCH 'old-shortcuts') | Out-Null
"BRAIN trading setup  $(Get-Date)" | Out-File $REPORT -Encoding utf8
function Say($m){ $m | Add-Content $REPORT; Write-Host $m }

# ---------- 1. stop old copies ----------
Say "`n[1] Stopping old bot windows..."
try { Invoke-RestMethod -Method Post http://127.0.0.1:7777/api/shutdown -TimeoutSec 5 | Out-Null; Say "    asked running hub to shut down"; Start-Sleep -Seconds 4 } catch {}
$pat = 'telegram_listener\.py|paperbot\.py|leverage_bot\.py|vibe-trading(\.exe)?\W+dev|cli\._legacy\s+serve|vite(\.js)?\W.*--port\s+5899|run dev -- --port 5899|trading-signal-agent.*server\.js|BRAIN\\trading-hub\\hub\.py|python(\.exe)?"?\s+"?hub\.py'
$me = $PID
$procs = Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $me -and $_.CommandLine -and $_.CommandLine -match $pat -and $_.CommandLine -notmatch 'setup-windows\.ps1' }
foreach($p in $procs){ Say ("    stop {0} {1}" -f $p.ProcessId, ($p.CommandLine.Substring(0,[Math]::Min(120,$p.CommandLine.Length)))); & taskkill /PID $p.ProcessId /T /F 2>$null | Out-Null }
Start-Sleep -Seconds 3

# ---------- 2. move folders into BRAIN (+ junction at the old path) ----------
function Move-WithJunction($src, $dst){
  $item = Get-Item $src -ErrorAction SilentlyContinue
  if(-not $item){ Say "    (skip) $src not found"; return }
  if($item.LinkType -eq 'Junction'){ Say "    (ok) $src already points to $($item.Target)"; return }
  if(Test-Path $dst){ Say "    !! $dst already exists - left $src where it is"; return }
  try { Move-Item -LiteralPath $src -Destination $dst -ErrorAction Stop }
  catch {
    Say "    Move-Item failed ($($_.Exception.Message)) - copying with robocopy instead"
    & robocopy $src $dst /E /MOVE /R:2 /W:2 /NFL /NDL /NJH /NJS | Out-Null
    if(Test-Path $src){ Say "    !! some files in $src were locked and stayed behind - close programs and re-run"; return }
  }
  New-Item -ItemType Junction -Path $src -Target $dst | Out-Null
  Say "    moved $src  ->  $dst  (old path still works)"
}
Say "`n[2] Moving folders into $BRAIN ..."
Move-WithJunction (Join-Path $H 'trading-paperbot') (Join-Path $BRAIN 'trading-paperbot')
Move-WithJunction (Join-Path $H 'Vibe-Trading')     (Join-Path $BRAIN 'Vibe-Trading')
Move-WithJunction 'C:\trading-signal-agent'          (Join-Path $BRAIN 'trading-signal-agent')

# point paperbot at the new Vibe-Trading location (junction would also work)
$pb = Join-Path $BRAIN 'trading-paperbot\paperbot.py'
if(Test-Path $pb){
  $t = Get-Content $pb -Raw
  $new = $t -replace 'VIBE_TRADING_CWD = r"[^"]*"', ('VIBE_TRADING_CWD = r"' + (Join-Path $BRAIN 'Vibe-Trading') + '"')
  if($new -ne $t){ Copy-Item $pb "$pb.bak" -Force; Set-Content $pb $new -NoNewline -Encoding utf8; Say "    paperbot.py now uses $BRAIN\Vibe-Trading (backup: paperbot.py.bak)" }
}

# ---------- 3. loose trading files from the home folder ----------
Say "`n[3] Archiving old launchers/logs to $ARCH ..."
foreach($f in 'start-trading-command-center.ps1','signal-agent.log','vibe-dev.log','vibe-serve.log','bot-test.log'){
  $p = Join-Path $H $f
  if(Test-Path $p){ Move-Item $p (Join-Path $ARCH $f) -Force; Say "    $f" }
}

# ---------- 4. Command Center source (git = source of truth, synced with Mac) ----------
Say "`n[4] Command Center source -> $REPO"
$git = Get-Command git -ErrorAction SilentlyContinue
if(Test-Path (Join-Path $REPO '.git')){
  if($git){ & git -C $REPO pull --ff-only -q; Say "    updated (git pull)" }
} elseif($git){
  & git clone -q $REPO_URL $REPO; Say "    cloned from GitHub"
} else {
  $zip = Join-Path $env:TEMP 'tcc.zip'
  Invoke-WebRequest 'https://github.com/AdeelSiddiqui38/trading-command-center/archive/refs/heads/main.zip' -OutFile $zip -UseBasicParsing
  Expand-Archive $zip -DestinationPath $env:TEMP -Force
  Move-Item (Join-Path $env:TEMP 'trading-command-center-main') $REPO -Force
  Say "    downloaded (git not found - install Git to enable auto-sync)"
}

# ---------- 5. hub ----------
Say "`n[5] Installing hub -> $HUB"
foreach($f in 'hub.py','Launch-Trading.vbs'){
  $s = Join-Path $REPO "brain-hub\$f"
  if(Test-Path $s){ Copy-Item $s (Join-Path $HUB $f) -Force; Say "    $f" } else { Say "    !! missing $s" }
}
$cfg = Join-Path $HUB 'hub_config.json'
if(-not (Test-Path $cfg)){
  @{
    port = 7777
    app_url = 'https://adeelsiddiqui38.github.io/trading-command-center/index.html'
    chrome_app_id = 'pgmmaaemnofafbhjagpfmmaegijlibbb'
    bot_dir = (Join-Path $BRAIN 'trading-paperbot')
    vibe_dir = (Join-Path $BRAIN 'Vibe-Trading')
    signal_agent_dir = (Join-Path $BRAIN 'trading-signal-agent')
    equity_watchlist = 'AAPL,MSFT,NVDA,GOOGL'
    equity_interval_minutes = 30
    autostart = @{ vibe = $true; telegram = $true; paperbot = $true; leverage = $true; signal_agent = $true }
  } | ConvertTo-Json -Depth 3 | Set-Content $cfg -Encoding utf8
  Say "    hub_config.json written (edit watchlist / autostart here)"
}

# ---------- 6. one Desktop icon ----------
Say "`n[6] Desktop icon"
$desk = [Environment]::GetFolderPath('Desktop')
$sh = New-Object -ComObject WScript.Shell
$appLnk = Join-Path $desk "Adeel's Trading Command Center.lnk"
$icon = $null
if(Test-Path $appLnk){
  $old = $sh.CreateShortcut($appLnk)
  if($old.TargetPath -notmatch 'wscript'){ $icon = $old.IconLocation; Copy-Item $appLnk (Join-Path $ARCH 'old-shortcuts') -Force }
  else { $icon = $old.IconLocation }
}
foreach($n in 'Start All Trading Bots.lnk','Start Paper Trading Bot.lnk','Start Trading Command Center.lnk'){
  $p = Join-Path $desk $n
  if(Test-Path $p){ Move-Item $p (Join-Path $ARCH 'old-shortcuts') -Force; Say "    archived old shortcut: $n" }
}
$lnk = $sh.CreateShortcut($appLnk)
$lnk.TargetPath = "$env:WINDIR\System32\wscript.exe"
$lnk.Arguments = '"' + (Join-Path $HUB 'Launch-Trading.vbs') + '"'
$lnk.WorkingDirectory = $HUB
$lnk.Description = 'Starts the trading bots in the background and opens the Command Center'
if($icon -and $icon.Trim(',0 ').Length -gt 0){ $lnk.IconLocation = $icon } else { $lnk.IconLocation = "$env:ProgramFiles\Google\Chrome\Application\chrome.exe,0" }
$lnk.Save()
Say "    Desktop: Adeel's Trading Command Center  (one click = bots + app)"

# ---------- 7. go ----------
Say "`n[7] Starting hub..."
Start-Process "$env:WINDIR\System32\wscript.exe" -ArgumentList ('"' + (Join-Path $HUB 'Launch-Trading.vbs') + '"')
Start-Sleep -Seconds 12
try { $st = Invoke-RestMethod 'http://127.0.0.1:7777/api/state' -TimeoutSec 10
      foreach($s in $st.services){ Say ("    {0,-40} {1}" -f $s.label, $s.status) } }
catch { Say "    hub not answering yet - check $HUB\logs\hub.log" }
Say "`nDONE. Everything lives in $BRAIN"

' Adeel's Trading Command Center - one-click launcher (Windows)
' Starts the BRAIN trading hub hidden (it runs all bots in the background,
' one copy each) and opens the Command Center app. If the hub is already
' running it just opens the app. No console windows.
Option Explicit
Dim sh, fso, hubDir, repoDir, pyw, cmd
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
hubDir = fso.GetParentFolderName(WScript.ScriptFullName)
repoDir = fso.BuildPath(fso.GetParentFolderName(hubDir), "trading-command-center")

' Quietly pick up the latest hub from GitHub (keeps Mac + Windows in sync). Never blocks long.
If fso.FolderExists(fso.BuildPath(repoDir, ".git")) Then
  cmd = "powershell -NoProfile -ExecutionPolicy Bypass -Command ""$ErrorActionPreference='SilentlyContinue'; " & _
        "$j = Start-Job { git -C '" & repoDir & "' pull --ff-only -q }; Wait-Job $j -Timeout 15 | Out-Null; " & _
        "foreach($f in 'hub.py','prices.py','Launch-Trading.vbs'){ $src = Join-Path '" & repoDir & "' ('brain-hub\' + $f); if (Test-Path $src) { Copy-Item $src (Join-Path '" & hubDir & "' $f) -Force } }"""
  sh.Run cmd, 0, True
End If

' Start the hub with pythonw (no console at all). If it's already running, the
' hub notices and exits on its own. Then open the Command Center app.
Dim pyw, logf, proxy, appId
pyw = "pythonw"
If fso.FileExists("C:\Python314\pythonw.exe") Then pyw = "C:\Python314\pythonw.exe"
If Not fso.FolderExists(fso.BuildPath(hubDir, "logs")) Then fso.CreateFolder fso.BuildPath(hubDir, "logs")
Set logf = fso.OpenTextFile(fso.BuildPath(hubDir, "logs\launch.log"), 8, True)
logf.WriteLine Now & "  launch: " & pyw & " hub.py"
logf.Close
sh.CurrentDirectory = hubDir
sh.Run """" & pyw & """ """ & fso.BuildPath(hubDir, "hub.py") & """", 0, False

WScript.Sleep 2500
appId = "pgmmaaemnofafbhjagpfmmaegijlibbb"
proxy = sh.ExpandEnvironmentStrings("%ProgramFiles%") & "\Google\Chrome\Application\chrome_proxy.exe"
If fso.FileExists(proxy) Then
  sh.Run """" & proxy & """ --profile-directory=Default --app-id=" & appId, 1, False
Else
  sh.Run "https://adeelsiddiqui38.github.io/trading-command-center/index.html", 1, False
End If

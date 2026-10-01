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
        "foreach($f in 'hub.py','Launch-Trading.vbs'){ $src = Join-Path '" & repoDir & "' ('brain-hub\' + $f); if (Test-Path $src) { Copy-Item $src (Join-Path '" & hubDir & "' $f) -Force } }"""
  sh.Run cmd, 0, True
End If

' Hidden python.exe (window style 0) rather than pythonw: same invisibility, but the
' hub keeps a real (hidden) console so nothing it runs can fail silently.
Dim py, logf
py = "python"
If fso.FileExists("C:\Python314\python.exe") Then py = "C:\Python314\python.exe"
If Not fso.FolderExists(fso.BuildPath(hubDir, "logs")) Then fso.CreateFolder fso.BuildPath(hubDir, "logs")
Set logf = fso.OpenTextFile(fso.BuildPath(hubDir, "logs\launch.log"), 8, True)
logf.WriteLine Now & "  launch: " & py & " hub.py"
logf.Close
sh.CurrentDirectory = hubDir
sh.Run """" & py & """ """ & fso.BuildPath(hubDir, "hub.py") & """", 0, False

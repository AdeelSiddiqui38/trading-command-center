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
        "$src = Join-Path '" & repoDir & "' 'brain-hub\hub.py'; if (Test-Path $src) { Copy-Item $src '" & hubDir & "\hub.py' -Force }"""
  sh.Run cmd, 0, True
End If

pyw = "pythonw"
If fso.FileExists("C:\Python314\pythonw.exe") Then pyw = "C:\Python314\pythonw.exe"
sh.CurrentDirectory = hubDir
sh.Run """" & pyw & """ """ & fso.BuildPath(hubDir, "hub.py") & """", 0, False

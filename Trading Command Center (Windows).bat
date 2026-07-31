@echo off
REM ============================================================
REM  Adeel's Trading Command Center - Windows launcher
REM  Serves the folder locally so the PWA can install + cache,
REM  then opens it in your default browser. Falls back to
REM  opening index.html directly if Python isn't installed.
REM ============================================================
setlocal
cd /d "%~dp0"
set PORT=8765

where python >nul 2>nul
if %ERRORLEVEL%==0 (
    echo Starting local server on http://localhost:%PORT% ...
    start "" http://localhost:%PORT%/index.html
    python -m http.server %PORT%
    goto :eof
)

where py >nul 2>nul
if %ERRORLEVEL%==0 (
    echo Starting local server on http://localhost:%PORT% ...
    start "" http://localhost:%PORT%/index.html
    py -m http.server %PORT%
    goto :eof
)

echo Python not found - opening index.html directly.
echo (Install Python from https://python.org to enable the installable PWA + offline mode.)
start "" "%~dp0index.html"
endlocal

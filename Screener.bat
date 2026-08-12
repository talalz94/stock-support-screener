@echo off
REM One click: start the local server if it is not already up, then open the hub.
REM
REM The server is NOT a permanent daemon -- `--idle-exit 30` makes it shut itself
REM down after 30 minutes with no requests. A static file:// page cannot start a
REM process (browsers forbid it, and it is circular anyway), so this shortcut is
REM the bootstrap. Once it is running, the dashboard's run buttons work and any
REM ticker's profile builds on demand.
setlocal
cd /d "%~dp0"
set URL=http://127.0.0.1:8787/index.html

powershell -NoProfile -Command "if((Test-NetConnection -ComputerName 127.0.0.1 -Port 8787 -InformationLevel Quiet -WarningAction SilentlyContinue)){exit 0}else{exit 1}"
if %ERRORLEVEL%==0 (
  echo Server already running.
) else (
  echo Starting screener server...
  start "" /min pythonw serve.py --port 8787 --idle-exit 30
  timeout /t 3 /nobreak >nul
)
start "" "%URL%"
endlocal

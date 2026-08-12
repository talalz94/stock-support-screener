@echo off
REM Double-click this to open the screener properly.
REM
REM WHY THIS FILE EXISTS: opening reports\index.html straight off the disk gives
REM you a file:// page, and a file:// page cannot ask anything to build. The
REM search box still finds all 3,479 tickers, but clicking one does nothing --
REM which reads exactly like a broken button. Served over http instead, every
REM ticker is one click and renders on demand.
REM
REM Safe to double-click repeatedly: if a server is already running this just
REM opens the browser at it rather than starting a second one. It also shuts
REM itself down after 4 idle hours, so a forgotten window does not sit resident.
REM To stop it now: "Stop Screener.bat", or Ctrl-C in this window.
cd /d "%~dp0"
echo Starting the screener...  (close this window or press Ctrl-C to stop)
python serve.py --open
pause

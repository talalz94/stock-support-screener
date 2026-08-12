@echo off
REM Double-click to stop the screener server cleanly.
REM
REM Politely first, so a page mid-build finishes writing -- a hard kill can
REM leave a truncated HTML file, and a half-written page renders as a broken
REM one rather than an absent one.
cd /d "%~dp0"
python serve.py --stop
timeout /t 3 >nul

@echo off
REM Manual launcher for the weekly newsletter pipeline.
REM Double-click to run on demand; the scheduled task runs this same command.
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
python run_weekly.py
echo.
echo Exit code: %ERRORLEVEL%
pause

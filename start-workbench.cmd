@echo off
cd /d "%~dp0"
set NO_PROXY=*
if not exist .venv\Scripts\python.exe (
  py -3 -m venv .venv
  if errorlevel 1 goto failed
)
.venv\Scripts\python.exe -c "import flask, waitress" >nul 2>&1
if errorlevel 1 (
  .venv\Scripts\python.exe -m pip install --index-url https://pypi.org/simple -e .
  if errorlevel 1 goto failed
)
.venv\Scripts\python.exe -c "import json, urllib.request; data=json.load(urllib.request.urlopen('http://127.0.0.1:18765/api/jobs', timeout=2)); assert 'items' in data and 'quotas' in data" >nul 2>&1
if not errorlevel 1 (
  start "" "http://127.0.0.1:18765"
  exit /b 0
)
.venv\Scripts\python.exe -m job_agent.web
if errorlevel 1 goto failed
exit /b 0
:failed
echo Workbench could not start. See the message above.
pause
exit /b 1

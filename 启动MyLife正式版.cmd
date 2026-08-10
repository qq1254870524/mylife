@echo off
setlocal
cd /d "%~dp0"
set "PYTHONDONTWRITEBYTECODE=1"

where pythonw.exe >nul 2>&1
if errorlevel 1 goto :error
start "" pythonw.exe -B "%~dp0main.py"
if errorlevel 1 goto :error
endlocal
exit /b 0

:error
set "MYLIFE_EXIT_CODE=%errorlevel%"
echo.
echo MyLife GUI start failed. Error code: %MYLIFE_EXIT_CODE%
pause
endlocal & exit /b %MYLIFE_EXIT_CODE%

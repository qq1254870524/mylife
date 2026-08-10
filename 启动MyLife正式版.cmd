@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONDONTWRITEBYTECODE=1"

python -B bootstrap.py
if errorlevel 1 goto :error
python -B main.py
if errorlevel 1 goto :error
endlocal
exit /b 0

:error
set "MYLIFE_EXIT_CODE=%errorlevel%"
echo.
echo MyLife GUI start failed. Error code: %MYLIFE_EXIT_CODE%
pause
endlocal & exit /b %MYLIFE_EXIT_CODE%

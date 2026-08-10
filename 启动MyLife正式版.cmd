@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONDONTWRITEBYTECODE=1
python -B bootstrap.py
if errorlevel 1 goto :error
python -B main.py
if errorlevel 1 goto :error
exit /b 0

:error
echo.
echo MyLife GUI 启动失败，错误码：%errorlevel%
pause
exit /b %errorlevel%

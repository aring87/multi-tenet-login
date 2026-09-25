@echo off
where pyw.exe >nul 2>nul
if %errorlevel% equ 0 (
  start "" pyw.exe -3 "%~dp0desktop_app.py"
  exit /b 0
)
where pythonw.exe >nul 2>nul
if %errorlevel% equ 0 (
  start "" pythonw.exe "%~dp0desktop_app.py"
  exit /b 0
)
echo Install Python 3.10 or newer with Tkinter, then reopen this launcher.
pause

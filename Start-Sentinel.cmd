@echo off
setlocal
pushd "%~dp0" || exit /b 1
where pyw.exe >nul 2>nul
if %errorlevel% equ 0 (
  start "" pyw.exe -3 -m sentinel_app
  popd
  exit /b 0
)
where pythonw.exe >nul 2>nul
if %errorlevel% equ 0 (
  start "" pythonw.exe -m sentinel_app
  popd
  exit /b 0
)
popd
echo Install Python 3.10 or newer with Tkinter, then reopen this launcher.
pause

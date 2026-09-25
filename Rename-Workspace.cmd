@echo off
setlocal
where py >nul 2>nul
if not errorlevel 1 (
    py -3 "%~dp0rename_workspace.py"
) else if exist "%LocalAppData%\Programs\Python\Python310\python.exe" (
    "%LocalAppData%\Programs\Python\Python310\python.exe" "%~dp0rename_workspace.py"
) else (
    echo Python 3.10 or newer is required.
    pause
    exit /b 1
)
if errorlevel 1 pause

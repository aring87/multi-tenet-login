@echo off
rem Builds dist\Sentinel Workspace.exe. Azure CLI and GitHub CLI are still needed on each PC.
setlocal
pushd "%~dp0" || exit /b 1
py -m pip install --upgrade pyinstaller -r requirements.txt || goto failed
py -m PyInstaller --noconfirm --clean --onefile --windowed --name "Sentinel Workspace" ^
  --paths . --collect-submodules sentinel_app ^
  --add-data "docs;docs" --add-data "templates;templates" ^
  scripts\sentinel_exe.py || goto failed
echo.
echo Built: %CD%\dist\Sentinel Workspace.exe
popd
exit /b 0
:failed
popd
echo Build failed.
exit /b 1

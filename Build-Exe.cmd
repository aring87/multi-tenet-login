@echo off
rem Builds dist\Sentinel Workspace.exe. Azure CLI and GitHub CLI are still needed on each PC.
setlocal
pushd "%~dp0" || exit /b 1
py -m pip install --upgrade pyinstaller -r requirements.txt || goto failed
rem The icon and the Windows version resource are drawn from the app's own brand module and version.
rem LICENSE is bundled for About > Open license; UPX is off because packed exes trip antivirus heuristics.
py scripts\make_build_assets.py || goto failed
py -m PyInstaller --noconfirm --clean --onefile --windowed --name "Sentinel Workspace" ^
  --icon assets\sentinel-workspace.ico --version-file assets\version-info.txt ^
  --paths . --collect-submodules sentinel_app ^
  --add-data "docs;docs" --add-data "templates;templates" --add-data "LICENSE;." --noupx ^
  scripts\sentinel_exe.py || goto failed
echo.
echo Built: %CD%\dist\Sentinel Workspace.exe
popd
exit /b 0
:failed
popd
echo Build failed.
exit /b 1

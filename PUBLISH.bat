@echo off
rem ============================================================
rem  UniversalAgent v2.2.0 - AUTONOMOUS GitHub publisher.
rem  ZERO questions asked:
rem    1) token is auto-read from any *.txt near this file;
rem    2) your login is auto-fetched from GitHub API;
rem    3) the repository is auto-created via GitHub API;
rem    4) the push happens by itself, token never lands
rem       in URL, .git/config or console output.
rem  USAGE: put your token .txt next to this file and double-click.
rem ============================================================
setlocal
set "SCRIPT_DIR=%~dp0"

if not exist "%SCRIPT_DIR%publish.ps1" (
  echo [!] publish.ps1 not found near PUBLISH.bat.
  echo     UNPACK the whole archive into a folder, then run PUBLISH.bat again.
  echo.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%publish.ps1"
echo.
pause
endlocal

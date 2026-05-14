:; # ===== Cross-platform hook runner =====
:; # This file is both a valid .cmd (Windows) and bash (Unix) script.
:; # On Windows: runs as .cmd, finds bash via Git or WSL
:; # On Unix: runs directly as bash
:; exec bash "$0" "$@" 2>/dev/null
:; exit

@echo off
if "%~1"=="" (
    echo run-hook.cmd: missing script name >&2
    exit /b 1
)

set "HOOK_DIR=%~dp0"

REM Try Git for Windows bash
if exist "C:\Program Files\Git\bin\bash.exe" (
    "C:\Program Files\Git\bin\bash.exe" "%HOOK_DIR%%~1" %2 %3 %4 %5 %6 %7 %8 %9
    exit /b %ERRORLEVEL%
)
if exist "C:\Program Files (x86)\Git\bin\bash.exe" (
    "C:\Program Files (x86)\Git\bin\bash.exe" "%HOOK_DIR%%~1" %2 %3 %4 %5 %6 %7 %8 %9
    exit /b %ERRORLEVEL%
)

REM Try WSL
where wsl >nul 2>nul
if %ERRORLEVEL% equ 0 (
    wsl bash "%HOOK_DIR%%~1" %2 %3 %4 %5 %6 %7 %8 %9
    exit /b %ERRORLEVEL%
)

REM No bash found — exit silently
exit /b 0

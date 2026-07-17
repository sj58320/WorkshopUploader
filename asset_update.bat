@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

if "%~1"=="" (
    set /p "ADDON_ID=Workshop Addon ID: "
    if not defined ADDON_ID (
        echo [ERROR] Addon ID is required.
        set "RESULT=1"
        goto :done
    )
    python asset_upload.py "!ADDON_ID!"
) else (
    python asset_upload.py %*
)
set "RESULT=!ERRORLEVEL!"

:done
pause
exit /b %RESULT%

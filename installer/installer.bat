@echo off
setlocal enabledelayedexpansion

:: Duet Voice - build script
::
:: Freezes the app into a single .exe with PyInstaller, stages it into
:: this installer\ folder, then runs makensis to produce
:: DuetVoiceSetup.exe. Run this script from anywhere - paths below are
:: resolved relative to this .bat file's own location, not the current
:: directory.
::
:: Usage (from a normal, non-elevated shell - no admin needed):
::     installer\installer.bat
::
:: Requires NSIS's makensis.exe on PATH - https://nsis.sourceforge.io/Download

set "INSTALLER_DIR=%~dp0"
set "ROOT=%INSTALLER_DIR%.."

:: Prefer the project's own .venv (where requirements.txt / pyinstaller
:: actually get installed) over whatever "pyinstaller" resolves to on
:: PATH - this script isn't run with the venv activated, so a bare
:: `pyinstaller` call would otherwise silently fail with "not recognized"
:: even when it's installed, just not globally.
set "PYINSTALLER=pyinstaller"
if exist "%ROOT%\.venv\Scripts\pyinstaller.exe" (
    set "PYINSTALLER=%ROOT%\.venv\Scripts\pyinstaller.exe"
)

echo === [1/4] Building DuetVoice.exe with PyInstaller ===
pushd "%ROOT%"
"%PYINSTALLER%" --noconfirm "%INSTALLER_DIR%DuetVoice.spec"
set "BUILD_ERR=%ERRORLEVEL%"
popd
if not "%BUILD_ERR%"=="0" (
    echo ERROR: PyInstaller failed - see output above.
    echo ERROR: Make sure PyInstaller is installed - either in .venv
    echo ERROR: ^(.venv\Scripts\pip install pyinstaller^) or on PATH.
    exit /b 1
)

if not exist "%ROOT%\dist\DuetVoice.exe" (
    echo ERROR: "%ROOT%\dist\DuetVoice.exe" was not produced.
    exit /b 1
)

echo === [2/4] Copying DuetVoice.exe into installer\ ===
copy /y "%ROOT%\dist\DuetVoice.exe" "%INSTALLER_DIR%DuetVoice.exe" >nul
if not "%ERRORLEVEL%"=="0" (
    echo ERROR: failed to copy DuetVoice.exe into installer\.
    exit /b 1
)

echo === [3/4] Cleaning up dist\ ===
rmdir /s /q "%ROOT%\dist"

where makensis >nul 2>nul
if not "%ERRORLEVEL%"=="0" (
    echo ERROR: makensis.exe not found on PATH - install NSIS from
    echo ERROR: https://nsis.sourceforge.io/Download and try again.
    echo ERROR: installer\DuetVoice.exe was still built successfully.
    exit /b 1
)

echo === [4/4] Building DuetVoiceSetup.exe with NSIS ===
pushd "%INSTALLER_DIR%"
makensis installer.nsi
set "NSIS_ERR=%ERRORLEVEL%"
popd
if not "%NSIS_ERR%"=="0" (
    echo ERROR: makensis failed - see output above.
    exit /b 1
)

echo.
echo Done. installer\ now contains:
echo   DuetVoice.exe            (frozen app)
echo   DuetVoiceSetup.exe       (installer - send this one)
endlocal

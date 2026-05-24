@echo off
setlocal
cd /d "%~dp0"

REM Build MultiRobloxManager as a folder distribution (fast startup).
REM Output: dist\MultiRobloxManager\MultiRobloxManager.exe
REM
REM --onedir is intentional: --onefile bundles the whole runtime into
REM one .exe that has to extract ~100 MB of Qt/Pillow/discord.py to
REM %TEMP% on every launch, which is why the previous build was slow
REM to start. With --onedir the DLLs sit beside the .exe and Windows
REM loads them straight from disk — startup is roughly 1-2 seconds
REM instead of 7-13.
REM
REM To share the app: zip the dist\MultiRobloxManager\ folder.
REM Recipients extract it anywhere and double-click the .exe inside.

if not exist .venv (
    echo Creating virtual environment...
    py -m venv .venv || goto :fail
)

echo Installing dependencies...
.\.venv\Scripts\python.exe -m pip install --upgrade pip || goto :fail
.\.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller || goto :fail

echo Building MultiRobloxManager.exe (onedir, fast startup)...
.\.venv\Scripts\python.exe -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onedir ^
    --windowed ^
    --name MultiRobloxManager ^
    --collect-all PySide6 ^
    --collect-submodules shiboken6 ^
    --collect-all pywebview ^
    --collect-all psutil ^
    --collect-all PIL ^
    --collect-all discord ^
    --hidden-import socks ^
    --hidden-import urllib3.contrib.socks ^
    --exclude-module PyQt5 ^
    --exclude-module PyQt6 ^
    --noupx ^
    main.py || goto :fail

if not exist dist\MultiRobloxManager\MultiRobloxManager.exe goto :fail

echo.
echo ===========================================================
echo  Build complete.
echo  Executable: dist\MultiRobloxManager\MultiRobloxManager.exe
echo  Distribute the entire dist\MultiRobloxManager\ folder.
echo ===========================================================
exit /b 0

:fail
echo.
echo Build failed. See output above.
exit /b 1

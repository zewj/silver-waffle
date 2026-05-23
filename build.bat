@echo off
setlocal
cd /d "%~dp0"

REM Build a standalone MultiRobloxManager.exe via PyInstaller.
REM Run this from a Windows machine; the result lands in dist\.

if not exist .venv (
    echo Creating virtual environment...
    py -m venv .venv || goto :fail
)

echo Installing dependencies...
.\.venv\Scripts\python.exe -m pip install --upgrade pip || goto :fail
.\.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller || goto :fail

echo Building MultiRobloxManager.exe...
.\.venv\Scripts\python.exe -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onefile ^
    --windowed ^
    --name MultiRobloxManager ^
    --collect-all pywebview ^
    --collect-all psutil ^
    --collect-all sv_ttk ^
    main.py || goto :fail

if not exist dist\MultiRobloxManager.exe goto :fail

echo.
echo ===========================================================
echo  Build complete: dist\MultiRobloxManager.exe
echo ===========================================================
exit /b 0

:fail
echo.
echo Build failed. See output above.
exit /b 1

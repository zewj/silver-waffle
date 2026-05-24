@echo off
setlocal
cd /d "%~dp0"

REM Build MultiRobloxManager as a self-contained single-file Windows .exe.
REM Requires the .NET 8 SDK installed (winget install Microsoft.DotNet.SDK.8).
REM
REM Output: dist\MultiRobloxManager.exe
REM
REM Self-contained means the .NET runtime is bundled — recipient doesn't
REM need .NET installed. Single-file publishes everything into one .exe.
REM Trimming is OFF because WPF + reflection + Json source generation is
REM still tricky to trim safely; the resulting .exe is ~80 MB but starts
REM in ~300 ms (vs. the Python --onedir build's ~1-2 s).

where dotnet >nul 2>&1
if errorlevel 1 (
    echo .NET SDK not found. Install with: winget install Microsoft.DotNet.SDK.8
    exit /b 1
)

dotnet publish MultiRobloxManager\MultiRobloxManager.csproj ^
    -c Release ^
    -r win-x64 ^
    --self-contained true ^
    -p:PublishSingleFile=true ^
    -p:IncludeNativeLibrariesForSelfExtract=true ^
    -p:EnableCompressionInSingleFile=false ^
    -p:DebugType=embedded ^
    -o dist || exit /b 1

if not exist dist\MultiRobloxManager.exe (
    echo Build failed: dist\MultiRobloxManager.exe not produced.
    exit /b 1
)

echo.
echo ===========================================================
echo  Build complete.
echo  Executable: dist\MultiRobloxManager.exe
echo ===========================================================
exit /b 0

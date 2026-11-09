@echo off
rem ===========================================================================
rem  PLC Bridge — build the Windows .exe
rem
rem    build_exe.bat            folder build  -> dist\PLC_Bridge\   (recommended)
rem    build_exe.bat onefile    single files  -> dist\onefile\
rem
rem  Needed on the BUILD machine only (the target machine needs nothing):
rem    pip install pyinstaller websockets aiohttp aiohttp-cors
rem ===========================================================================
setlocal
cd /d "%~dp0"

python -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo [ERROR] PyInstaller is not installed.
    echo         pip install pyinstaller websockets aiohttp aiohttp-cors
    exit /b 1
)

if /i "%~1"=="onefile" (
    set PLC_BRIDGE_ONEFILE=1
    python -m PyInstaller --noconfirm --clean --distpath dist\onefile plc_bridge.spec
    echo.
    echo Done: dist\onefile\PLC_Bridge.exe  +  PLC_Bridge_CLI.exe
) else (
    set PLC_BRIDGE_ONEFILE=
    python -m PyInstaller --noconfirm --clean plc_bridge.spec
    echo.
    echo Done: dist\PLC_Bridge\  ^(copy the whole folder to the target machine^)
)
endlocal

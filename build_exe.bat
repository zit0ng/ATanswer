@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================
echo ATanswer - 打包为独立 exe（可选）
echo ============================================
echo 需要网络安装 PyInstaller；若离线可跳过此步骤，直接用 启动.bat 运行。
echo.

python --version >nul 2>nul
if errorlevel 1 (
    echo [ERROR] 未找到 python 命令，请先安装 Python 3.10+ 并加入 PATH。
    pause
    exit /b 1
)

echo [1/2] 安装 / 升级 PyInstaller...
python -m pip install --upgrade pyinstaller
if errorlevel 1 goto :error

echo [2/2] 开始打包...
python -m PyInstaller --noconfirm --clean --windowed ^
    --name "ATanswer" ^
    --icon "assets\app.ico" ^
    --add-data "assets;assets" ^
    --collect-submodules app ^
    main.py
if errorlevel 1 goto :error

echo.
echo 打包完成：dist\ATanswer\ATanswer.exe
pause
exit /b 0

:error
echo.
echo 打包失败，请检查上方错误信息。
pause
exit /b 1

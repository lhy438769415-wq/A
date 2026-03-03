@echo off
chcp 65001 >nul
echo ===================================================
echo   Brooks-AI 环境安装与启动脚本
echo ===================================================

REM 1. 检查 Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未检测到 Python！请安装 Python 3.10+ 并添加到系统 PATH 环境变量。
    pause
    exit /b
)

echo [信息] 检测到 Python:
python --version
echo.

REM 2. 安装依赖
echo [信息] 正在安装 requirements.txt 中的依赖...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [错误] 依赖安装失败。
    pause
    exit /b
)

echo.
echo [信息] 依赖安装成功。
echo.

REM 3. 启动选项
set /p "RUN_APP=是否立即运行控制台？(y/n): "
if /i "%RUN_APP%"=="y" (
    echo [信息] 正在启动 GUI 控制台...
    python gui_dashboard.py
) else (
    echo [信息] 安装完成。您可以手动运行 'python gui_dashboard.py'。
)

pause

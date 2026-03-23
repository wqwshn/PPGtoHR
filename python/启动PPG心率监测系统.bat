@echo off
chcp 65001 >nul
echo ====================================
echo PPG心率监测系统启动器
echo ====================================
echo.

REM 激活conda环境
echo [1/2] 正在激活conda环境 ppg_prj...
call conda activate ppg_prj
if errorlevel 1 (
    echo 错误: 无法激活conda环境 'ppg_prj'
    echo 请确认:
    echo 1. conda已正确安装
    echo 2. 环境名称 'ppg_prj' 存在
    echo 3. 如果环境名称不同，请编辑此文件修改环境名
    pause
    exit /b 1
)

REM 进入Python脚本目录
cd /d "%~dp0"
echo [2/2] 正在启动PPG心率监测系统...
echo.

REM 运行主程序
python getdata.py

REM 程序结束后暂停
echo.
echo ====================================
echo 程序已退出
echo ====================================
pause

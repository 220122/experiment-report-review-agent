@echo off
chcp 936 >nul
title 实验报告批阅-启动器
cd /d %~dp0
set "PYEXE="

echo ============================================================
echo   实验报告批阅多智能体系统 - 启动
echo ============================================================
echo.

REM ---------- 找到 Python（优先用安装时缓存的解释器） ----------
if exist ".run_python.txt" set /p PYEXE=<".run_python.txt"
if not defined PYEXE if exist ".venv\Scripts\python.exe" set "PYEXE=%~dp0.venv\Scripts\python.exe"
if not defined PYEXE if exist "E:\Anaconda\python.exe" set "PYEXE=E:\Anaconda\python.exe"
if not defined PYEXE if exist "D:\Anaconda\python.exe" set "PYEXE=D:\Anaconda\python.exe"
if not defined PYEXE if exist "C:\Anaconda\python.exe" set "PYEXE=C:\Anaconda\python.exe"
if not defined PYEXE if exist "C:\ProgramData\Anaconda3\python.exe" set "PYEXE=C:\ProgramData\Anaconda3\python.exe"
if not defined PYEXE for /f "delims=" %%i in ('where python 2^>nul') do if not defined PYEXE set "PYEXE=%%i"
if not defined PYEXE for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do if not defined PYEXE set "PYEXE=%%i"

if not defined PYEXE (
    echo   [X] 没有找到 Python，请先双击「① 首次安装（双击我）.bat」
    echo.
    pause
    exit /b 1
)

REM ---------- 未安装依赖时给出明确指引 ----------
"%PYEXE%" -c "import streamlit" >nul 2>&1
if errorlevel 1 (
    echo   检测到尚未安装运行环境，请先双击「① 首次安装（双击我）.bat」
    echo   （若你确定已安装，请检查是否换了 Python 解释器）
    echo.
    pause
    exit /b 1
)

REM ---------- 启动服务（窗口标题：「③ 停止项目」脚本靠它定位进程） ----------
echo   使用解释器: %PYEXE%
echo   正在启动服务，请稍候，服务就绪后会自动打开浏览器 ...
echo.
title 实验报告批阅-服务
"%PYEXE%" launcher.py "实验报告批阅" 8501

echo.
echo   服务已停止。
timeout /t 3 /nobreak >nul
exit

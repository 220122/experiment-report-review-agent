@echo off
chcp 936 >nul
title 实验报告批阅-首次安装
cd /d %~dp0
set "PYEXE="

echo ============================================================
echo   实验报告批阅多智能体系统 - 首次环境安装
echo   全程自动，使用国内镜像（清华 / 阿里），请耐心等待 1-5 分钟
echo ============================================================
echo.

REM ==================== [1/5] 寻找 Python ====================
echo [1/5] 检查本机 Python 环境 ...
if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python39\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python39\python.exe"
if not defined PYEXE if exist "E:\Anaconda\python.exe" set "PYEXE=E:\Anaconda\python.exe"
if not defined PYEXE if exist "D:\Anaconda\python.exe" set "PYEXE=D:\Anaconda\python.exe"
if not defined PYEXE if exist "C:\Anaconda\python.exe" set "PYEXE=C:\Anaconda\python.exe"
if not defined PYEXE if exist "C:\ProgramData\Anaconda3\python.exe" set "PYEXE=C:\ProgramData\Anaconda3\python.exe"
if not defined PYEXE if exist "%USERPROFILE%\anaconda3\python.exe" set "PYEXE=%USERPROFILE%\anaconda3\python.exe"
if not defined PYEXE if exist "%USERPROFILE%\miniconda3\python.exe" set "PYEXE=%USERPROFILE%\miniconda3\python.exe"
if not defined PYEXE for /f "delims=" %%i in ('where python 2^>nul') do if not defined PYEXE set "PYEXE=%%i"
if not defined PYEXE for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do if not defined PYEXE set "PYEXE=%%i"

if not defined PYEXE (
    echo.
    echo   [X] 没有找到 Python。
    echo       请先安装 Python 3.9 及以上版本，推荐下面任一种：
    echo         1. Anaconda: https://www.anaconda.com/download
    echo         2. 官方安装包: https://www.python.org/downloads/
    echo            安装时务必勾选 "Add python.exe to PATH"
    echo       装好后重新双击本脚本。
    echo.
    pause
    exit /b 1
)
echo   [√] 使用解释器: %PYEXE%

REM ==================== [2/5] 检查 Streamlit 运行依赖 ====================
echo.
echo [2/5] 检查运行依赖 Streamlit ...
"%PYEXE%" -c "import streamlit" >nul 2>&1
if not errorlevel 1 (
    echo   [√] 当前环境已自带 Streamlit，无需安装
    goto :deps_ok
)

echo   [!] 缺少 Streamlit，开始安装（清华镜像）...
"%PYEXE%" -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple --timeout 60
"%PYEXE%" -m pip install streamlit -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn --timeout 60 --retries 3
"%PYEXE%" -c "import streamlit" >nul 2>&1
if errorlevel 1 (
    echo   [!] 清华镜像安装失败，改用阿里云镜像重试 ...
    "%PYEXE%" -m pip install streamlit -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com --timeout 60 --retries 3
)
"%PYEXE%" -c "import streamlit" >nul 2>&1
if errorlevel 1 (
    echo.
    echo   [X] Streamlit 安装失败。请把本窗口的报错信息截图反馈。
    echo       若你的电脑是受限环境，也可改用其它已装 Streamlit 的 Python 解释器。
    echo.
    pause
    exit /b 1
)

:deps_ok
echo   [√] Streamlit 就绪

REM ==================== [3/5] 可选依赖（有则更好，无则自动降级） ====================
echo.
echo [3/5] 安装可选依赖 openai / httpx（失败不影响运行）...
"%PYEXE%" -m pip install openai httpx -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn --timeout 60 --retries 2 >nul 2>&1
if errorlevel 1 (
    echo   [i] 可选依赖未装全，程序将自动使用 httpx/标准库引擎，功能完全一致
) else (
    echo   [√] 可选依赖已安装
)

REM ==================== [4/5] 配置 API Key ====================
echo.
echo [4/5] 检查 DeepSeek API Key 配置 ...
if not exist ".env" if exist ".env.example" copy ".env.example" ".env" >nul

findstr /c:"sk-请替换" ".env" >nul 2>&1
if not errorlevel 1 (
    echo.
    echo   还没有填写 API Key，接下来会打开记事本：
    echo     把 DEEPSEEK_API_KEY= 后面的内容改成你自己的 Key，
    echo     保存后关闭记事本即可。
    echo   Key 申请地址: https://platform.deepseek.com/api_keys
    echo   （也可以先跳过，启动后在网页左侧「临时 API Key」里填写）
    echo.
    pause
    start notepad ".env"
) else (
    echo   [√] .env 中已有 API Key
)

REM ==================== [5/5] 自检 ====================
echo.
echo [5/5] 环境自检 ...
"%PYEXE%" launcher.py --check
if errorlevel 1 (
    echo.
    echo   [X] 自检失败，请把上面的信息截图反馈。
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   安装完成！
echo   以后直接双击「② 启动项目（双击我）.bat」即可使用
echo ============================================================
echo.
pause

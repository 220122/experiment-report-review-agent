@echo off
chcp 936 >nul
title 实验报告批阅-停止器
cd /d %~dp0
set "PORT=8501"
if exist ".run_port.txt" set /p PORT=<".run_port.txt"

echo ============================================================
echo   停止实验报告批阅服务（端口 %PORT%）
echo ============================================================
echo.

echo   [1/2] 关闭服务窗口（若服务是从「② 启动项目」拉起的）...
taskkill /F /T /FI "WINDOWTITLE eq 实验报告批阅-服务*" >nul 2>&1

echo   [2/2] 清理端口 %PORT% 上的监听进程 ...
set "PIDS="
for /f "tokens=5" %%b in ('netstat -ano ^| findstr ":%PORT% " ^| findstr /i "LISTENING 侦听"') do call :addpid %%b

if not defined PIDS (
    echo       端口未被占用，无需清理
) else (
    for %%p in (%PIDS%) do (
        taskkill /F /T /PID %%p >nul 2>&1
        if errorlevel 1 (
            echo       结束 PID %%p 失败，请手动在任务管理器中结束该进程
        ) else (
            echo       已结束监听进程 PID %%p
        )
    )
)

echo.
echo   服务已全部关闭。
timeout /t 3 /nobreak >nul
exit

:addpid
if "%~1"=="0" exit /b 0
echo %PIDS% | findstr /c:" %~1 " >nul || set "PIDS=%PIDS% %~1 "
exit /b 0

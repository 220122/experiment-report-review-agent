@echo off
chcp 936 >nul
title 实验报告批阅-推送到GitHub
cd /d %~dp0

REM ============================================================
REM  >>> 仓库配置（已按你的账号填好，一般不需要改）<<<
REM      账号   : 220122
REM      仓库名 : experiment-report-review-agent
REM      请先在 GitHub 上创建【私人/Private】仓库，地址：
REM        https://github.com/new
REM        Repository name 填 experiment-report-review-agent
REM        Visibility 选 Private（私人）
REM        不要勾选 Add a README file / .gitignore / license
REM      仓库主页（创建后可见）：
REM        https://github.com/220122?tab=repositories
REM ============================================================
set "REMOTE=https://github.com/220122/experiment-report-review-agent.git"
set "BRANCH=main"

echo ============================================================
echo   将本项目推送到 GitHub（私人仓库）
echo   账号  : 220122
echo   远端  : %REMOTE%
echo ============================================================
echo.

REM ---------- 0) 检查 git ----------
where git >nul 2>&1
if errorlevel 1 (
    echo   [X] 没有找到 git。请先安装 Git for Windows:
    echo       https://git-scm.com/download/win
    echo.
    pause
    exit /b 1
)

REM ---------- 1) 初始化仓库 ----------
if not exist ".git" (
    echo   未检测到 Git 仓库，正在初始化 ...
    git init -b %BRANCH% >nul 2>&1
    if errorlevel 1 (
        git init >nul
        git branch -M %BRANCH% >nul 2>&1
    )
    echo   [√] 仓库已初始化（分支 %BRANCH%）
) else (
    echo   [√] 检测到已有 Git 仓库
)

REM ---------- 2) 确保有提交身份（没配过则只对当前仓库生效） ----------
git config user.name >nul 2>&1
if errorlevel 1 (
    git config user.name "220122"
    echo   [i] 已为本仓库设置提交用户名 220122
)
git config user.email >nul 2>&1
if errorlevel 1 (
    git config user.email "220122@users.noreply.github.com"
    echo   [i] 已为本仓库设置提交邮箱（可自行改成你的真实邮箱）
)

REM ---------- 3) 配置远端 ----------
git remote get-url origin >nul 2>&1
if errorlevel 1 (
    echo   正在添加远端 origin ...
    git remote add origin "%REMOTE%"
) else (
    echo   正在更新远端 origin ...
    git remote set-url origin "%REMOTE%"
)
echo   远端地址: 
git remote get-url origin
echo.

REM ---------- 4) 提交本地改动 ----------
git add -A
git diff --cached --quiet
if errorlevel 1 (
    echo   正在提交本地改动 ...
    git commit -m "更新：实验报告批阅多智能体系统 %DATE% %TIME%" >nul
    echo   [√] 已提交
    echo.
    echo   本次提交包含以下文件：
    git show --stat --oneline HEAD | more +1
) else (
    echo   [i] 没有新的改动需要提交
)
echo.

echo ------------------------------------------------------------
echo  关于登录（私人仓库必须认证，无法匿名推送）：
echo   1. 若已弹出浏览器，请按提示授权登录 GitHub 账号 220122
echo   2. 若提示输入 Username：填 220122
echo   3. 若提示输入 Password：必须粘贴 Personal Access Token
echo      （GitHub 已不支持用登录密码推送）
echo      Token 生成地址: https://github.com/settings/tokens
echo      勾选 repo 权限即可
echo   4. 本机凭据管理器里已有 220122 的记录，通常可直接推送成功
echo ------------------------------------------------------------
echo.
pause
echo.
echo   正在推送 ...
echo.

git push -u origin %BRANCH%
if errorlevel 1 (
    echo.
    echo ============================================================
    echo  推送失败，常见原因：
    echo   1. GitHub 上还没有这个仓库，或建成了公开仓库
    echo      请打开 https://github.com/new
    echo      仓库名填 experiment-report-review-agent
    echo      Visibility 选 Private（私人仓库）
    echo      不要勾选 Add a README file / .gitignore / license
    echo   2. 未登录 / Token 失效：重新推送并按提示登录 220122
    echo   3. 远端已有内容导致冲突：先执行 git pull --rebase origin %BRANCH%
    echo   4. 网络问题：可执行 git config --global http.proxy 配置代理后重试
    echo ============================================================
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  推送成功！
echo  私人仓库地址：https://github.com/220122/experiment-report-review-agent
echo  仓库列表    ：https://github.com/220122?tab=repositories
echo ============================================================
echo.
pause

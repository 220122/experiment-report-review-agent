# -*- coding: utf-8 -*-
"""
launcher.py —— 「② 启动项目（双击我）.bat」背后的引导器（自动生成，可安全删除后重建）

职责：
  1. 找到合适的 Python 解释器（缓存于 .run_python.txt）
  2. 生成 backend 用的 .env（如缺失，从 .env.example 复制）
  3. 在所有可用来源里搜索 DeepSeek API Key（环境变量 / .env / .streamlit/secrets.toml）
  4. 逐项检查依赖，缺失时静默用清华镜像补齐（装不上不阻塞，程序会自动降级）
  5. 等待 http://127.0.0.1:<port> 就绪，然后打开浏览器

用法（一般由 bat 调用，不需要手动执行）：
    python launcher.py <窗口标题前缀> <端口>
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
PY_CACHE = os.path.join(ROOT, ".run_python.txt")
VENV_DIR = os.path.join(ROOT, ".venv")
PIP_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"
PIP_ARGS = ["-i", PIP_INDEX, "--trusted-host", "pypi.tuna.tsinghua.edu.cn", "--timeout", "60", "--retries", "3"]

RUNTIME_DEPS = [
    ("streamlit", "streamlit>=1.30"),
    ("openai", "openai>=1.30"),
    ("httpx", "httpx>=0.24"),
]


def out(message: str = "") -> None:
    """输出一行（兼容中文控制台）。"""
    try:
        print(message, flush=True)
    except UnicodeEncodeError:
        print(message.encode("utf-8", "replace").decode("utf-8", "replace"), flush=True)


# ---------------------------------------------------------------------------
# 一、寻找 Python 解释器
# ---------------------------------------------------------------------------

def _has_streamlit(python: str) -> bool:
    """判断该解释器是否已经装了 streamlit。"""
    try:
        done = subprocess.run([python, "-c", "import streamlit"], capture_output=True, timeout=60)
        return done.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _is_python39(python: str) -> bool:
    """判断是否为 Python 3.9+ 的可用解释器。"""
    try:
        done = subprocess.run(
            [python, "-c", "import sys;raise SystemExit(0 if sys.version_info>=(3,9) else 1)"],
            capture_output=True, timeout=60,
        )
        return done.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def candidate_pythons() -> list:
    """收集本机可能存在的 Python，按"最可能可用"排序。"""
    found: list = []

    def add(path) -> None:
        if path and os.path.isfile(path) and path not in found:
            found.append(path)

    # 0) 之前安装时缓存下来的解释器
    if os.path.isfile(PY_CACHE):
        try:
            with open(PY_CACHE, "r", encoding="utf-8") as handle:
                add(handle.read().strip())
        except OSError:
            pass

    # 1) 项目自带的虚拟环境
    add(os.path.join(VENV_DIR, "Scripts", "python.exe"))

    # 2) PATH 里的 python / python3
    for name in ("python", "python3"):
        add(shutil.which(name))

    # 3) py 启动器能列出的所有版本
    py = shutil.which("py")
    if py:
        try:
            listed = subprocess.run([py, "-0p"], capture_output=True, text=True, timeout=30).stdout
            for line in listed.splitlines():
                match = re.search(r"([A-Za-z]:\\[^\s]+python\.exe)", line)
                if match:
                    add(match.group(1))
        except Exception:  # noqa: BLE001
            pass

    # 4) 常见安装位置
    for pattern in (
        r"E:\Anaconda\python.exe", r"D:\Anaconda\python.exe", r"C:\Anaconda\python.exe",
        r"C:\ProgramData\Anaconda3\python.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python313\python.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python312\python.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python311\python.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python310\python.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python39\python.exe"),
        os.path.expandvars(r"%USERPROFILE%\anaconda3\python.exe"),
        os.path.expandvars(r"%USERPROFILE%\miniconda3\python.exe"),
    ):
        add(pattern)

    return found


def resolve_python() -> str:
    """选出解释器：优先「已装 streamlit」的，其次任意 3.9+ 的。"""
    candidates = candidate_pythons()
    out("  正在检查本机 Python 环境……")
    for exe in candidates:
        if _has_streamlit(exe):
            out(f"  [√] 找到可用解释器（已自带 Streamlit）：{exe}")
            return exe
    for exe in candidates:
        if _is_python39(exe):
            out(f"  [!] 找到 Python 3.9+ 但缺少 Streamlit：{exe}（稍后会尝试安装）")
            return exe
    return ""


# ---------------------------------------------------------------------------
# 二、配置（.env 与 API Key）
# ---------------------------------------------------------------------------

def ensure_env_file() -> None:
    """确保存在 .env 配置文件。"""
    env_file = os.path.join(ROOT, ".env")
    example = os.path.join(ROOT, ".env.example")
    if not os.path.isfile(env_file) and os.path.isfile(example):
        try:
            shutil.copyfile(example, env_file)
            out("  已生成 .env 配置文件（可从「① 首次安装」脚本里填写 API Key）")
        except OSError:
            pass


def _key_from_file(path: str, pattern: str = "DEEPSEEK_API_KEY") -> str:
    """从 .env / secrets.toml 里读取 API Key。"""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            text = handle.read()
    except OSError:
        return ""
    match = re.search(rf"{pattern}\s*[=:]\s*[\"']?([^\s\"'#]+)", text)
    if not match:
        return ""
    value = match.group(1).strip()
    if not value or "请替换" in value or value.lower() in {"your_api_key", "sk-xxx"}:
        return ""
    return value


def find_api_key() -> str:
    """按优先级搜索 API Key：环境变量 > .env > .streamlit/secrets.toml。"""
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    for path in (
        os.path.join(ROOT, ".env"),
        os.path.join(ROOT, ".streamlit", "secrets.toml"),
    ):
        if os.path.isfile(path):
            key = _key_from_file(path)
            if key:
                return key
    return ""


# ---------------------------------------------------------------------------
# 三、依赖检查与安装（装不上不阻塞，程序会自动降级到 httpx/标准库引擎）
# ---------------------------------------------------------------------------

def ensure_deps(python: str) -> None:
    """检查必需依赖；缺失时用国内镜像尝试安装。"""
    try:
        done = subprocess.run([python, "-c", "import streamlit"], capture_output=True, timeout=60)
        if done.returncode == 0:
            out("  [√] 运行依赖已满足（streamlit 可用）")
        else:
            out("  [!] 缺少 streamlit，正在用清华镜像安装……")
            subprocess.run([python, "-m", "pip", "install", *PIP_ARGS, "streamlit>=1.30"],
                           timeout=1800)
    except Exception as exc:  # noqa: BLE001
        out(f"  [!] 依赖检查/安装出现问题：{type(exc).__name__}: {exc}")

    # 可选依赖：只为让日志更清爽，缺失也不影响运行
    try:
        done = subprocess.run(
            [python, "-c", "import httpx, openai"],
            capture_output=True, timeout=60,
        )
        if done.returncode != 0:
            out("  [i] 可选依赖（openai / httpx）不完整，将自动使用 httpx/标准库引擎，功能不受影响")
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# 四、启动与等待
# ---------------------------------------------------------------------------

def wait_and_open(port: int, timeout: float = 90.0) -> None:
    """轮询健康检查接口，服务就绪后打开浏览器。"""
    url = f"http://127.0.0.1:{port}"
    health = f"{url}/_stcore/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(health, timeout=3) as res:
                if res.status == 200:
                    out(f"  [√] 服务已就绪：{url}")
                    break
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(2)
    else:
        out(f"  [!] 等待超时，请手动在浏览器打开 {url}")

    try:
        os.startfile(url)  # type: ignore[attr-defined]  # 仅 Windows
    except Exception:  # noqa: BLE001
        subprocess.run(["cmd", "/c", "start", "", url], shell=False)


def run_check() -> int:
    """环境自检（供「① 首次安装」脚本调用）：只报告状态，不启动服务。"""
    print("  解释器     :", sys.executable)
    print("  Python 版本:", sys.version.split()[0])
    ok = True

    try:
        import streamlit  # noqa: WPS433

        print("  Streamlit  :", streamlit.__version__)
    except ImportError:
        print("  Streamlit  : [X] 缺失")
        ok = False

    try:
        import openai  # noqa: WPS433

        print("  openai SDK :", openai.__version__, "（将使用官方 SDK 引擎）")
    except ImportError:
        print("  openai SDK : 未安装（将自动使用 httpx/标准库引擎，功能一致）")

    try:
        import httpx  # noqa: WPS433

        print("  httpx      :", httpx.__version__)
    except ImportError:
        print("  httpx      : 未安装（将回退到 Python 标准库 urllib）")

    print("  app.py     :", "存在" if os.path.isfile(os.path.join(ROOT, "app.py")) else "[X] 缺失")
    if not os.path.isfile(os.path.join(ROOT, "app.py")):
        ok = False

    key = find_api_key()
    if key:
        print(f"  API Key    : 已配置（{key[:6]}...{key[-4:]}）")
    else:
        print("  API Key    : 未配置 —— 启动后可在网页左侧「临时 API Key」填写")

    return 0 if ok else 1


def main() -> int:
    # 参数 1 为窗口标题前缀，仅用于日志辨识；此处保留占位，避免误以为漏用
    _title = sys.argv[1] if len(sys.argv) > 1 else "APP"
    try:
        port = int(sys.argv[2]) if len(sys.argv) > 2 else 8501
    except ValueError:
        port = 8501

    if "--check" in sys.argv:
        print("=" * 60)
        print("  环境自检结果")
        print("=" * 60)
        return run_check()

    print("=" * 60)
    print("  实验报告批阅多智能体系统 - 启动引导")
    print("=" * 60)

    python = resolve_python()
    if not python:
        print()
        print("  [X] 没有找到可用的 Python 3.9+。")
        print("      请先安装 Python（推荐 Anaconda 或 python.org 官方安装包，安装时勾选 Add to PATH），")
        print("      然后重新双击「① 首次安装（双击我）.bat」。")
        return 1

    try:
        with open(PY_CACHE, "w", encoding="utf-8") as handle:
            handle.write(python)
    except OSError:
        pass

    ensure_env_file()
    ensure_deps(python)

    if find_api_key():
        out("  [√] 已找到 DeepSeek API Key")
    else:
        out("  [!] 未找到 DeepSeek API Key —— 页面仍可打开，")
        out("      请在侧边栏「临时 API Key」里填写，或运行「① 首次安装」写入 .env")

    # 记录端口，供「③ 停止项目」脚本使用
    try:
        with open(os.path.join(ROOT, ".run_port.txt"), "w", encoding="utf-8") as handle:
            handle.write(str(port))
    except OSError:
        pass

    print()
    print(f"  正在启动 Streamlit（端口 {port}）……")
    print("=" * 60)
    print()

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"
    env.setdefault("STREAMLIT_SERVER_HEADLESS", "true")

    command = [
        python, "-m", "streamlit", "run", os.path.join(ROOT, "app.py"),
        "--server.port", str(port),
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false",
    ]

    # 服务窗口标题由外面的 bat 负责设置（写在 app.py 的启动命令之前）
    server = subprocess.Popen(command, cwd=ROOT, env=env)
    try:
        wait_and_open(port)
        print()
        print("  服务运行中。关闭本窗口或按 Ctrl+C，或双击「③ 停止项目（双击我）.bat」即可停止。")
        server.wait()
    except KeyboardInterrupt:
        out("  收到 Ctrl+C，正在停止服务……")
    finally:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())

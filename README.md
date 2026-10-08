# 🧪 实验报告批阅多智能体系统

一个**完全本地运行**的网页应用：模拟 4 个智能体按顺序协作，对学生的实验报告做出**可解释**的批阅评分。
每一个扣分点都能追溯到报告中的具体位置和原文，并附带改进建议。

模型侧使用 **DeepSeek**（OpenAI 兼容接口，`base_url = https://api.deepseek.com`，`model = deepseek-chat`）。

---

## 一、目录结构

```
实验报告批阅系统/
├── ① 首次安装（双击我）.bat      # 一键装环境：找 Python → 装依赖（清华/阿里镜像）→ 配 API Key → 自检
├── ② 启动项目（双击我）.bat      # 一键启动：自动找解释器 → 拉起服务 → 就绪后自动打开浏览器
├── ③ 停止项目（双击我）.bat      # 一键停止：按窗口标题 + 端口 8501 精确结束监听进程
├── ④ 推送到GitHub（双击我）.bat  # 一键推送：git init → add/commit → push（远端已配好 220122）
├── GitHub私人仓库操作指南.md      # 建私人仓库 + 生成 Token 的图文步骤（做完可删）
├── app.py                        # 全部业务代码（智能体 + 流程编排 + 界面 + 渲染），单文件
├── launcher.py                   # 「② 启动项目」背后的启动引导器（依赖检查/开浏览器）
├── requirements.txt              # 依赖清单（实际上只有 streamlit 是必需的）
├── .env.example                  # API Key 配置模板（复制为 .env 后填写）
├── .gitignore / .gitattributes   # 屏蔽 .env/.venv/缓存；统一 bat 的 CRLF 换行
├── README.md                     # 本说明文件
└── .streamlit/
    └── config.toml               # Streamlit 主题与运行参数（可选，不影响功能）
```

> 脚本运行时会生成两个缓存文件，可随时删除（已加入 .gitignore）：
> `.run_python.txt`（记住用哪个 Python）、`.run_port.txt`（记住端口）。

### 四个 bat 脚本怎么用（Windows 用户看这里）

| 脚本 | 什么时候双击 | 它会做什么 |
|---|---|---|
| ① 首次安装 | **只需一次** | 自动搜索本机 Python（PATH / py 启动器 / Anaconda / 常见安装路径）→ 检查 Streamlit，缺了就用清华镜像装、失败自动换阿里云镜像 → 生成 `.env` 并打开记事本让你填 API Key → 最后打印环境自检结果 |
| ② 启动项目 | 每次使用 | 复用 ① 记住的解释器 → 检查依赖 → 打印日志并启动服务 → 轮询健康检查，就绪后**自动打开浏览器** |
| ③ 停止项目 | 用完后 | 先按窗口标题结束服务窗口，再按端口 `8501` 精确结束**监听**进程（不会误杀浏览器） |
| ④ 推送到GitHub | 想上传代码时 | 检查 git → `git init`（首次）→ 配置远端 → 提交改动 → `git push`；失败时打印 4 条常见原因 |

**推送目标（已按你的账号配置好）**：私人仓库
`https://github.com/220122/experiment-report-review-agent`
→ 首次推送前请先在 <https://github.com/new> 建好这个 **Private** 仓库，
详细图文步骤见 `GitHub私人仓库操作指南.md`。

想换端口，改这两个地方（保持一致即可）：`② 启动项目（双击我）.bat` 里 `launcher.py "实验报告批阅" 8501` 的最后一个参数。

---

## 二、四个智能体如何协作

```
        用户输入（指导书/评分标准 + 学生报告）
                       │
                       ▼
        ┌──────────────────────────────┐
        │ 1. 护栏监督员 Guard          │  检查抄袭 / 数据篡改 / 代写 / 不当言论
        └──────────────┬───────────────┘
                       │
           ┌───────────┴────────────┐
           │ 风险 medium/high       │ 风险 none/low
           ▼                        ▼
   ⛔ 立即中断，提示人工复核   ┌──────────────┐   ┌──────────────┐
      （不再进入评分环节）     │ 2. 格式审查员 │ ‖ │ 3. 内容评审员 │  ← 两个线程并行执行
                              │   0-10 分    │   │   0-70 分    │
                              └──────┬───────┘   └──────┬───────┘
                                     └────────┬─────────┘
                                              ▼
                              ┌──────────────────────────────┐
                              │ 4. 评分与解释官 Judge         │  0-100 分
                              │   汇总扣分依据 + 改进建议      │
                              └──────────────────────────────┘
```

| 智能体 | 职责 | 输出 |
|---|---|---|
| 护栏监督员 | 学术风险筛查，熔断开关 | `risk_level` + 违规明细 + 复核建议 |
| 格式审查员 | 结构、排版、字数、图表编号、引用规范 | 格式得分 0-10 + 问题清单 |
| 内容评审员 | 原理、步骤、数据处理、分析讨论、结论逻辑 | 内容得分 0-70 + 问题清单（含正确做法） |
| 评分与解释官 | 综合上游结论、逐条给出扣分依据 | 总分 0-100 + 维度表 + 扣分依据 + 改进计划 |

**计分口径**：格式 10 + 内容 70 + 加分项 20 = 100 分。
两位审查员负责 80 分，其余 20 分由评分与解释官依据评分标准给出（必须写明加分或不予加分的理由）。

---

## 三、本地运行步骤

### 最快路径（Windows 用户）

1. 双击 **`① 首次安装（双击我）.bat`**（只需一次；会打开记事本让你填 API Key）
2. 双击 **`② 启动项目（双击我）.bat`** → 浏览器自动打开 <http://localhost:8501>
3. 用完后双击 **`③ 停止项目（双击我）.bat`**

想上传到 GitHub：改好 `④ 推送到GitHub（双击我）.bat` 里的仓库地址后双击它。

下面是手动命令行的步骤，macOS / Linux 用户或想自己排查问题时用。

### 第 1 步：确认 Python 与 Streamlit

要求 Python 3.9+。先在终端里检查：

```bash
python --version
python -c "import streamlit; print(streamlit.__version__)"
```

- 若打印出版本号（如 `1.37.1`）→ **直接跳到第 3 步**，你什么都不用装。
- 若提示 `No module named 'streamlit'` → 继续第 2 步。

> 本机已实测可用的解释器：`E:\Anaconda\python.exe`（Python 3.12.7，已带 Streamlit 1.37.1 与 httpx）。
> 如果你的电脑上也装了 Anaconda，用它的 python 最省事。

### 第 2 步：安装依赖（仅在缺 Streamlit 时需要）

```bash
# 国内镜像，速度快
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

> 如果 `openai` 装不上也没关系——程序会自动改用 httpx / 标准库引擎，功能完全一致。
> 只想装最小依赖的话，一句就够：`pip install streamlit -i https://pypi.tuna.tsinghua.edu.cn/simple`

### 第 3 步：设置 API Key

到 <https://platform.deepseek.com/api_keys> 申请一个 Key（形如 `sk-xxxxxxxx`），然后用**任意一种**方式配置：

**方式 A：环境变量（推荐）**

```powershell
# Windows PowerShell（当前窗口有效）
$env:DEEPSEEK_API_KEY="sk-你的Key"
```

```cmd
:: Windows CMD（当前窗口有效）
set DEEPSEEK_API_KEY=sk-你的Key
```

```bash
# macOS / Linux
export DEEPSEEK_API_KEY=sk-你的Key
```

**方式 B：`.env` 文件** —— 把 `.env.example` 复制为 `.env`，填好 Key（程序启动时会自动读取）。

**方式 C：网页侧边栏** —— 启动后直接在左侧「临时 API Key」输入框粘贴，只存在内存里，不落盘。

### 第 4 步：启动

```bash
streamlit run app.py
```

浏览器会自动打开 <http://localhost:8501>。若没有自动打开，手动访问该地址即可。

> 如果 `streamlit` 命令找不到，说明它不在 PATH 里，改用：
> `python -m streamlit run app.py`
> 例如本机：`E:\Anaconda\python.exe -m streamlit run app.py`

### 第 5 步：使用

1. 点「🔌 测试连接」确认 API Key 可用（可选但推荐）。
2. 左框粘贴**实验指导书 / 评分标准**，右框粘贴**学生实验报告**。
   想快速试用，可以点「📋 填充示例指导书」和「📝 填充示例报告（含典型错误）」。
3. 点「🚀 开始批阅」，等待 30~90 秒。
4. 结果分区展示：**评分与解释 / 格式审查详情 / 内容审查详情 / 护栏检查结果**。
5. 可下载 Markdown 批阅报告或完整 JSON 结果。

---

## 四、常见问题

| 现象 | 原因与解决 |
|---|---|
| `401 Authentication Fails` | API Key 不对或没生效，重新设置 `DEEPSEEK_API_KEY` |
| `Connection error` / 超时 | 网络不通，确认能访问 `https://api.deepseek.com` |
| `Insufficient Balance` | DeepSeek 账户余额不足，去官网充值 |
| `response_format` / `json_object` 报错 | 侧边栏关掉「启用 JSON 输出模式」 |
| 模型返回空内容 | 稍后重试，或把温度调到 0.1 |
| 评分结果看起来不合理 | 在侧边栏把温度降到 0.1，或在上方「实验指导书」里写清具体评分细则 |
| 想换调用引擎 | 侧边栏「模型调用引擎」选「仅用 httpx / 标准库（零安装）」 |

---

## 五、实现要点（答辩时可讲）

1. **护栏熔断**：护栏监督员判定 `medium/high` 风险时，`review_report()` 直接 return，
   后续智能体一次都不会被调用——用自检脚本可验证「熔断后 LLM 调用次数 = 1」。
2. **并行编排**：格式审查员与内容评审员互不依赖，用 `ThreadPoolExecutor` 并行；
   各自的异常独立捕获，一个失败不会拖垮另一个，评分官会在降级信息中说明。
3. **可解释评分**：所有提示词都强制要求 `location`（具体位置）+ `quote`（真实原文摘录）+ `reason`（原因）
   + `points`（分值）+ `suggestion`（建议）；评分官还被要求做「账目自检」：
   `100 − 扣分 + 加分 ≈ 总分`，页面上会把这条账目直接算给读者看。
4. **健壮的解析**：模型输出常带 ```` ```json ```` 围栏或前后废话，`extract_json()` 做了三级兜底
   （直接解析 → 去围栏 → 截取首尾大括号），分数一律用 `clamp_int()` 约束到合法区间。
5. **双引擎**：`SDKEngine`（openai 官方 SDK）与 `HTTPXEngine`（httpx/标准库 SSE 流式解析）
   暴露完全相同的接口，`make_engine()` 负责自动选择与降级，上层智能体代码零感知。

# 如何创建私人仓库并推送（图文步骤）

> 本文件是一次性操作指南，按顺序做完即可。做完后可以删掉。

---

## 一、在 GitHub 网页上新建私人仓库

1. 用**已登录 `220122` 的浏览器**打开：<https://github.com/new>
   （或从 <https://github.com/220122?tab=repositories> 点右上角的 **New** 按钮）

2. 按下表填写，**一个字母都不能错**：

   | 表单项 | 填什么 |
   |---|---|
   | **Owner**（所有者） | `220122` |
   | **Repository name**（仓库名） | `experiment-report-review-agent` |
   | Description（描述，可留空） | 实验报告批阅多智能体系统 |
   | **Visibility**（可见性） | **Private / 私人** ← 一定要选这个 |
   | Add a README file | ❌ **不要勾选** |
   | Add .gitignore | ❌ **不要勾选**（本项目自带 .gitignore） |
   | Choose a license | ❌ **不要勾选** |

3. 点最下面的绿色按钮 **Create repository**。

   > 如果提示 `Repository name already exists`，说明你已经有同名仓库了。
   > 这时两种选择：① 直接沿用现有仓库（继续第二步即可）；
   > ② 把本项目 ④ 脚本里的仓库名改成别的（例如 `experiment-report-review-agent-2026`）。

4. 创建成功后页面会跳转到新仓库。**先确认右上角有个 `Private` 标记**。
   私人仓库未登录是打不开的，这正是我们想要的效果。

---

## 二、双击脚本推送

回到项目文件夹 `E:\DSH\tr agengt`，双击：

```
④ 推送到GitHub（双击我）.bat
```

会出现什么、该怎么办：

| 屏幕提示 | 你要做的事 |
|---|---|
| `关于登录（私人仓库必须认证…）` + 请按任意键继续 | 按一下回车 |
| 弹出浏览器要求 **Authorize Git Credential Manager** | 点 **Authorize** / 授权登录 `220122` |
| 黑窗口提示 `Username for 'https://github.com':` | 输入 `220122` 后回车 |
| 黑窗口提示 `Password for 'https://github.com/220122@github.com':` | **粘贴 Personal Access Token**（不是账号密码！） |
| 什么都没提示，直接出现 `推送成功！` | 太好了，本机凭据管理器里已有 `220122` 的登录记录 |

> **本机已检测到凭据管理器里有 `git:https://github.com` / User: 220122 的记录**，
> 所以大概率不需要重新登录，直接就会成功。

### 如果卡在 Password 上：生成 Personal Access Token

GitHub 早就不允许用登录密码推送了，必须用 Token：

1. 打开 <https://github.com/settings/tokens> → **Generate new token** → 选
   **Generate new token (classic)**
2. Note 填 `experiment-report-review-agent`，Expiration 选 90 天或 No expiration
3. 勾选权限：**`repo`**（私人仓库必需，勾最上面那个大项即可）
4. 拉到底点 **Generate token**，**立刻复制**那串 `ghp_...`
   （离开页面就再也看不到了）
5. 回到黑窗口，在 `Password:` 处**右键粘贴**（黑窗口里不显示粘贴内容，正常），回车

---

## 三、验证

推送成功后脚本会打印仓库地址。打开：

<https://github.com/220122?tab=repositories>

应该能看到 `experiment-report-review-agent`，旁边有一个 **Private** 标签，
点进去应能看到这 12 个文件：

```
.env.example    .gitattributes  .gitignore
.streamlit/config.toml          README.md
app.py          launcher.py     requirements.txt
① 首次安装（双击我）.bat          ② 启动项目（双击我）.bat
③ 停止项目（双击我）.bat          ④ 推送到GitHub（双击我）.bat
```

**不会**上传 `.env`（里面有你的 API Key）——它已被 `.gitignore` 排除，检查一下确实不在列表里。

---

## 四、以后怎么更新代码

改完代码后，直接双击 `④ 推送到GitHub（双击我）.bat` 就行，
脚本会自动 `git add -A` → `git commit` → `git push`。

---

## 五、常见报错

| 报错 | 含义与处理 |
|---|---|
| `Repository not found` | 仓库还没建，或名字拼错，或建成了 Public 而 Token 无权限 → 回到第一步 |
| `Authentication failed` | Token 过期/权限不足 → 重新生成 Token（必须勾 `repo`） |
| `Updates were rejected` / `non-fast-forward` | 远端有本地没有的提交 → 在黑窗口执行 `git pull --rebase origin main` 后重试 |
| `Failed to connect ... timeout` | 网络问题；国内可尝试给 git 配代理：`git config --global http.proxy http://127.0.0.1:端口` |
| `fatal: not a git repository` | 不在项目目录里运行；确保从 `E:\DSH\tr agengt` 双击脚本 |

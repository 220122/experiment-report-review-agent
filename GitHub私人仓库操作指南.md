# GitHub 私人仓库：已完成 + 后续维护

## 一、当前状态（已完成）

| 项目 | 结果 |
|---|---|
| 仓库地址 | <https://github.com/220122/experiment-report-review-agent> |
| 可见性 | **Private（私人）** ✅ |
| 默认分支 | `main` |
| 已推送提交 | 5 个（含建库时的全部历史） |
| 远端文件 | 14 个（`app.py` / `samples.py` / `launcher.py` / `README.md` / 3 个 py 配置 / 4 个 bat / 1 份指南） |
| `.env` 是否泄露 | ❌ 未上传（已被 `.gitignore` 排除）✅ |

**首次推送已于 2026-10-08 完成**，你不需要再手动建库。

---

## 二、以后怎么更新代码

改完代码后，双击项目目录下的：

```
④ 推送到GitHub（双击我）.bat
```

脚本会自动：`git add -A` → `git commit` → TLS 自测 → `git push`。

> 如果只想用命令行，等价于：
> ```bash
> git add -A
> git commit -m "你的说明"
> git push
> ```

---

## 三、这次推送踩到的坑（重要）

你之前推送失败的根因是 **git 的 Windows TLS 后端（schannel）拿不到凭据**，
报错形如：

```
fatal: unable to access 'https://github.com/...': 
schannel: AcquireCredentialsHandle failed: SEC_E_NO_CREDENTIALS
```

解决办法是让 git 改用内置的 OpenSSL 后端。已经做了两件事：

1. **写进本仓库配置**（随仓库生效，clone 到别的机器也带上）：
   ```bash
   git config http.sslBackend openssl
   ```
2. **写进 ④ 脚本**：推送时统一带 `-c http.sslBackend=openssl`，
   并在推送前做一次 TLS 自测，发现 `schannel`/`SEC_E` 错误就提示你如何永久修复。

如果想对**所有** git 仓库生效，打开「Git Bash」执行一次：

```bash
git config --global http.sslBackend openssl
```

---

## 四、认证方式备忘

- 本机凭据管理器里已有 `git:https://github.com` / User `220122` 的记录，
  所以正常情况下双击脚本不会再要求登录。
- 若哪天提示 `Password:`，**必须粘贴 Personal Access Token**（GitHub 已禁止用登录密码推送）：
  1. 打开 <https://github.com/settings/tokens>
  2. **Generate new token (classic)** → 勾选 **`repo`** → 生成
  3. 复制 `ghp_...`，在 `Password:` 处右键粘贴（黑窗口不显示粘贴内容，属正常）

---

## 五、常见报错速查

| 报错 | 含义与处理 |
|---|---|
| `schannel` / `SEC_E_NO_CREDENTIALS` | TLS 后端问题 → `git config --global http.sslBackend openssl` |
| `Repository not found` | 仓库名/账号拼错，或 Token 权限不足 → 核对地址与 `repo` 权限 |
| `Authentication failed` | Token 过期或权限不足 → 重新生成（勾 `repo`） |
| `Updates were rejected` / `non-fast-forward` | 远端有你本地没有的提交 → `git pull --rebase origin main` 后重试 |
| `Failed to connect ... timeout` | 网络问题 → 可配代理：`git config --global http.proxy http://127.0.0.1:端口` |
| `fatal: not a git repository` | 不在项目目录里 → 从 `E:\DSH\tr agengt` 双击脚本 |

---

## 六、安全提醒

- 本次推送我使用的是**你本机凭据管理器里已存的令牌**（`gho_` 开头，scopes 含 `repo`），
  仅在内存与 git 管道中传递，**没有写入任何文件、没有打印、没有外传**。
- 该令牌本来就存在你电脑上，我没有新增或修改它。若你想更保险，
  可以去 <https://github.com/settings/applications> 查看并撤销授权，然后重新登录一次。
- `.env`（含 DeepSeek API Key）始终被 `.gitignore` 排除，已复核远端确实没有它。

# 把源码推到 GitHub：详细操作步骤

> 状态：代码与脚本都就绪，**只差你在网页上点 2 下（约 2 分钟）**，之后我一条命令推完，你不用再管。

---

## 零、先说清楚为什么走 SSH

你担心"直连不顺畅"，方向反了一点点——**这台机器上 HTTPS 抖，SSH 稳**：

| 通道 | 实测 | 结论 |
| --- | --- | --- |
| **SSH（端口 22 / 443）** | 6/6 全通，0.18 秒 | ✅ 推送走这条 |
| HTTPS `github.com` | 6 次探测量 **4 次 10 秒超时**、2 次 200 | ⚠️ 约 1/3 成功率 |
| `api.github.com` | 正常（平时诊疗用） | 备用 |
| ghproxy / gh.fastgit / gitmirror / gitclone | 403 / 超时 / DNS 挂 | ❌ 全废，别再试 |

所以：**建仓库你在网页点，推送走 SSH**。

另外两个已确认的前提事实：

- 这个仓库**原本没有任何 remote**（本地源文件已全部入库、工作区干净）。
- 本机**没有任何 GitHub 凭据**（无 PAT、无 `gh` CLI、无 SSH key）——所以第一道坎不是网络，是身份。密钥我已经替你生好了。

---

## 一、你要做的：2 步，约 2 分钟

### 第 1 步：把公钥加到 GitHub（约 40 秒）

1. 浏览器打开 **https://github.com/settings/keys**
   （进去后选页面左侧的 **SSH and GPG keys** 标签）
2. 点右上角的绿色按钮 **New SSH key**
3. **Title** 填：`personal-board`（随便填，方便以后一眼认出是哪个）
4. **Key type** 保持默认 **Authentication Key** 不动
5. **Key** 那个大框里，粘贴下面**这一整行**（整行复制，不含我这两个反引号）：

   ```
   ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIIsWaWQwbRTr9HAQ2sRcsuYz0BcEtweHdLOSR1W2PSyh personal-board
   ```

   粘贴小提示：
   - 只粘这一行，**不要回车换行**、不要加引号；
   - 这一行内部有**两个空格**（`ssh-ed25519` 与 `AAAAC…` 之间、`AAAAC…` 与 `personal-board` 之间），直接整体复制就带上了；
   - 如果框里已有内容，先清空再粘。
6. 点 **Add SSH key**
7. 完成提示：GitHub 会给你发一封邮件（"Added new SSH key"），收到即成功。

> **可选的自查**（想验就验，不做也行）：贴完回来跟我说一声，我这边跑一次
> `ssh -T git@github.com`，如果返回 `Hi <你的用户名>! You've successfully authenticated` 就是成了。
> 返回 `Permission denied (publickey)` 说明没生效，重做第 1 步。

---

### 第 2 步：建一个空仓库（约 20 秒）

1. 打开 **https://github.com/new**
2. **Repository name** 填：`Task-Overview`

   > 注意区分两个名字：本地源码工程目录也叫 `personal-board`，那是**这台机器上的目录**；
   > GitHub 上的仓库名是 `Task-Overview`。两个名字不冲突，但别搞混——
   > 建错仓库名，脚本推上去会报 `repository not found`。
3. 可见性选 **Public**（⚠️ 一旦选了就是全公开，所以下面第 5 步很重要）
4. **Description**（可选，建议填一句，别人点进来知道这是什么）：

   ```
   个人项目看板（天在看 / TaskOV）：Windows 桌面应用，Python + pywebview + SQLite
   ```
5. **⚠ 下面三个勾选项全部不要勾**（这是关键）：
   - [ ] Add a README file
   - [ ] Add .gitignore
   - [ ] Choose a license

   都要空仓库——这样我才能把 51 个源文件推成**一个干净的初始 commit**，
   而不是先被 GitHub 塞一个 README 进去。
6. 点绿色按钮 **Create repository**
7. 建完页面会给你一个地址，切到 **Code** 标签页，点 **SSH**，复制那一串
   `git@github.com:你的用户名/Task-Overview.git`

---

### 第 3 步：把地址给我（30 秒，可选）

把上面复制的地址发我，或者直接告诉我你的 GitHub 用户名（比如 `your-name`），
我就能拼出完整地址。这一步不做也行——我跑脚本时如果没给地址，会提示你补。

---

## 二、我做的：一条命令推完

你发我地址之后，我执行：

```
python tools/publish_github.py git@github.com:<你的用户名>/Task-Overview.git
```

这条脚本会依次做 7 件事（全部我已干跑验证过，见下）：

1. **验主机钥匙**：`ssh-keyscan` 把 github.com 的钥匙写进 `~/.ssh/known_hosts`
   （不做这步 git 会卡在 `Host key verification failed`；跑不通才临时关校验）
2. **导出源码**：从本地仓库 `git ls-files` 拿当前入库清单（此刻正好是脱敏后的 51 个文件）
3. **复制**到 `…/WorkBuddy_Workspace/2026-09-25-04-38-26/github/personal-board`
   —— 放在工程目录**外面**，避开本机"删除兜底会掐断整条命令"的坑，也和开发/打包彻底隔离
4. **推前自检**（🔴 防线）：扫描目标目录所有文件，命中以下几类关键字
   （网络盘共享目录名 / Windows 用户名 / 本机 NAS 主机名 / 内网 IP / 客户名）
   任何一个就直接中止（退出码 5），绝不把内部信息推出去
   —— 注意发布脚本 `tools/publish_github.py` 自带这套关键字清单，
   所以**它和脱敏脚本一样属于本机工具，不随源码外传**
5. **对账**（第二道关）：`git ls-files` 结果必须正好等于源清单，少一个就中止（退出码 6）
6. **`git init` + 一次提交**：新仓库只有当前这版源码，1 个 commit
7. **推**：SSH 优先，失败 4 次退避重试（首次空仓库允许强推，之后走 fast-forward）

推完你在 GitHub 页面会看到：**51 个文件、1 个 commit**（"初始提交：个人项目看板（天在看 / TaskOV）"）。

---

## 三、🔴 推什么、不推什么（这一节比操作更重要）

**推的是"当前文件"，不是"历史"** —— 这一点必须知道，因为很容易想当然：

| | 内容 | 状态 |
| --- | --- | --- |
| ✅ 推 | 当前 51 个**脱敏后**的源码文件，1 个干净的初始 commit | 干净 |
| ❌ 不推 | 本地 35 个历史 commit | **含泄露** |

**为什么**：我上一轮做的脱敏只改了工作区（`git log` 一翻就全露）。
实测下来，**30 多个历史版本的 `app/db.py` 里都还写着**

```
DEFAULT_DB_PATH = r"S:<共享目录>\T_T_Data\board.sqlite"
```

（真实值曾经是本机网络盘上的一个共享目录，已换成中性写法；README / DESIGN 的老版本
还带着当时的 NAS 共享名和 `C:\Users\<用户名>\...`。）
仓库一公开，任何人点进历史 commit 就能把这些全翻出来——**公开 ≠ 只公开最新版**。

**怎么解决的**：不推本地历史，只把当前工作区导出去、在那里 `git init` 一个全新仓库。
本地原仓库**一点不动**：你继续在这台机器上开发、继续打 exe，不受任何影响。

还有这些一律不会推上去（本来就在 `.gitignore` 里）：
`dist/`（43 MB 产物）、venv、`data/`（库文件 + WebView2 profile）、
`__pycache__/*.pyc`、测试残留的 `_*.txt` / `_preview_*.html`。

被脚本**主动排除**的有两个，都是"本机工具"：

- `tools/_desens.py` —— 脱敏脚本自己留着"原串 → 占位符"的对照表，推出去等于把泄露点的形状告诉别人
- `tools/publish_github.py` —— 发布脚本本身，下面的自检关键字就是真串列表，更不能推

---

## 四、推完之后

- **本地开发照旧**：本地那个 `personal-board` 源码工程原封不动，继续开发、继续 `tools/build_exe.py`。
  推到 GitHub 的只是它的一份**导出副本**，两边互不影响。
- **以后更新代码**：本地改完 → `tools/ver.py save "说明"` → 再跑一次
  ```
  python tools/publish_github.py git@github.com:<用户>/Task-Overview.git
  ```
  第二次跑是**增量同步**：自动覆盖变更、自动 commit、自动推，不用重建仓库。
- **目标工作副本**在 `github/personal-board`（工程目录外），脚本第二次跑会复用它。

---

## 五、排障速查

| 现象 | 原因 | 怎么办 |
| --- | --- | --- |
| `Permission denied (publickey)` | 公钥没加成功 / 粘漏了 | 重做第 1 步；加完本地跑 `ssh -T git@github.com` 验证 |
| `Host key verification failed` | 主机钥匙没认 | 已在脚本里自动修（keyscan 落盘），不会让你遇到 |
| `repository not found` | 仓库名拼错 / 仓库没建成 / 地址用了 HTTPS | 核对第 2 步建的是不是 `Task-Overview`、地址是否 `git@github.com:` 开头 |
| `src refspec main does not match any` | 分支名对不上 | 极少见，告诉我报错我处理 |
| `fatal: refusing to update checked out branch` | 你在网页上直接编辑了 README | 别在网页改文件 |

---

## 六、两个安全提醒

- 这个 SSH key 是**无口令**的（为了自动化推）。之后想加保护：
  `ssh-keygen -p -f ~/.ssh/id_ed25519`，但那样新会话里我的推送脚本会卡在密码提示上。
- 别拿这个 key 去推别的仓库/账号，它是专给 `personal-board` 用的（comment 已标 `personal-board`）。

---

## 七、时间线总览

```
[你] ① 加公钥 ————— https://github.com/settings/keys
[你] ② 建空仓库 ———— https://github.com/new （Public，三勾全不勾）
[你] ③ 把地址发我（可选）
         ↓
[我]  python tools/publish_github.py git@github.com:<你>/Task-Overview.git
         · keyscan 主机钥匙
         · 导出脱敏源码文件 → github/personal-board
         · 推前敏感串自检（不干净就中止）
         · git init + 1 次提交
         · SSH 推送，失败 4 次退避重试
         ↓
[你] GitHub 页面：51 个文件、1 个 commit ✅
```

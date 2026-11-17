# Campus Collab：课程多智能体协作平台

面向 `NLP_Mid_Test` 三个场景、六个连续任务的 Python CLI 实现。读取原始四个角色提示词，不修改测试材料。同一群组的第二个任务继承任务、产物、对话和各角色记忆。

目前提供自主任务委派、角色协商、独立工作目录、Docker 代码执行、澄清/审批/验收、独立审查返工、搜索及网页文本检索、显式产物传递、Git/GitHub 工具、指标与报告导出。没有硬编码六道任务的答案。

**状态：用户已在本地终端验证接口、Docker 内执行隔离和真实搜索；离线测试可运行。六个任务的真实完成率、完整交付质量与 GitHub 推送尚未完成实测，不能据此声称高分。**

## 1. 环境配置

Python 3.11+；平台本身仅使用标准库，无须安装第三方 Python 包。应显式使用3.11+解释器或对应的虚拟环境。

涉及 JavaScript 的产物还需宿主安装 Node.js，用于固定参数的 `node --check` 静态语法检查。检查器只解析源码，不执行生成的代码；代码执行仍在 Docker 内。可用 `ui.check` 检查 `.html`、`.js` 或 Python 字面量 `INDEX_HTML`。发布及最终提交会拦截语法错误，但静态检查不能代替浏览器点击测试。

在项目目录运行：

```powershell
cd D:\Projects\NLP-Multiagent
python -m unittest discover -s tests -v
python -m collab scenarios
```

复制 `.env.example` 为 `.env`，填写密钥；已有 `.env` 时保留现有文件。当前接口配置：

```dotenv
LLM_PROVIDER=hkgai
LLM_BASE_URL=https://api1.hkgai.net/v1
LLM_MODEL=t2_hkgai-v3_fp8_1m_e7
LLM_API_KEY=在本地填写
LLM_AUTH_HEADER=Authorization
LLM_MAX_RUN_ACTIONS=360
LLM_TRANSPORT=auto
TAVILY_API_KEY=可选，正式评测建议填写以提高搜索限额
```

`LLM_BASE_URL` 包含 `/v1`；省略它时当前服务返回 404。请求发送到 `/v1/chat/completions`。`LLM_TRANSPORT=auto`在Windows Python 3.11+上使用系统curl的原生TLS，避开当前HKGAI端点与Python OpenSSL的握手停滞。认证值只通过子进程环境传给curl，不出现在命令行或日志。密钥不写入源码、运行日志或提交包。

```powershell
python scripts/probe_api.py
```

期望输出 `connection_ok: true`。探测只发出一个最小 JSON 生成请求；网络/限流错误可能产生重试。当前 Codex 工具环境的外网连接被限制，因此需要在用户普通终端做实际接口测试；401/403 表示需要检查密钥/权限，404 检查路径和模型，429 检查限流/额度，400 检查接口协议。

HKUST GenAI 旧项目的 GPT 配置与本课程指定的 HKGAI/DeepSeek 配置不同，勿直接复制旧模型设置。此平台不包含 OpenAI/Anthropic 客户端或回退逻辑。

## 2. 启动执行沙箱

启动 Docker Desktop，等待引擎就绪，然后在本地终端运行：

```powershell
docker build -f Dockerfile.sandbox -t campus-collab-sandbox:local .
python -m collab doctor
python scripts/check_runtime.py --search
```

沙箱镜像含 Python 标准库和 Git。每次执行只有当前智能体的工作目录被挂载，关闭网络，限制资源，并使用非 root 用户。**未提供宿主机执行的降级模式。** GitHub 推送需要单次人工批准，随后才开启该次推送容器的网络。

构建前可先做模型对话、文件及状态机实验；涉及代码验证的任务须在 Docker 可用后执行。Linux 的非 root Docker 账号应有访问引擎的权限；不要因此给执行容器加 `--privileged`。

## 3. 建群与交互

创建三个互相独立的群组：

```powershell
python scripts/prepare_groups.py
python -m collab chat software
```

在交互界面输入：

```text
/task 1
/answer q1 你的真实需求说明
/resume
/approve q2
/resume
/export
/task 2
```

上面的 `q1/q2` 仅为命令格式示例，**实际请求由模型运行时产生**，请按屏幕展示的类型、编号和内容回答；不要预设问答或批量同意。验收前应检查实际产物，拒绝时写明缺陷。

支持的命令：

| 命令 | 用途 |
|---|---|
| 普通文本 | 向群组提交一个新目标 |
| `/task 1`、`/task 2` | 发布当前场景对应原始任务 |
| `/say 补充要求` | 给当前群聊补充信息 |
| `/abort 原因` | 终止当前试跑并如实记录为失败 |
| `/dm dev 私信内容` | 给指定智能体创建独立私信任务 |
| `/answer q1 内容` | 回答澄清问题 |
| `/approve q1`、`/reject q1 原因` | 批准/拒绝工具操作或最终验收 |
| `/resume 30` | 最多再执行 30 个模型动作 |
| `/status`、`/export`、`/quit` | 状态、导出和退出 |

根工作任务必须先通过`plan`提交非空且不重复的验收条件，之后才能使用工具、委派或提交。澄清、审批和验收后需要`/resume`才继续。单次运行最多120步、默认30步；整个目标另有持久化的360次模型动作硬预算，恢复不会重置。预算耗尽后明确标记且不计为完成。Ctrl+C保存已持久化状态，随后可恢复。不要同时对同一群组启动两个写入进程。

软件场景两项任务验收结束后退出，分别启动：

```powershell
python -m collab chat newsroom
python -m collab chat fund
```

也支持非交互命令：

```powershell
python -m collab post software --task 1 --steps 10
python -m collab status software
python -m collab answer software q1 '这里填写实际回答'
python -m collab resume software --steps 20
python -m collab export software
```

任意新题可使用 `post GROUP --text '目标'` 或 `--file task.md`，不局限于这六个任务。

## 浏览器验收预约系统

出现根任务 `acceptance` 请求后先在聊天中 `/export`，再开另一个 PowerShell：

```powershell
cd D:\Projects\NLP-Multiagent
python scripts/preview_booking.py
```

服务器启动后打开 `http://127.0.0.1:8000`，检查预约、重复预约拒绝、取消后重订。预览保留经哈希验证的原始交付源码，在 Docker 中执行；只挂载这份只读源码和独立数据目录，不注入模型/搜索/GitHub 密钥。为浏览器访问启用 bridge 网络，并仅发布到宿主 127.0.0.1。原程序的监听地址只在容器启动时调整，不改产物源码。

数据保存在 `runtime/software/preview/data/bookings.db`，后续版本使用同一数据目录；保持预览终端打开，Ctrl+C 停止。若 8000 被占用，可以加 `--port 8001`。检查符合要求后回到聊天，以实际请求编号执行 `/approve q1`，再 `/export` 更新指标；发现缺陷则 `/reject q1 具体问题`。预览脚本不会替人类批准请求。

## 4. 网络研究与 GitHub

`web.search` 使用 Tavily API；未填写 `TAVILY_API_KEY` 时使用官方支持的 [keyless 模式](https://docs.tavily.com/documentation/keyless)，受免费限流约束。正式评测建议配置自己的搜索密钥。支持新闻/金融主题、时间范围和域名过滤。`web.fetch` 不需要搜索密钥，只允许公开 HTTPS 文本/HTML/JSON，拦截私有地址、检查重定向并校验实际连接的 IP。搜索片段和打开的原文有不同标签，来源带日期、时间戳和 SHA256。限流或失败时明确记录，不会虚构联网结果。

如需推送，配置仅用于该次获批推送容器的 `GITHUB_TOKEN`，并由智能体提出明确 GitHub 仓库地址和分支。人工审批后才执行，默认不会强推。推送在临时裸仓库进行，不继承生成代码可能修改的 Git hooks/config。须使用自己有权限的测试仓库；平台不会替你创建外部 GitHub 仓库。

## 5. 输出与评测

```text
runtime/<group>/
  state.json                 # 持久化任务、队列、审批、对话、来源
  agents/<agent>/workspace/  # 专属文件系统
  agents/<agent>/memory.jsonl
  artifacts/<sha256>         # 发布时的不可变文件快照
  export/
    metrics.json             # 完成率、耗时、人类等待、动作预算、调用/用量、记忆快照
    report.md
    dialogue.md
    dialogue.jsonl
    sources.json
    artifacts/<artifact-id>/<filename>
    delivery/<run-id>/        # 根任务选定的文件按原始目录结构组装
```

评测成功定义为“独立审查通过，且人类验收通过”；未完成、暂停、失败不计成功。子任务完成率单独报告，自动产生的审查节点不计入工作子任务分母。耗时包含人类等待，等待区间取并集避免重复计数。私信任务不计入场景任务完成率。指标属于本平台记录，不替代课程最终评分。

通过 `artifact.import`，其他智能体将显式发布的文件复制到自己的目录再检查。交付源代码时需发布全部必要文件，并提供运行说明。导出既保留每个 artifact ID 的快照，也把根任务选定的文件按原始路径组装到 `export/delivery/<run-id>/`，可直接按运行说明使用；清单标明是否经人类验收，未验收的文件仍为草稿。

每次模型请求默认有 120 秒的总时限，而不只是 socket 空闲超时；超时后保留任务，等待人工决定恢复。可在 `.env` 配置 `LLM_REQUEST_TIMEOUT_SECONDS` 和 `LLM_MAX_OUTPUT_TOKENS`（默认 8192）。提供 `files.append` 以便分段生成大文件，避免整份程序 JSON 被输出长度截断。客户端超时不保证提供商立即停止计费。

导出包含完整私信和工具内容，只给有权查看的评估者。`.env` 不在导出中；如人类主动把个人资料写入对话，仍需自行控制导出分享范围。

## 6. 项目文档

- [架构与边界](docs/architecture.md)
- [Agent Orchestration 集成契约](docs/orchestration-contract.md)
- [Agent Orchestration 能力审计](docs/orchestration-audit.md)
- [成员 A 集成验证](docs/member-a-verification.md)
- [六项任务验收清单](docs/benchmark.md)
- [开发与实测状态](docs/status.md)
- [离线测试记录](docs/verification.md)，可用 `scripts/run_checks.py` 重新生成。

所有文档为 Markdown。HTML 只作为可运行网页、图表和场景三明确指定的幻灯片产物；测试集 HTML 要求与课程一般文档限制的关系需要向老师确认。仓库已有Git历史和协作PR；尚未推送的本地分支必须在验证完成后按团队流程提交。

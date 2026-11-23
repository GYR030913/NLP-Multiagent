# 场景实测报告与平台修复记录

本文件记录 `NLP_Mid_Test` 三个场景的真实运行结果、运行中发现的产品缺陷，以及由此引出的平台代码修复。所有改动均可用 `python -m unittest discover -s tests` 复现验证。

- 运行时间：2026-11-23
- 分支：`feature/member-a-orchestration`
- 模型：`newapi` / `GLM-4.6-W8A8`（校园网关）
- 搜索：Tavily API（本地密钥）

## 0. 总览

| 场景 | 任务 | 状态 | 模型动作 | 交付物 | 人工验收 |
| --- | --- | --- | --- | --- | --- |
| software | Task 1 会议室预约系统 | ✅ 完成 | 129 | `meeting-room-booking.html`（含仪表板） | 通过 |
| software | Task 2 使用率仪表板 | ✅ 完成 | 68 | `meeting-room-booking.html`（含仪表板） | 通过 |
| newsroom | Task 1 HKUST AI 专题报道 | ✅ 完成 | 71 | `hkust_ai_article_with_social.md` + 调研档案 | 通过 |
| newsroom | Task 2 AI 检测器事实核查 | ✅ 完成 | 66 | `ai_detector_factcheck.md` | 通过 |
| fund | Task 1 模拟投资方案 | ⏳ 未跑 | — | — | — |
| fund | Task 2 | ⏳ 未跑 | — | — | — |

```text
software : task_completion_rate 1.0 · subtask 1.0 · 9828.96 s · 人工等待 5424.19 s
newsroom : task_completion_rate 1.0 · subtask 1.0 · 9088.86 s · 人工等待 6573.88 s
```

**四个已跑任务全部经独立审查 + 人工验收通过**，无预算耗尽、无致命错误。

累计平台改动：`collab/cli.py`、`collab/engine.py`、`collab/prompts.py`、`collab/tools.py`、`collab/ui_check.py` 共 +301 行，测试从 112 项增加到 **123 项**（全部通过，1 项因 Windows 符号链接跳过）。

---

## 1. 场景一：software（会议室预约系统）

### 1.1 Task 1 结果

```text
Run r1: 129 actions / 360 · 11 tasks · task_completion_rate = 1.0
duration 3943.91 s · human_wait 1539.55 s · human_accepted = true
```

交付包 `runtime/software/export/delivery/r1/`：

| 文件 | Artifact | 大小 | 说明 |
| --- | --- | --- | --- |
| `meeting-room-booking.html` | `a5` | 10420 B | 最终预约系统（已修复 ID 冲突） |
| `test_report.md` | `a3` | 2942 B | QA 测试记录 |

任务树：

```text
t1  lead      根任务：预约系统（人类验收通过）
├── t2  dev   实现单页 HTML 预约系统
│   └── t7  qa    独立审查 t2
├── t3  qa   测试预约系统
│   └── t6  release  独立审查 t3
├── t4  dev   修复重复 HTML 文档结构
│   └── t5  qa    独立审查 t4
├── t9  dev   修复预订 ID 冲突缺陷
│   └── t10 qa    独立审查 t9
└── t8  release  独立审查 t1
    └── t11 release  复审 t1
```

### 1.2 人工验收发现的产品缺陷

#### 预订 ID 冲突（严重，已修复）

第一版交付被人工验收**驳回**。用 DOM 测试桩运行真实应用代码后发现：

```js
// 缺陷代码
id: Date.now().toString()
```

同一毫秒内创建的预订得到**相同 ID**，而 `cancelBooking(id)` 按 ID 过滤：

```text
连续预订 room1/room2/room3 → 3 条记录，仅 1 个不同 ID
取消其中一条                → 3 条全部被删除
```

用户影响：连续预订多个会议室后，取消任意一条会连带清空其余预订。

修复（artifact `a5`）：

```js
id: Date.now().toString() + '-' + Math.random().toString(36).substr(2, 9)
```

修复后复测 15/15 通过。

#### QA 测试的覆盖局限

QA 报告本身声明「Functional testing performed via code analysis due to Docker unavailability」，并把浏览器交互测试列为 limitation——这是**诚实披露**，但也说明仅靠代码审查无法覆盖 ID 生成这类运行时语义问题。人工验收阶段补充的行为测试才暴露该缺陷。

### 1.3 Task 2 结果（使用率仪表板）

Task 2 在同一群组内继承 Task 1 的产物、对话与角色记忆，由 lead 派生出 t13–t18。

交付包 `runtime/software/export/delivery/r2/`：`meeting-room-booking.html`（artifact `a7`，24597 B）。

**人工验收驳回一轮**，发现 4 个缺陷：

| # | 缺陷 | 严重性 |
| --- | --- | --- |
| 1 | **取消率是伪造的**：`calculateAnalytics()` 用 `Math.random() * 10` 生成「Cancellation Rate」，每次渲染都变 | 严重 |
| 2 | **缺少最忙日期指标**（任务明确要求 busiest days and times） | 中 |
| 3 | **缺少闲置房间识别**（任务明确要求 underused rooms） | 中 |
| 4 | **只有 last 30 days 预设**，缺 this week / month / quarter | 轻 |

缺陷 1 的根因在预约系统本身：`cancelBooking()` 只做数组过滤，`saveData()` 后**从未记录任何取消操作**，所以仪表板没有数据可用，只能编造。

修复（artifact `a7`）：`cancelBooking()` 现在把 `roomId`、`date`、`startTime`、`endTime`、`purpose`、`cancelledAt` 写入 localStorage 的 `meetingRoomCancellations`；`calculateAnalytics()` 从该记录读取并按日期范围过滤，`cancellationRate = totalCancellations / totalBookings * 100`，无取消时为 0。同时新增 **Busiest Day**、**Least Used Room** 卡片、**Day of Week Distribution** 图表，以及三个日期预设按钮。

修复后我复测 **18/18 通过**：1 次取消 + 2 条预订 → 取消率 50%；零取消 → 0 而非随机数；相同输入两次计算结果一致（确定性）；日期范围同时过滤预订与取消记录。

---

## 2. 场景二：newsroom（AI 专题报道 + 事实核查）

### 2.1 Task 1：HKUST AI 专题报道

交付包 `runtime/newsroom/export/delivery/r1/`：

| 文件 | Artifact | 大小 |
| --- | --- | --- |
| `hkust_ai_article_with_social.md` | `a4` | 7233 B |
| `hkust_ai_interaction_dossier.md` | `a2` | 7023 B |

协作链路：chief_editor 选角度并做初轮检索 → reporter 实地调研（7 个真实来源，全部 HKUST 官方域名）→ fact_checker 核查 → editor 撰写 → fact_checker 复核 → chief_editor 集成。

**人工验收驳回一轮**，发现 2 个缺陷：

| # | 缺陷 | 说明 |
| --- | --- | --- |
| 1 | **标题夸大 84% 数字** | 原文标题 `HKUST researchers query AI for literature, but 84% never click the sources`，读起来像 HKUST 自己的统计；实际该数字来自 Ai2 的全球 opt-in 数据集，且只适用于 ScholarQA 报告线程（正文已正确限定，标题未限定） |
| 2 | **社交摘要 218 字符**，超出 150–200 限制 | 超 18 字符 |

修复后（artifact `a4`）：标题改为 `In a global AI research dataset, 84% of literature reports never get clicked: Why HKUST scholars should verify sources`；社交摘要缩到 173 字符。

我复验结果：正文 928 词（区间 800–1000 内）、13 项关键数字全部命中、3 个读者分层章节齐全、4 位具名校友引语齐全、3 条来源 URL 带日期。

### 2.2 Task 2：AI 检测器事实核查

交付包 `runtime/newsroom/export/delivery/r2/`：`ai_detector_factcheck.md`（artifact `a9`，6772 B）。

**人工验收驳回两轮**，累计发现 7 处引用问题：

| # | 缺陷 | 性质 |
| --- | --- | --- |
| 1 | Liang 研究venue 写成 `ACL/IJEL` | 事实错误，实为 **Patterns**（Stanford HAI） |
| 2 | 本族语者误报率写成 `near-zero` | 低估问题，实为 **4–9%** |
| 3 | `19 of 39` 未标注来源性质 | 实为 **Programs.com 商业追踪页**，非同行评审 |
| 4 | Pangram ~98%、ZeroGPT 98.4% 未标为厂商声明 | 混淆厂商与独立证据 |
| 5 | Perkins 2024 无标题/期刊/链接 | 不可追溯 |
| 6 | Sources 列表仍写 `ACL/IJEL` | 与正文自相矛盾 |
| 7 | 缺具体 base rate 算例 | 读者无法理解「85% 不是判决」 |

第 2 轮驳回时，**正文改了但 Sources 列表没改**，导致文章自相矛盾——这说明单次驳回后必须逐项复核，不能只看摘要。

修复后我逐条核对源文本：

```text
192              → s18（Springer 全文，含 DOI 10.1007/s40979-026-00213-1）
0.61 / 0.69      → s18 / s13 / s17
61.22            → s19 / s22 / s24
85.3 → 26.4      → s8
31.3 → 17.3      → s8
6,000            → s21 / s18
750 / 97.8       → s21,s24,s25,s26,s27,s28 / s20,s22
```

**每一个数字都能追溯到具体源**，且文章明确区分了同行评审、商业聚合页与厂商声明三类来源。最终结论写得很直接：检测器无法单独可靠判定作者身份。

### 2.3 场景三：fund

尚未运行。任务为「25 岁、100 万港币的美股模拟投资方案」，需要产出配置理由、风险说明和一份可理解的新手介绍。

---

## 3. 平台代码修复

三次运行暴露了 **12 个真实平台缺陷**，全部修复并补充回归测试（112 → 123 项）。分类如下。

### 3.1 缺少产物发现能力（`collab/tools.py`）

**现象**：dev 写完文件但未发布，QA 无法知道该导入什么；QA 反复 `artifact.import` 猜测 ID，报裸 `KeyError: 'a5'`。

**修复**：新增 `artifact.list` 工具，列出已发布产物（ID、owner、path、描述、时间、sha256）；`artifact.import` 遇到未知 ID 时列出可用 ID 而非抛裸 `KeyError`。

### 3.2 等待中的同事收不到消息（`collab/engine.py`）

**现象**：QA 发消息「请 dev 发布文件」，dev 处于 `waiting_for_children`，永不处理；run 反复卡在「Repeated agent/tool errors」。

**根因**：`wait` 把任务挂起后，只在子任务完成时唤醒，**队友发来的消息不在唤醒条件内**。

**修复**：新增 `wake_recipient()`，收到消息的等待中任务立即恢复 `running`。

### 3.3 根任务无法提供证据（`collab/engine.py`、`collab/prompts.py`）

**现象**：根任务 t1/t12 由 lead 协调，本身不执行工具，因此 `current_task_evidence_index` 恒为空；模型只能**编造证据 ID**，反复触发「Evidence must belong to the current task」。

**修复（两处）**：

1. `validate_evidence(..., allow_review_evidence=True)`：根任务可引用其**整棵子树**中独立审查任务产生的工具结果（`is_review_of_own_child` + `descends_from`）。非根任务的证据隔离规则完全不变。
2. 上下文新增 `delegated_review_evidence_index`：主动把合法证据 ID（含 seq、review_task、reviewer）喂给根任务。

原有「审查不得复用其他任务证据」的规则和对应测试保持通过。

### 3.4 证据类型过窄（`collab/engine.py`、`collab/prompts.py`）

**现象**：newsroom 场景里 reporter 引用 `s3`/`s5`/`s7`（自己检索到的**来源 ID**）被拒「Invalid evidence event」；fact_checker 引用 `a2`（自己审查过的**产物 ID**）被拒。

**根因**：证据只接受整数事件序号。但对研究型任务，**来源 ID 才是真正的证据**；对审查者，**被审查的产物**才是真正的证据。

**修复**：`validate_evidence` 现在接受三种形式——整数 `tool_result` 序号、来源 ID（`s3`）、产物 ID（`a2`），三者都会校验是否真实存在；审查路径同步跳过对字符串 ID 的 `exit_code` 检查。

### 3.5 错误信息不可操作（`collab/engine.py`、`collab/tools.py`、`collab/prompts.py`）

模型在多处把工具名当成动作类型发出、把 `agent_action` 序号当证据、引用任务 ID 当证据、把一条验收标准拆成多条。这些都不是模型的「意图错误」，而是错误信息太笼统导致无法自我纠正。

**修复**：

| 场景 | 修复前 | 修复后 |
| --- | --- | --- |
| 动作类型写错 | `Unknown action type 'ui.check'` | 列出全部合法类型，并给出 `{"type":"tool","name":"artifact.list","args":{}}` 示例 |
| 引用不存在的产物 | `KeyError: 'a5'` | `Unknown artifact 'a5'. Published artifacts: a1, a2, a3. Call artifact.list...` |
| 引用 action 序号 | `Evidence must reference actual tool results` | 指出**具体哪个序号**是错的，并给出**正确的相邻 tool_result 序号** |
| 引用任务 ID | `Invalid evidence event` | `t3 is a task ID, not evidence. Cite the tool_result sequence IDs...` |
| 审查标准不匹配 | `Review must cover every acceptance criterion exactly as assigned` | 列出**多出的**和**缺失的**标准，并给出应复制的原文 |
| 证据类型不认识 | `Invalid evidence event` | 说明可用三种形式并举例 `s3` / `a2` |

### 3.6 HTML 拼接损坏无法检出（`collab/ui_check.py`）

**现象**：dev 用 `files.append` 分段写大文件时，模型输出在中途被截断又续写，产出 `const formData = {}}d === roomId && ...` 这类残缺内容。`node --check` 能抓到这个语法错误（拦住了发布），但**修复后残留的第二个完整文档副本无法被 JS 语法检查发现**。

**修复**：新增 `structural_warnings()`，检测重复的 `<html>`/`<body>`/`<head>` 元素与重复脚本；`require_valid()` 在发布和提交时拦截结构重复的 HTML。

该修复在场景一 Task 2 实跑中**当场生效**：dev 首次发布即被拦截，报 `HTML contains 2 scripts that both reference document; the file may have been concatenated twice.`

### 3.7 Windows 控制台因 Unicode 崩溃（`collab/cli.py`）

**现象**：lead 发消息含 `✅`，`print()` 触发 `'gbk' codec can't encode character '\u2705'`；newsroom 场景中又因 `'\u2011'`（不换行连字符）再次崩掉整个 run。

**修复**：新增 `safe_print()`，按流编码降级替换无法编码的字符；`status()`、`/export`、错误输出路径全部改用它。`status()` 之前用 `print(json.dumps(request, ensure_ascii=False))` 直接打印请求内容，是后一次崩溃的确切位置。

### 3.8 提交引用的产物不存在（`collab/engine.py`、`collab/tools.py`）

**现象**：dev 连试四次 `finish artifacts=['meeting-room-booking.html']`，每次报 `Unknown artifact`——它把**文件名**当成了**产物 ID**。

**修复**：报错信息明确区分二者，并提示先 `artifact.publish` 再引用其返回的 ID。

---

## 4. 环境配置变更

| 项 | 变更 | 原因 |
| --- | --- | --- |
| `.env` `TAVILY_API_KEY` | 从 shell 环境写入 | 搜索需真实密钥 |
| `.env` `LLM_REQUEST_TIMEOUT_SECONDS` | 120 → 240 | GLM 偶发超时会中断任务（实测一次 120s 总时限耗尽） |
| `~/.docker/daemon.json` | 新增 `registry-mirrors` | 本机无法直连 `auth.docker.io`（443 超时），改用 `docker.m.daocloud.io` 与 `docker.1panel.live` |

Docker 镜像源配置需**重启 Docker Desktop** 后生效；已验证生效：

```text
docker info --format "{{json .RegistryConfig.Mirrors}}"
["https://docker.m.daocloud.io/","https://docker.1panel.live/"]
```

`LLM_ALLOW_INSECURE_HTTP` 仍只从真实进程环境读取，每个新 shell 需设置一次。

---

## 5. 验证结果

| 检查 | 结果 |
| --- | --- |
| `python -m unittest discover -s tests` | **123 项通过**，1 项跳过（Windows 符号链接），0 失败 |
| `python scripts/run_checks.py` | Passed=122, skipped=1, failed=0 |
| `python -m collab doctor` | Docker / 镜像 / Node / 搜索 / 密钥 全部就绪 |
| `python scripts/check_runtime.py --search` | `runtime_checks_passed: True` |
| 预约系统行为测试（DOM 桩） | 15/15 通过 |
| 仪表板行为测试（DOM 桩） | 18/18 通过 |
| 报道文章指标核对 | 13 项数字全部命中，928 词，173 字符摘要 |
| 事实核查逐条溯源 | 全部数字可追溯到具体 source ID |

新增回归测试覆盖：

- 根交付可引用下属评审的证据
- 根交付仍拒绝无关任务的证据
- 消息可唤醒等待中的同事
- 证据可引用来源 ID 与产物 ID，未知 ID 被拒
- `Unknown action type` / `Unknown artifact` 错误信息包含可用 ID 与示例
- Windows GBK 控制台下的 `status()` 与 `safe_print()` 不崩溃

---

## 6. 人工验收方法

本轮 4 次人工验收全部基于**直接检查交付源码**，而非采信团队的总结：

1. **阅读产物原文**（HTML / Markdown），逐项比对任务要求
2. **搭建 DOM 测试桩**运行真实应用代码，验证行为而非阅读代码（发现 ID 冲突、伪造取消率）
3. **写脚本把文章里的每个数字回查源文本**，确认可追溯（发现 venue 错误、near-zero 夸大、来源性质混淆）
4. **计数校验**（词数、字符数），发现社交摘要超限
5. **检查文章自相矛盾**（正文改了、来源列表没改）

驳回时给出**可执行的修复说明**（具体改哪句、改成什么数值、附上正确数据），而非仅指出问题。

---

## 7. 遗留与边界

- 场景三（fund）两项任务**尚未运行**。
- 调度仍是**顺序队列**，不是多 Agent 真并行。
- 根任务的证据规则放宽仅限「根任务 + 独立评审任务」这一条路径；普通任务与其他任务的证据隔离未放松。
- HTML 结构检查是启发式的（计数重复外壳元素），不替代浏览器测试。
- `LLM_ALLOW_INSECURE_HTTP` 每次新 shell 都需手动设置，容易遗漏。
- 本轮动作数偏高（software 197 次 / newsroom 137 次），相当比例来自人工 `answer`/`resume` 推进；§3.3–3.5 的改动正是为减少这类干预而做，效果需下一轮验证。
- 真实模型输出质量、浏览器人工验收与课程最终评分仍以教师口径为准。

---

## 8. 相关文档

- [成员 A 工作与分支报告](member-a-work-report.md)
- [架构与边界](architecture.md)
- [Agent Orchestration 集成契约](orchestration-contract.md)
- [六项任务验收清单](benchmark.md)
- [评测协议](evaluation.md)

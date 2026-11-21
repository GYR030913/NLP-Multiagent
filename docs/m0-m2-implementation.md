# M0–M2 实施与验证记录

更新时间：2026-11-21。代码分支：`codex/m0-m2-harness`；起点：`42db55a`，当前工作区包含尚未提交的实现。本文记录实际状态，不替代人类验收。

## M0 基线

已完成原状态/产物/记忆备份及哈希记录，位置由 `runtime/_milestones/latest-m0.txt` 指向。基线原有 105 项测试：104 通过、1 项 Windows 符号链接测试跳过。课程模型 curl 请求成功，真实 Docker 隔离、默认断网、无密钥注入及真实搜索检查通过。

没有填写或假定课程截止时间及货币费用上限；使用现有每目标 360 次调度动作上限，网络尝试另行记录，费用未知不记为零。正式六题/隐藏测试不用于本次工具调参。小型 UI 正反例属于开发控制用例。

原 `runtime/software` 保留 schema v1；迁移在 `runtime/development_m2/software` 副本进行，并单独保存原 state 哈希备份。新增开发过程不覆盖原会话，也不计作正式六题成绩。

## M1 已实现

- schema v2 任务输入/输出、能力、工具权限、skill 版本、显式子预算、交付键和 revision；依赖/权限/预算校验先于节点创建。
- 子预算预留及祖先计数，根预算持久化；模型调用前先保存预算预留，避免中断返还已尝试调用的预算。
- `/resume` 默认 30 步、`/resume N`、`/resume --continuous` 共用一个入口。已有或新增人工请求都会停止；没有 `/run` 命令，重启不自动连续执行。
- 显式停止原因、执行模式、剩余预算、活跃状态及下一步；无进展和错误有界停止。
- 工具注册、参数及任务权限校验；审批与具体动作/revision 摘要绑定；网络尝试与动作/返回 token 分开记录。
- 五个带版本和内容哈希的 runtime skills。实际存于 `collab/runtime_skills/` 并作为 Python package data 分发；不是 Codex 宿主技能。
- 私有任务使用独立 workspace/记忆路径，公共上下文不召回私信派生内容。含旧私有任务的 v1 工作区因缺乏可靠文件归属，迁移 dry-run 会提示必须先人工分离，正式迁移默认拒绝，避免泄漏。
- `migrate --dry-run`、备份后迁移、未知 schema 拒写及关系校验。长期记忆治理、短期压缩和自进化属于 M3 以后，未在本阶段冒充完成。

## M2 工具与实测

已实现 `artifact.publish_bundle/import_bundle/validate_bundle`，保留单文件 API；集合逐文件校验，导入先准备完整目录再原子切换，不回退到半完成的递归复制。Windows 短暂文件占用有有限重试。格式不支持、缺文件、坏哈希或 JS 语法错误不会暴露部分集合。

已实现 `browser.verify`：固定版本的 Playwright/Chromium 独立镜像，在断网容器中运行应用与声明式浏览器检查。真实 UI 正例通过；语法合法但按钮调用不存在函数的反例被拒绝。负例中的预期 HTTP 错误必须按路径和状态显式声明，仍需断言可见错误提示，不能全局忽略控制台错误。

已实现本机预览租约：只读交付源码、独立持久数据目录、127.0.0.1 端口、非 root、资源限制、自动过期及清理。相同 data_key 跨版本保留数据，一次只允许一个活跃预览写入该数据。真实检查已验证本机 HTTP 可访问、应用主动 TCP/UDP 外连被拒、模型密钥未注入、租约到期容器消失。

Docker Desktop 26 对 internal bridge 的端口发布无法正常访问，因此预览使用普通专用 bridge，加由可信监督进程对子进程叠加的 seccomp 出站限制；保留 Docker 默认 seccomp、只读根目录及 capabilities 限制。仅支持已验证的 x86_64/aarch64 配置，安装过滤器失败时关闭预览，不降级为任意联网执行。浏览器验证始终使用 `--network none`。

证据：`runtime/development_m2/_browser_controls/verification.json`；完整离线结果：`runtime/verification.md`。目前已运行 129 项离线测试，128 通过、1 跳过、0 失败。

## 软件任务的真实开发验证

独立副本已经运行计划、开发、QA、根审查和批量交付。第一份交付通过浏览器预约、冲突拒绝和取消测试，但补充的首次并发测试发现 20 个请求产生了 7 个 HTTP 500。

该失败完整保留在 `concurrency-verification.json`。原验收请求 q1 被标记为 `invalidated`，没有填写人类批准或拒绝；任务恢复修复。修复版 b2 已由独立脚本确认：20 个并发请求为 1 个 201 和 19 个 409，取消重订成功，重新打开数据库保留取消历史。该结果只适用于对应不可变 bundle，不自动等于后续版本已通过。

并发修复版本 **b3** 已通过独立并发复验（`concurrency-b3.json`）。用户随后要求统一房间名称显示；当前选定交付为 **b4（1.0.3）**，SHA256 为 `77b868ebbad12ca01404f01022a4fb4fdc0dd9c808da46db1650b2185b541c11`，仅修改 `meeting_rooms/static/app.js`，使 Bookings 与房间列表、可用性查询统一显示房间名称，后端与测试文件和 b3 字节一致。

b4 的隔离浏览器检查通过 38 个操作/断言，覆盖四个房间的名称、新建、刷新、取消和重新打开页面；开发团队及独立 QA 也已复查 b4，原有 26 项软件测试通过。本机预览更新前后，原有 5 条预约的完整行摘要一致。证据位于 `runtime/development_m2/software/operator-fixes/room-labels/` 及群组事件中。q1、q2 保留失效记录，随后已依据用户对当前房间名称修复版本的明确确认，将 **q3/b4 记为已验收**；此前 q2 的替代关系保留。

房间名称修复后的交付审查已完成，连续模式曾自动停在 q3；r1 累计 226/360 个动作（包含原始运行历史）。用户随后在继续 M3/M4 时确认预约系统已通过，已将该确认记录到当前 q3/b4，保留此前 q2 的替代关系。开发群组 Task 2 尚未开始；后续六题自主测试另用隔离目录，不能混算成该群组的人类验收。**不能把 M2 工具已实现理解成软件两题均已完成。**

## 使用方式

在项目根目录运行；以下命令不覆盖原 `runtime/software`：

```powershell
python -m unittest discover -s tests -v
python scripts/check_runtime.py --search
docker build -f Dockerfile.browser -t campus-collab-browser:local .
python scripts/check_m2.py
python -m collab --runtime runtime/development_m2 status software
python -m collab --runtime runtime/development_m2 chat software
```

进入聊天后使用 `/resume 30` 或 `/resume --continuous`。出现人工请求先处理请求；停止原因显示预算耗尽时，重复 `/resume` 不会增加预算。

旧目录要先在副本查看迁移结果：

```powershell
python -m collab --runtime YOUR_COPIED_RUNTIME migrate software --dry-run
python -m collab --runtime YOUR_COPIED_RUNTIME migrate software
```

独立并发检查针对选定交付：

```powershell
python scripts/check_software_concurrency.py --bundle b4
```

本机预览使用当前选定的 b4：

```powershell
python -m collab --runtime runtime/development_m2 preview software --bundle b4 --port 8000 --lease-seconds 3600 --argv python -m meeting_rooms.web --host 0.0.0.0 --port 8000 --db /data/bookings.db
python -m collab --runtime runtime/development_m2 preview software --stop RETURNED_LEASE_ID
```

本机预览不会批准验收。默认数据位于开发群组的 `preview-data/default/bookings.db`，不与原始运行数据库混用。修改 bundle 或入口后需重新检查，不能用早期版本证据替代。

Task 1 人类验收后，可由操作员显式将一致性数据快照交给 Task 2 的开发/QA 角色：

```powershell
python -m collab --runtime runtime/development_m2 preview software --snapshot default --to-role dev
python -m collab --runtime runtime/development_m2 preview software --snapshot default --to-role qa
```

此管理操作使用 SQLite backup，保存哈希及清单，把只读副本放进指定角色的 `inputs/`，不把实时数据库挂载给生成代码，不发布原始个人数据，也不会由 Agent 静默执行。随后在同一 software 群组提交 Task 2。通用数据登记/分析工具仍属于 M4；本阶段仅提供经过明确操作员调用的数据交接。

## 仍需完成的阶段出口

- 预约系统 b4 已通过测试、浏览器检查、独立审查及用户确认；完整记忆/研究扩展与六题自测见 [M3/M4 实施记录](m3-m4-implementation.md)。
- 在相同开发群组执行 Task 2，基于实际预约记录完成看板、独立复算、浏览器与人类验收。
- 汇总最终选择的镜像、skill、产物、数据摘要和成本记录。

M0–M1 的工程基线与实现可验证；M2 的工具实现和控制用例已验证，软件两题场景闭环尚不能标为全部完成。

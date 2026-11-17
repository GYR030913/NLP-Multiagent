# 成员 A 工作与分支报告

## 1. 分支定位

- 长期维护分支：`feature/member-a-orchestration`
- 当前远端基线：PR4 已合并后的 `main`，提交 `d569ca1`

本分支继续以追加提交的方式维护，不通过新建快照分支重复保存同一项工作。PR1 的 macOS 路径修复、PR2 的评测能力、PR3 的成员 A 编排能力和 PR4 的整合修复都保留在提交历史中。

## 2. 成员 A 的职责范围

成员 A 负责多智能体系统的控制面和模型连接可靠性：

- Agent 身份、群聊、私信和上下文可见范围。
- 任务计划、验收条件、动态委派、子任务依赖和父任务等待。
- 独立审查、拒绝返工、失败传播、预算限制和重启恢复。
- 与工具、记忆、人类交互和评测模块之间的稳定接口。
- 同一群组中后续任务复用前序任务上下文和产物。
- HKGAI 模型请求的端点、超时、TLS、鉴权和错误处理。

## 3. 已完成的编排能力

### 3.1 任务状态机

- 根工作任务必须先提交非空且不重复的验收条件，之后才能使用工具、委派或提交。
- 被委派任务可以继续生成多级子任务，同时限制最大深度、任务数和模型动作数。
- 依赖关系必须先完成校验再写入，拒绝父子回指、直接循环和多节点循环。
- 父任务必须等待直接子任务完成审查，不能跳过子任务直接结束。
- 审查节点占用任务预算，不能通过反复创建审查任务突破上限。
- 每个运行使用持久化动作预算，恢复和继续执行不会重置累计次数。

### 3.2 审查与返工

- 执行者不能审查自己的任务。
- 每条验收条件都必须有当前审查任务、当前审查者实际取得的工具证据。
- 不能复用其他任务或历史审查中的旧证据。
- 拒绝必须提供反馈；被拒绝任务重新进入执行状态。
- 达到返工上限后明确失败，不把返工成功或人工拒绝前的状态计为完成。

### 3.3 通信和队友接口

- 群聊对相关角色可见；私信只进入发送者和指定接收者上下文。
- 私有任务禁止向群聊转发、继续委派、发布或导入公共产物。
- `collab/contracts.py` 固定工具、记忆、人类交互和消息信封的最小接口。
- 现有工具、记忆和引擎实现通过结构兼容性测试，队友可以替换内部实现，但不能静默改变字段语义。

### 3.4 上下文复用和恢复

- 同一群组的后续任务可以读取前序任务产生的公开对话、产物、记忆和事件。
- 状态通过临时文件原子替换保存。
- 重载时恢复遗漏在队列外的运行任务，并避开依赖、子任务、审查和人工等待状态。
- 只对已持久化状态提供恢复；外部副作用发生时进程崩溃，仍应先检查外部系统再决定是否重试。

### 3.5 模型连接可靠性

- `LLM_TRANSPORT=auto|curl|urllib` 三种选择。
- Windows Python 3.11+ 在 `auto` 模式下优先使用系统 curl/Schannel，避开 Python OpenSSL 与 HKGAI 端点的 TLS 握手停滞。
- 自动传输改为双向回退：curl 连接失败可尝试 urllib，urllib 连接失败也可尝试 curl。
- HTTP 4xx/5xx 属于服务响应，不会因为自动回退而重复发出同一请求。
- 回复体大小、连接超时和总请求时限均有上限。
- curl 禁止重定向并验证返回状态。
- 鉴权值通过子进程环境传给 curl，不进入命令行参数、日志或异常消息。
- urllib 请求不在跨域重定向时携带鉴权头。
- 成功请求记录实际使用的传输方式，供 `scripts/probe_api.py` 输出诊断信息。

## 4. 验证结果

当前分支的 Python 3.11 离线验证：

- 执行 105 项测试。
- 通过 104 项。
- Windows 符号链接测试跳过 1 项。
- 失败 0 项。

新增测试覆盖：

- Windows Python 3.11 的 curl 优先顺序。
- curl 失败后回退 urllib。
- urllib 失败后回退 curl。
- HTTP 鉴权或限流错误不触发无意义回退。
- 凭据不进入 curl 命令行。
- 超时、断连、截断、非法 JSON、端点协议和密钥缺失。

测试中的模型和 Docker 响应主要使用假对象，只能证明协议、状态机和边界，不代表真实模型任务质量。真实运行记录由本地 `runtime/` 保存并保持忽略。

## 5. 交付清单

| 交付项 | 状态 | 位置 |
| --- | --- | --- |
| 多级委派、依赖、父任务等待 | 已完成 | `collab/engine.py` |
| 独立审查、拒绝返工和预算 | 已完成 | `collab/engine.py` |
| B/C/D 最小接口契约 | 已完成 | `collab/contracts.py` |
| 群聊、私信和私有任务隔离 | 已完成 | `collab/engine.py` |
| HKGAI TLS 自动选择和双向回退 | 已完成 | `collab/llm.py` |
| 重新加载和运行级动作预算 | 已完成 | `collab/store.py`、`collab/engine.py` |
| 评测和指标导出 | 已完成 | `collab/report.py`、`scripts/evaluate_benchmark.py` |
| 成员 A 专项回归 | 已完成 | `tests/test_member_a_orchestration.py`、`tests/test_transport.py` |
| 公开文档收敛 | 已完成 | 本报告 |

## 6. 团队使用方式

安装 Python 3.11+、Node.js 和 Docker Desktop，然后：

```powershell
cd D:\Projects\NLP-Multiagent
python -m unittest discover -s tests -v
python scripts\probe_api.py
python scripts\check_runtime.py --search
python scripts\prepare_groups.py
python -m collab chat software
```

其他成员使用自己的 Python 路径、`.env`、Docker 和课程额度，不需要复制成员 A 的本地运行目录。

当前接口配置：

```dotenv
LLM_PROVIDER=hkgai
LLM_BASE_URL=https://api1.hkgai.net/v1
LLM_MODEL=t2_hkgai-v3_fp8_1m_e7
LLM_API_KEY=在本地填写
LLM_AUTH_HEADER=Authorization
LLM_TRANSPORT=auto
```

`.env`、`runtime/`、真实对话、数据库和本地记忆不得提交。

## 7. 相关团队文档

- [系统架构与边界](architecture.md)
- [Agent Orchestration 集成契约](orchestration-contract.md)
- [六项任务验收清单](benchmark.md)
- [评测协议](evaluation.md)

## 8. 已知边界

- 当前调度是顺序队列，不是多个 Agent 真正并行执行。
- Agent 能力主要由场景提示词和委派选择表达，尚无独立机器能力清单。
- 重启恢复不提供外部副作用的精确一次保证。
- 离线测试不能代替真实模型输出质量、浏览器人工验收或最终隐藏测试集评分。
- GitHub 推送仍需拥有仓库权限的账号和明确人工批准。

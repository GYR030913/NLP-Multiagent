# 成员 A 分支说明

分支：`feature/member-a-orchestration`

更新时间：2026-11-17

本文给第一次打开本分支的队友说明：成员A改了什么、为什么改、如何验证，以及合并前需要注意什么。

## 分支关系

- 本分支从最新远端`origin/main`的`13026ae`提交创建，已包含该提交之前的协作改动。
- 远端`feat/evaluation`也从同一`main`基线向前开发，和本分支目前互不包含。
- 远端`fix/macos-path-validation`已通过PR合并到`main`。
- 本分支目前尚未推送到GitHub。
- 合并前应重新`git fetch origin`并同步最新`main`，然后重新运行测试。

## 成员A负责的范围

成员A负责多智能体系统的控制面，而不是B、C、D各模块的全部内部实现：

- Agent角色之间的群聊、私信和可见范围。
- 高层任务计划、验收条件、动态委派和多级子任务。
- 依赖等待、自动唤醒、父任务完成约束和自审拦截。
- 独立审查、拒绝返工、失败传播和进程重启恢复。
- 委派深度、任务数量、返工次数和模型动作预算。
- 与B工具、C记忆、D人类交互/评测之间的稳定接口。
- Task 2在同一群组中复用Task 1上下文与产物。

## 本分支相对main的主要改动

### 编排正确性

- 根工作任务必须先提交非空且不重复的验收条件，之后才能用工具、委派或提交。
- 审查证据必须属于当前审查任务并由当前审查者获取，禁止引用上一次审查的旧证据。
- 审查节点也占用任务预算，不能通过反复创建审查任务突破`max_tasks`。
- 每个运行新增持久化的`LLM_MAX_RUN_ACTIONS`总预算，默认360；`/resume`不会重置。
- 拒绝三次返工后明确失败，不计入成功。

### 成员接口

新增`collab/contracts.py`，固定：

- `ToolGateway`：B的工具执行、审批需求与产物验证。
- `MemoryGateway`：C的召回、经验写入和记忆快照。
- `HumanInteractionGateway`：D的澄清、审批、验收请求和回答。
- `MessageEnvelope`：群聊、私信、任务归属与发送者/接收者字段。

字段和行为说明见[编排集成契约](orchestration-contract.md)。

### 模型传输

当前HKGAI端点在Windows Python 3.11下会与Python OpenSSL卡在TLS握手。Windows curl使用系统Schannel可正常连接，因此：

- `LLM_TRANSPORT=auto`在Windows Python 3.11+优先使用curl。
- 认证值通过子进程环境传入，不出现在curl命令行、日志或异常中。
- Python 3.10/支持urllib的环境仍可继续使用原传输。

### 测试与文档

- 新增成员A专项测试，覆盖计划、委派、深度、依赖、私密消息、审查返工、预算、恢复和Task 2复用。
- 新增接口兼容性测试。
- 更新能力审计、架构、状态和离线验证记录。

## 验证结果

截至本说明：

- Python 3.11.15离线测试：87项，86通过，1项Windows符号链接测试跳过，0失败。
- HKGAI最小探测：`connection_ok: true`，传输为`curl`。
- Docker沙箱、默认断网、密钥隔离和真实搜索：通过。
- 软件Task 1：人工拒绝一次后返工通过，`r1`完成，6/6工作子任务。
- 软件Task 2：同一群组复用Task 1上下文和数据库，`r2`完成，3/3工作子任务。
- 软件场景群聊目标完成率：2/2 = 1.0。

详细证据：

- [成员A集成验证](member-a-verification.md)
- [能力审计](orchestration-audit.md)
- [离线测试记录](verification.md)
- [当前状态](status.md)

软件运行记录和生成产物位于被Git忽略的`runtime/`，不会随分支上传。其他成员需要使用自己的`.env`、HKGAI额度、Docker和人工审批重新运行。

## 本地验证方法

在Python 3.11+环境运行：

```powershell
cd D:\Projects\NLP-Multiagent
python -m unittest discover -s tests -v
python scripts\probe_api.py
python scripts\check_runtime.py --search
```

预期：

- 87项测试全部通过，仅允许Windows符号链接测试跳过。
- HKGAI探测返回`connection_ok: true`。
- Docker检查返回`runtime_checks_passed: True`。

如果Windows Python 3.11下`probe_api.py`卡住，应为它选择自动curl传输，或显式设置：

```dotenv
LLM_TRANSPORT=curl
```

## 对其他成员的影响

本分支可以独立推送，不影响`main`或其他成员的本地工作。合并前请注意这些行为变化：

1. 根任务不能跳过`plan`。
2. 审查证据只能在当前审查任务中获取。
3. 任务预算包含审查节点。
4. `/resume`不能重置累计动作预算。
5. Windows Python 3.11会使用curl作为HKGAI传输。

现有工具、记忆和人类交互类都通过结构契约测试，但B/C/D若重命名接口或返回非JSON数据，需要同步更新适配层。

## 已知限制

- 调度仍是顺序队列，不是真正并行执行多个Agent。
- Agent能力主要由场景提示词和委派选择表达，尚无独立的机器能力清单。
- 重启恢复不提供外部副作用的精确一次保证。
- 新闻、基金和最终隐藏测试集不属于本次软件场景完成率。
- 真实模型输出质量仍需人工验收，离线测试不能替代内容质量判断。

## 合并前检查清单

```powershell
git fetch origin
git rebase origin/main
python -m unittest discover -s tests -v
python scripts\probe_api.py
git status
git diff --check
```

全部通过后，推送本分支并创建Draft PR。PR描述可直接引用本文，并附测试数量、真实软件场景结果和仍然依赖B/C/D的事项。

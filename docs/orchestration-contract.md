# Agent Orchestration 集成契约

本契约固定成员 A 与 B（工具）、C（记忆）、D（人类交互和评测）之间的最小接口。实现可以替换，但字段和语义必须有迁移说明。

## Agent 与消息

`state["roles"]` 以稳定 Agent ID 为键。每个角色至少有：

```json
{
  "title": "Technical Lead",
  "prompt": "Original scenario role prompt"
}
```

角色能力由场景提示词和协商共同决定。Agent ID 必须属于该映射；委派、私信和任务负责人都不能使用未知 ID。

群聊和私信事件使用 `MessageEnvelope`：

```json
{
  "seq": 12,
  "time": "2026-11-17T00:00:00+00:00",
  "kind": "message",
  "sender": "lead",
  "to": "dev",
  "task": "t1",
  "text": "Please verify the database constraint."
}
```

`to="group"` 表示群聊；其他值表示私信。私信只进入发送方和指定接收方的上下文。私有任务禁止向群聊转发、继续委派、发布或导入公共产物。

## 任务和状态

任务由以下稳定字段组成：

- `id`、`owner`、`description`、`criteria`、`run`。
- `parent`、`children`、`depends_on`、`depth`。
- `kind`：`work` 或 `review`。
- `status`、`attempt`、`created_at`、`submission`。

合法状态为：

```text
running
waiting_for_children
waiting_for_dependencies
waiting_for_human
reviewing
completed
failed
```

根工作任务必须先通过 `plan` 提交非空且不重复的验收条件，之后才能使用工具、委派或提交。被委派任务在创建时必须已有验收条件。依赖只在工作子任务经审查完成后唤醒。

重启恢复以任务状态中已经持久化的 `planned` 标记为准。`agent_action` 只记录模型尝试，可能对应校验失败的计划，不能据此将任务提升为已计划。只有实际通过 `plan` 校验才写入 `planned=True`；缺少该标记的旧状态按未计划处理。

## A 调用 B：ToolGateway

结构化协议位于 `collab/contracts.py`，最小调用为：

```python
tool_gateway.execute(state, agent_id, tool_name, arguments)
```

返回值必须可JSON序列化，并作为 `tool_result` 事件持久化。工具失败必须返回明确失败或抛出异常，不能伪装成成功。`needs_approval(name)` 为真时，A 只能创建人工审批请求，不能在审批前执行。`validate_artifacts(state, ids)` 必须在交付和人工验收前验证哈希及版本一致性。

## A 调用 C：MemoryGateway

```python
memory_gateway.recall(agent_id, query, limit=6)
memory_gateway.remember(agent_id, lesson, task_id, outcome)
memory_gateway.snapshot()
```

`recall` 只能读取指定 Agent 的记忆。`remember` 写入的 `outcome` 至少区分 `peer_approved`、`human_accepted`、`review_rejected` 和 `human_rejected`。`snapshot()` 返回记忆文件到SHA256的映射，用于记录一次运行前后的变化。

## A 调用 D：HumanInteractionGateway

```python
gateway.request(task, kind, **payload) -> request_id
gateway.answer(request_id, text="", approved=None)
```

`kind` 支持 `clarification`、`approval` 和 `acceptance`。审批与验收必须使用显式布尔值；普通文本不能代替批准。等待人工时只记录 `waiting_for_human`，不能在后台继续执行当前动作。

CLI、评测导出和报告属于 D 的适配层，可以消费 `state` 和事件，但不得直接修改任务状态。

## 证据

审查通过时，每条验收条件都必须覆盖，并提供真实 `tool_result` 证据。证据必须：

- 属于当前审查任务；
- 由当前审查 Agent 获取；
- 没有失败退出码或验证警告。

完成工作的提交证据同样必须属于当前工作任务。其他任务或历史审查中的证据无效。该规则只能证明证据来源，不能替代人工对语义质量和真实体验的判断。

## 预算

- 每个根任务先计划一次。
- 每轮目标最多 `max_tasks` 个节点，审查节点也占预算。
- 委派深度最多 `max_depth`。
- 单次 `run()` 最多 `max_steps` 个动作。
- 整个运行最多 `max_run_actions` 个动作；恢复不会重置累计数。

达到预算、审查返工上限或未捕获异常时，任务或运行必须明确标记失败或预算耗尽，不能计为成功。

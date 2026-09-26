# Fay 记忆机制总览（2026-04-27）

> 本文档描述 Fay 当前默认的认知记忆机制。涉及代码：
> `llm/nlp_cognitive_stream.py` + `genagents/modules/memory_stream.py`
> + `core/memory_service.py` + `core/member_db.py` + `faymcp/mcp_server.py`。
>
> 旧版精简说明见 [`memory_module.md`](./memory_module.md)，本文为增补与扩展。

---

## 一、三层存储

| 存储 | 路径 | 内容 | 隔离方式 |
|---|---|---|---|
| **memory_stream**（核心） | `memory/[<username>/]memory_stream/nodes.json` + `embeddings.json` | 所有事件、对话、反思节点 | `config.memory.isolate_by_user=true` 时按 username 分目录，否则全局共享 |
| **scratch**（人物属性） | 内存里的 `agent.scratch` dict，启动时从 `config.json` 的 `attribute` 段加载 | 姓名、年龄、性别、性格、目标等 Fay 自身的"角色卡" | 不持久化，每次启动从 config 重新装填 |
| **T_Member**（用户档案） | `memory/user_profiles.db` 的 sqlite 表 | `username`、`extra_info`（人工补充）、`user_portrait`（每天 22:35 自动生成的画像） | 一行一个用户 |

ChromaDB 仅用于本地知识库（doc 检索），不参与记忆流。

---

## 二、节点的数据结构

每个 `ConceptNode` 包含：

```
node_id, node_type, content, importance, datetime,
created (time_step), last_retrieved, pointer_id,
tags  ← 业务标签列表
```

`node_type` 只有三种：`observation` / `conversation` / `reflection`。

`tags` 走命名空间约定：

```
kind:<decision|event|fact|rule|error|insight|preference|observation>
source:<fay_self|claude_code|cursor|user|fay_reflection|...>
persistent:true              ← 长期保留标志，rule 必带
domain:<quant|homecare|education|life_assistant|home_automation|companion|...>
strategy:<策略名>  symbol:<标的>  session:<会话ID>  schedule:<表达式>  date:<YYYY-MM-DD>
```

### 2.1 节点完整事例

#### 事例 A：普通对话（conversation，无 tag）

```json
{
  "node_id": 142,
  "node_type": "conversation",
  "content": "主人：今天股市怎么样？\nFay：A股震荡走低，沪指跌 0.8%。",
  "importance": 4,
  "datetime": "2026/04/27 10:23:15",
  "created": 142,
  "last_retrieved": 142,
  "pointer_id": null,
  "tags": []
}
```

> 当前对话路径暂未打 tag，反思阶段无法从中继承业务标签。

#### 事例 B：外部 agent 写入的事件（event）

```json
{
  "node_id": 156,
  "node_type": "observation",
  "content": "上午 10:30 AAPL 突破日内高点 187.4，breakout 策略买入信号触发",
  "importance": 7,
  "datetime": "2026/04/27 10:30:42",
  "created": 156,
  "last_retrieved": 156,
  "pointer_id": null,
  "tags": [
    "domain:quant",
    "kind:event",
    "session:2026-04-27-am",
    "source:claude_code",
    "strategy:breakout",
    "symbol:AAPL"
  ]
}
```

#### 事例 C：长期规则（rule，必带 persistent:true）

```json
{
  "node_id": 88,
  "node_type": "observation",
  "content": "每小时整点检查所有持仓策略是否有未触发的止损单",
  "importance": 9,
  "datetime": "2026/04/26 09:00:00",
  "created": 88,
  "last_retrieved": 130,
  "pointer_id": null,
  "tags": [
    "domain:quant",
    "kind:rule",
    "persistent:true",
    "schedule:hourly",
    "source:user"
  ]
}
```

#### 事例 D：用户偏好（preference）

```json
{
  "node_id": 31,
  "node_type": "observation",
  "content": "用户希望晚上 22:00 之后不要主动发起对话",
  "importance": 8,
  "datetime": "2026/04/15 22:13:05",
  "created": 31,
  "last_retrieved": 105,
  "pointer_id": null,
  "tags": [
    "domain:life_assistant",
    "kind:preference",
    "persistent:true",
    "source:user"
  ]
}
```

#### 事例 E：失败事件（error）

```json
{
  "node_id": 173,
  "node_type": "observation",
  "content": "买入信号在 AAPL 涨停封板后仍触发，导致 0 成交但占用了风控额度",
  "importance": 8,
  "datetime": "2026/04/27 14:02:11",
  "created": 173,
  "last_retrieved": 173,
  "pointer_id": null,
  "tags": [
    "domain:quant",
    "kind:error",
    "session:2026-04-27-pm",
    "source:claude_code",
    "strategy:breakout",
    "symbol:AAPL"
  ]
}
```

#### 事例 F：居家养老观察（event，跨场景示例）

```json
{
  "node_id": 412,
  "node_type": "observation",
  "content": "老人晨起血压 158/95，比上周平均高 12 个点",
  "importance": 7,
  "datetime": "2026/04/27 07:15:00",
  "created": 412,
  "last_retrieved": 412,
  "pointer_id": null,
  "tags": [
    "domain:homecare",
    "kind:event",
    "source:elderly_monitor",
    "date:2026-04-27"
  ]
}
```

#### 事例 G：Fay 自动生成的反思（reflection，继承+追加 tag）

```json
{
  "node_id": 201,
  "node_type": "reflection",
  "content": "用户在 AAPL 上的 breakout 策略本周 3 次假突破亏损，可能需要加 ATR 滤波或限制涨停后入场",
  "importance": 8,
  "datetime": "2026/04/27 23:00:14",
  "created": 201,
  "last_retrieved": 201,
  "pointer_id": [156, 162, 173],
  "tags": [
    "domain:quant",
    "kind:insight",
    "source:fay_reflection",
    "strategy:breakout",
    "symbol:AAPL"
  ]
}
```

> 反思节点的 tag 由 `MemoryStream.reflect()` 自动从 `pointer_id` 指向的源节点继承（去掉 `session:`、`date:`、`schedule:` 前缀），再覆盖为 `kind:insight` + `source:fay_reflection`。

---

## 三、写入路径（共 4 条）

| 触发 | 函数 | 节点类型 | 是否打 tag |
|---|---|---|---|
| 用户每说一句话 | `remember_conversation_thread` | conversation | ❌ 暂未打 |
| 主动观察（API/前端调） | `record_observation` → `remember_observation_thread` | observation | ❌ 暂未打 |
| 外部 agent / Fay 自身 | `core.memory_service.remember()` | observation（默认） | ✅ 自动 normalize |
| 每晚 23:00 反思 | `perform_daily_reflection` → `MemoryStream.reflect()` | reflection | ✅ 继承源节点 tag + 自动加 `kind:insight`、`source:fay_reflection` |

写入的统一管线：
1. **锁外** 算 importance（LLM 评分 0–10）和 embedding（API 向量）
2. **持锁** `agent_lock`，把节点 append 到 `memory_stream.seq_nodes`，并写 `embeddings[content]`
3. 内部 4 条只更新内存；只有 `core.memory_service.remember` 会立即落盘
4. 每天 00:00 `save_agent_memory` 全量 dump 一次 nodes.json + embeddings.json

---

## 四、检索路径

唯一入口：`MemoryStream.retrieve(focal_points, time_step, ...)`。

打分公式：
```
score = recency_w · 衰减(last_retrieved)
      + relevance_w · cosine(query_embedding, node_embedding)
      + importance_w · normalized(importance)
```

默认权重 `[0, 1, 0.5]`（纯相关度+重要度），但**对话流**用的是 `[0.8, 0.5, 0.5]`（加重时间权重）。

新增 tag 过滤：`filter_tags_all`（AND）/ `filter_tags_any`（OR）。

**对话时拼提示词的过程**（见 `nlp_cognitive_stream.py` 2329 行附近）：
1. 用当前用户输入做 query
2. 一次 retrieve 拉 30 条候选
3. 按 `node_type` 分成三段：观察记忆 / 对话记忆 / 反思记忆，每段最多 10 条
4. 拼成 markdown，塞进系统 prompt 的 `memory_context`

---

## 五、定时任务（`init_memory_scheduler`）

| 时间 | 任务 | 作用 |
|---|---|---|
| 00:00 | `save_agent_memory` | 把内存里的 nodes/embeddings/scratch 全量落盘 |
| 11:30（注释说正式应改 22:35） | `perform_user_portrait_analysis` | LLM 读最近对话，更新 `T_Member.user_portrait`（含"与 Fay 的关系"维度） |
| 23:00 | `perform_daily_reflection` | 抽取热门主题做反思，生成 reflection 节点 |

启动时还会：
- `precheck_embedding_dimensions` — 修复维度不一致的旧 embedding
- `create_agent` 默认 username 创建主 agent

---

## 六、对外接口

| 调用方 | 接口 | 用途 |
|---|---|---|
| Fay 内部对话流 | 直接调 `agent.memory_stream` + `remember_*_thread` | 高频路径，绕过 service 层 |
| Flask `/api/observation` 等 | `record_observation` | 外部 HTTP 写入观察 |
| **MCP** | `faymcp/mcp_server.py`（SSE，端口 8765） | 暴露 7 个 `memory_*` 工具，进程内直调 `core.memory_service` |
| `core.memory_service` | 7 个函数：`remember / search / get_recent / get_active_rules / get_reflections / get_user_profile / get_schema` | 唯一权威 API，统一 tag 规范 + 立即落盘 |

MCP 的 7 个工具：
- `memory_remember` — 写入（含 kind 枚举 + persistent + extra_tags）
- `memory_search` — 语义检索 + tag 过滤
- `memory_get_recent` — 时间倒序最近 N 条
- `memory_get_active_rules` — 所有 `kind:rule + persistent:true`
- `memory_get_reflections` — 最近反思
- `memory_get_user_profile` — portrait + extra_info
- `memory_get_schema` — kind 枚举与 tag 命名空间，外部 agent 拿来对齐参数

---

## 七、典型流程示例

### 7.1 内部场景（Fay 主进程内）

#### 例 1：普通用户聊天
```
用户："今天天气真不错"
└─→ Fay 主流程: question() 拿到内容
    ├─→ 锁外算 importance/embedding
    ├─→ remember_conversation_thread 异步写一条 conversation 节点（content="主人：...\nFay：..."）
    └─→ 当前提示词组装时调 memory_stream.retrieve(content)
         ├─→ 取回 30 条候选，分三段塞进 system prompt 的 memory_context
         └─→ LLM 据此生成回复
```
当前 conversation 节点不带 tag，反思阶段无法继承。

#### 例 2：用户主动表达偏好（理想路径）
```
用户："以后晚上 10 点之后别叫我"
└─→ Fay 听到后做出回应（写 conversation 节点）
    └─→ Fay 应该 同时调 core.memory_service.remember(
            content="用户希望晚上 22:00 之后不要主动发起对话",
            kind="preference",
            persistent=True,
            source="fay_self",
            extra_tags=["domain:life_assistant"]
        )
        └─→ 写入一条 observation 节点（事例 D）
```
> 当前 Fay 还没自动做这件事，需要后续在对话流里加"指令识别 → 调 service" 的钩子。

#### 例 3：每天 22:35 用户画像分析
```
schedule.run_pending() 触发 perform_user_portrait_analysis()
└─→ 读 T_Member.user_portrait（旧画像）
└─→ 抽取最近 N 条 conversation 节点
└─→ 调 LLM，按 6 个维度（含"与 Fay 的关系"）生成新画像，1000 字以内
└─→ 写回 T_Member.user_portrait
```

#### 例 4：每晚 23:00 反思
```
schedule.run_pending() 触发 perform_daily_reflection()
└─→ 选若干"主题锚点"（anchor）
    └─→ 对每个 anchor 调 memory_stream.reflect(anchor)
         ├─→ 内部 retrieve 出 120 条相关节点
         ├─→ LLM 提炼为 5 条 reflection 文本
         ├─→ 算 importance + embedding
         └─→ 调 _add_node 写入，tags 自动从源节点继承+覆盖
              结果如事例 G
```

---

### 7.2 外部场景（外部 agent 通过 MCP 调用）

> 外部 agent 通过 SSE 连接 `http://<fay_host>:8765/sse`，按 MCP 协议调用 `memory_*` 工具。

#### 例 5：Claude Code 开新策略任务前先拉规则 & 写 session 开始
```
[Claude Code 启动新会话]
1) call memory_get_active_rules(username="trader_zhang")
   ← 返回 [事例 C, 例 8 的 homecare 规则等]
   → Claude Code 把规则塞进自己的 system prompt

2) call memory_remember(
      content="开始 2026-04-27 上午盘量化执行任务",
      kind="event",
      source="claude_code",
      extra_tags=["domain:quant", "session:2026-04-27-am"],
      username="trader_zhang"
   )
   ← {"ok": true, "node_id": 155, ...}
```

#### 例 6：执行过程中实时回写事件
```
[策略触发 → Claude Code 调 broker API → 成交]
3) call memory_remember(
      content="上午 10:30 AAPL 突破日内高点 187.4，breakout 策略买入信号触发",
      kind="event",
      source="claude_code",
      extra_tags=["domain:quant", "strategy:breakout", "symbol:AAPL", "session:2026-04-27-am"],
      username="trader_zhang"
   )
   → 节点 156 落地（事例 B）

[发现策略 bug]
4) call memory_remember(
      content="买入信号在 AAPL 涨停封板后仍触发，导致 0 成交但占用了风控额度",
      kind="error",
      source="claude_code",
      extra_tags=["domain:quant", "strategy:breakout", "symbol:AAPL", "session:2026-04-27-pm"],
      username="trader_zhang"
   )
   → 节点 173 落地（事例 E）
```

#### 例 7：第二天另一个会话回顾
```
[次日 Claude Code 新会话开启]
1) call memory_search(
      query="breakout 策略最近问题",
      filter_tags_all=["domain:quant", "strategy:breakout"],
      n=10,
      username="trader_zhang"
   )
   ← 返回:
      - 节点 173 (kind:error 假突破)
      - 节点 201 (kind:insight 反思——"3 次假突破，建议加 ATR 滤波")
      - 节点 156 (kind:event 当时的买入)

2) Claude Code 据此调整策略实现，再写一条:
   call memory_remember(
      content="已在 breakout 策略加 ATR(14)>1.5 滤波，规避涨停后假突破",
      kind="decision",
      source="claude_code",
      extra_tags=["domain:quant", "strategy:breakout"],
      username="trader_zhang"
   )
```

#### 例 8：居家养老监测脚本（非交易场景）
```
[ESP32 血压计 → 中转脚本 → MCP]
call memory_remember(
   content="老人晨起血压 158/95，比上周平均高 12 个点",
   kind="event",
   source="elderly_monitor",
   extra_tags=["domain:homecare", "date:2026-04-27"],
   username="grandpa_li"
)
→ 节点 412 落地（事例 F）

[家属在 app 设规则]
call memory_remember(
   content="爷爷每天早晨必须测血压，9 点前没数据要报警",
   kind="rule",
   persistent=True,
   source="family_app",
   extra_tags=["domain:homecare", "schedule:daily"],
   username="grandpa_li"
)
→ 一条 persistent rule 入库

[当晚反思]
Fay 23:00 reflect → 检索 grandpa_li 节点
→ 生成 insight: "老人本周血压偏高 3 次，建议家属安排复查" (kind:insight, domain:homecare)
```

#### 例 9：Cursor 修 bug 后回写
```
[Cursor 修了一个内存泄漏]
call memory_remember(
   content="发现 stream_manager 在 ws 异常断开时未释放 buffer，已改为 try/finally 关闭",
   kind="fact",
   source="cursor",
   extra_tags=["domain:engineering", "module:stream_manager", "session:fix-memleak-1"]
)

[同时记录决策]
call memory_remember(
   content="Fay 项目里所有 ws 路径都要在 finally 里 close buffer",
   kind="rule",
   persistent=True,
   source="cursor",
   extra_tags=["domain:engineering"]
)
```

#### 例 10：教育辅导 agent
```
[家教 agent 完成一节课]
call memory_remember(
   content="小明今天独立完成 5 道一元二次方程，全部正确",
   kind="fact",
   source="tutor_bot",
   extra_tags=["domain:education", "subject:math", "topic:quadratic_eq"],
   username="xiaoming"
)

[下次开课前]
call memory_search(
   query="小明数学进度",
   filter_tags_all=["domain:education", "subject:math"],
   username="xiaoming"
)
→ 拉到上一次的"已掌握一元二次方程" → 这次直接进二次函数
```

#### 例 11：跨 agent 协作 — Claude Code 写规则、Fay 自身遵守
```
1) Claude Code 调:
   memory_remember(
      content="每小时整点检查所有持仓策略是否有未触发的止损单",
      kind="rule",
      persistent=True,
      source="user",
      extra_tags=["domain:quant", "schedule:hourly"]
   )

2) Fay 主进程的某个内置 agent（如调度器）
   每小时启动时调:
   core.memory_service.get_active_rules(username)
   ← 返回所有 persistent rules，包含上面这条
   → 调度器据此触发巡检流程
```
> 这是"MCP 工具同时向 Fay 自身暴露"的核心价值：写入与读取走同一条 service 层，外部 agent 写、内部 agent 读，无 sync 问题。

---

## 八、还没解决的事

1. **对话/观察线程没打 tag**：反思继承不到东西，需在 `remember_conversation_thread` / `remember_observation_thread` 里加默认 tag（如 `source:fay_conversation`、`source:fay_observation`）
2. **冷热分层缺失**：节点数大了之后 retrieve 全扫，没有按 importance + 年龄做分层或聚类收敛
3. **上下文压缩缺失**：retrieve 回来的节点直接全文进 prompt，没有 summary 压缩层
4. **端到端联调未跑**：MCP 工具刚加，需要：启动 Fay → Claude Code 连 SSE → 调 `memory_get_schema` / `memory_remember` / `memory_search`，确认节点正确落盘

---

## 九、文件位置速查

| 用途 | 文件 |
|---|---|
| 节点结构 + memory_stream 主体 | `genagents/modules/memory_stream.py` |
| 对话流 + 写线程 + 定时任务 | `llm/nlp_cognitive_stream.py` |
| 唯一权威 service | `core/memory_service.py` |
| 用户档案 sqlite | `core/member_db.py` |
| MCP SSE 服务（含记忆工具） | `faymcp/mcp_server.py` |
| 记忆数据 | `memory/[<username>/]memory_stream/nodes.json` + `embeddings.json` |
| 用户档案 DB | `memory/user_profiles.db` |

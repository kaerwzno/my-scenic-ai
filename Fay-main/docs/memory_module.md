# Fay Memory Module

本说明描述当前默认的认知记忆实现。适用于 `llm/nlp_cognitive_stream.py` + `genagents/modules/memory_stream.py` 这条路径。

## 记忆节点
- 节点类型：`observation`（观察）、`conversation`（对话）、`reflection`（反思）
- 字段：
  - `node_id`: 递增整数
  - `node_type`: 节点类型
  - `content`: 记忆内容（纯文本，已去除 `<think>` / `<prestart>`）
  - `importance`: 整数重要度
  - `datetime`: 节点创建时间，格式 `YYYY/MM/DD HH:MM:SS`；旧节点可能为空
  - `created` / `last_retrieved`: 时间步（整型）
  - `pointer_id`: 反思节点指向的源节点列表 ID
- 创建：
  - 观察：`remember_observation_thread` / `record_observation`
  - 对话：`remember_conversation_thread`（问答格式：`{user}：{问}\n{agent}：{答}`，其中 `user/user` 会写成“主人”）
  - 反思：`MemoryStream.reflect` 生成
  - 写盘策略：即时不落盘，按定时/退出在 `llm/nlp_cognitive_stream.py::save_agent_memory` 才写入
- Embedding：`memory_stream._add_node` 创建时生成；维度不符时检索阶段会临时重算（内存中，不立即落盘）

## 检索与提示词
- 检索：`MemoryStream.retrieve` 按 recency/relevance/importance 组合权重取回；关联记忆一步检索 `curr_filter="all"` 后按类型分段展示。
- 展示格式：检索结果中若节点有 `datetime`，前缀 `"[{datetime}] "`；无时间则不展示时间。
- 关联记忆段落无“关联记忆”标题，直接列出各类型小节（观察、对话、反思）。

## 配置与隔离
- `memory.isolate_by_user`: 打开后记忆目录按用户名隔离。

## 运行时要点
- 文字接口 `no_reply=true` 且有 `observation` 时：只记观察，不回复；无 `messages` 且有 `observation` 会强制 `no_reply=true`。
- `no_reply=false` 的普通对话：问题/回答会写入对话记忆；`observation` 若存在也会写入观察记忆。
- Prompt 打印：已关闭（`_log_prompt` 是空操作）。

## 文件位置
- 核心逻辑：`llm/nlp_cognitive_stream.py`
- 记忆结构：`genagents/modules/memory_stream.py`
- 定时保存：`llm/nlp_cognitive_stream.py::save_agent_memory`
- 记忆数据：`memory/memory_stream/nodes.json`, `embeddings.json`, `meta.json`

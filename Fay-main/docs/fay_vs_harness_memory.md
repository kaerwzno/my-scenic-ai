# Fay 与 Harness Agent 的记忆机制对比

> 写作时间：2026-04-27。"Harness agent" 指 Anthropic Claude Code 这一类"为开发者准备的通用代理脚手架"，下文以 Claude Code 为代表。**Fay 是为数字人场景设计的开源 agent 框架——它的记忆机制不是给开发者用的工程辅助工具，而是支撑一个长期陪伴主体的认知底座。**

**核心结论先放上：**
- Harness agent 把"开发者协作记忆"做透了，Fay 学了它的元数据组织、来源归因、规则通道、自描述这套"记忆治理"经验；
- 但**数字人场景的诉求与 IDE 场景根本不同**——长期陪伴、多用户隔离、跨会话语义检索、自动反思、自动画像、多模态状态、跨领域外部协作；
- 这些诉求 harness agent 并不打算解决，**Fay 才是数字人场景下记忆机制的正确形态**。

---

## 一、Fay 从 harness agent 学到了什么

Fay 的记忆底座来自斯坦福 Generative Agents 的 memory_stream：三种节点 + 三因子打分 + 反思机制。但 Claude Code 这一年的演进里把"长期记忆怎么对外协作"这件事打磨得很到位，Fay 这次记忆改造里就直接搬了几条它的设计经验：

### 1.1 用前缀命名空间组织 tag，而不是新增枚举字段

Claude Code 的 feedback 记忆文件里，每条记忆带 YAML frontmatter——`name / description / type: feedback / originSessionId`。它没有为"feedback 是不是规则、是不是偏好、属于哪个项目"再单独建字段，而是用"key：value"风格把维度收敛在元数据里。

Fay 这次没有给 ConceptNode 加 `is_rule / is_persistent / domain` 一堆布尔字段，而是加了一个 `tags: list[str]`，用 `kind:rule / persistent:true / domain:quant` 这种命名空间式的前缀承载所有维度。新增维度不改 schema，反思继承 tag 也是按前缀过滤——这套思路直接对标了 harness 的做法。

### 1.2 来源归因（source attribution）

Claude Code 的每条 feedback 都记 `originSessionId`，让"这条规则是哪次对话留下的"可追溯。Fay 这次在 tag 里固定预留了 `source:` 命名空间——`source:claude_code / source:cursor / source:fay_self / source:fay_reflection / source:user`。同一条记忆在反思、检索、调试时都能立刻看出来源，不会出现"这条规则是谁写的"这种盲区。

### 1.3 persistent 标志与"长期规则"通道

Claude Code 的 MEMORY.md 是 auto-loaded 的——每次会话开始自动注入。这种"无条件加载"的语义让"长期约束"和"具体事件"在物理上分开。

Fay 借鉴了这个区分：tag 里专门有 `persistent:true`，并给 service 层加了 `get_active_rules()` 函数，专门返回 `kind:rule + persistent:true` 的节点。外部 agent 在每次开新任务前调一下，就拿到一份等效于 MEMORY.md 的"必须遵守的清单"。

### 1.4 schema 自描述工具

Claude Code 的 memory 系统有一个隐含但关键的体验：约定是公开的、可发现的。第三方工具看到一个 feedback 文件就知道它的字段是什么含义。

Fay 把这条做成了显式接口——`memory_get_schema()` MCP 工具会返回 `kind` 枚举与 tag 命名空间约定。任何外部 agent（Claude Code、Cursor、自定义工具）在不熟悉参数时调用一次，就能拿到"该填什么、有什么约束"的完整说明。这是 Fay 不让外部 agent 乱写 tag 的主要保险。

### 1.5 append-only + 周期性 consolidation

Claude Code 的实践：会话过程中一直 append（消息日志、TodoWrite、feedback 文件），到容量上限时由 compaction 把历史压缩成 summary 注入下一段。

Fay 这次确认了同样的节奏：写入路径只 append 不修改（4 条写入路径都走 `append_prepared_node`），每天 23:00 由 `perform_daily_reflection` 把零散事件 consolidate 成 reflection 节点。reflection 节点继承业务 tag 但覆盖打 `kind:insight + source:fay_reflection`，相当于一次结构化的 compaction。

### 1.6 index/detail 分层

Claude Code 的 MEMORY.md 是个 index，里面是指向 `feedback_xxx.md` 等详细文件的链接，主索引短，详情分文件。

Fay 在用户档案上对应同样的模式：`T_Member.user_portrait` 是简短画像（1000 字以内的 LLM 总结），而 `memory_stream` 里 nodes.json 是完整明细。检索时先看 portrait 拿"这个人是谁"，再用语义检索拿具体事件。

---

## 二、数字人场景下，Fay 是更合适的形态

**这一节是本文的重点。** Harness agent 的目标是辅助开发者写代码——它面对的是一个键盘前的工程师、一个仓库、一段会话；记忆机制只要让"上下文不丢"就够了。

Fay 的目标完全不同：承载一个**会陪伴用户多年、会跨多种场景工作、会随时间成长**的数字人主体。它面对的是一个老人、一个家庭、一个学生、一支量化团队——记忆要做的不是"帮 agent 别忘事"，而是**让数字人成为一个有人格、有关系、有时间纵深的存在**。

下面这张对比表，**第三列是关键**——它直接说明每一项设计差异背后，数字人场景为什么必须选 Fay 这条路：

| 维度 | Harness Agent（如 Claude Code） | Fay | 数字人场景为什么需要 Fay 的设计 |
|---|---|---|---|
| **存储介质** | 主要是文件（CLAUDE.md / MEMORY.md / feedback md） | sqlite（用户档案）+ JSON（节点流）+ embedding（向量） | 数字人要支持多用户、按用户隔离、跨会话语义检索，纯文件不够 |
| **节点类型** | 单一文件粒度，无类型语义 | observation / conversation / reflection 三种 | 对话、观察、反思在数字人交互里需要分别召回（比如检索时按"近期发生过什么"vs"我们聊过什么"分段） |
| **检索方式** | 全量文件加载到 prompt（命中即注入） | 三因子打分（recency + relevance + importance）+ tag 过滤 | 数字人的对话会话短、节点数量多，必须按相关度+时间打分挑出 top-N，否则 prompt 直接撑爆 |
| **重要度评估** | 无，由人/agent 自己取舍内容 | LLM 在写入时自动给 0–100 重要度分数 | 数字人写入是高频自动行为（每句话/每次观察都写），必须有自动评分才能在检索时分层 |
| **时间维度** | 文件 mtime（粗粒度） | 每个节点带 `datetime` + `created` time_step + `last_retrieved`，retrieve 用时间衰减打分 | 陪伴型数字人需要回答"上周我们聊过什么"、"昨天血压是多少"，时间是一等公民 |
| **反思机制** | 无内建反思（用户主动整理 MEMORY.md） | 每天 23:00 LLM 自动反思，把零散事件提炼成 insight 节点，自动继承业务 tag | 数字人作为长期陪伴对象，需要主动从昨日发现规律（"用户连续 3 天血压偏高"），不能等用户来整理 |
| **画像生成** | 无 | 每天 22:35 LLM 重写 `T_Member.user_portrait`，含"与 Fay 的关系"维度（亲密度、情感基调、共同经历） | 数字人需要稳定人格 + 关系记忆，让"昨天对你说过的话"塑造"今天的语气" |
| **多用户隔离** | 单一用户视角 | `memory.isolate_by_user=true` 时按用户名分目录，T_Member 表按 username 索引 | 数字人产品经常一对多（养老院、家教平台、客服），必须支持每个用户独立记忆 |
| **写入并发模型** | 串行（agent 顺序写文件） | 锁外算 importance/embedding，锁内 append；落盘异步 | 对话延迟敏感，不能因为 LLM 评分卡住就让下一句话排队（曾经因此挂死过 5 小时） |
| **多模态状态** | 纯文本 | 与 voice / avatar / live2d / 设备状态联动（scratch 角色卡 + observation 节点） | 数字人是有"身体"的——表情、语音、设备事件都是记忆材料 |
| **实时对话注入** | 一次性加载，不每轮变化 | 每轮对话 retrieve 30 条候选 → 按 type 分三段拼进 system prompt | 数字人对话每一轮上下文都不同，需要动态更新"该想起来什么" |
| **外部 agent 协作写入** | 主要是被 harness 写，没有标准化外部写入接口 | core.memory_service + 7 个 MCP 工具，外部 agent（Claude Code/Cursor）可以反向回写 | 数字人是被多个工具/服务共同塑造的——量化策略写交易事件、监测脚本写血压、家教 agent 写学员进度 |
| **schema 自描述** | 约定式（看示例就懂） | `memory_get_schema()` MCP 工具显式返回 kind 枚举与命名空间 | 数字人对接的外部 agent 异质化高，必须能让外部"运行时发现规约" |
| **场景普适性** | 紧贴 IDE / coding 场景 | 量化交易、居家养老、教育辅导、生活助理、智能家居……同一套机制 | 数字人的产品形态本身就是跨领域的，记忆机制必须场景无关 |

### 2.1 一个具象场景：harness agent 撑不起，Fay 撑得起

想象一个真实数字人产品——居家养老陪护机器人 "小慧"，部署在一个家庭里 365 天。

| 时间 | 发生的事 | 用 harness agent 的记忆能不能扛？ | Fay 怎么扛？ |
|---|---|---|---|
| Day 1 上午 | 老人第一次和小慧聊天，告诉她"我不喜欢被叫'老人家'，叫我老张" | ❌ 没有 user 概念，下次会话不会记得 | conversation 节点 + 偏好节点 `kind:preference, persistent:true`，写入 `T_Member.user_portrait` |
| Day 1 下午 | 血压计自动上传：158/95 | ❌ 不是文本对话，无写入接口 | MCP 工具 `memory_remember(kind=event, source=elderly_monitor, domain:homecare)` 直接写入 |
| Day 1 晚 23:00 | 系统主动反思 | ❌ 不会主动反思 | `perform_daily_reflection` 跑一次，提炼"老张今天血压偏高"insight |
| Day 7 早晨 | 老张说"最近怎么有点累" | ❌ 没有时间维度的检索 | retrieve 带 recency 权重 + tag 过滤，捞到这周 3 条血压偏高的 event 与 1 条反思 insight |
| Day 30 | 家属在 app 设规则"每天早上必须测血压" | ❌ 没有外部 agent 写入通道 | 家属 agent 通过 MCP 调 `memory_remember(kind=rule, persistent=true)`，小慧每天自动 `get_active_rules()` 读到 |
| Day 90 | 小慧和老张已经熟络 | ❌ 没有人格演化 | 22:35 画像分析持续重写 `user_portrait`，含"与 Fay 的关系（亲密度、称呼偏好、共同经历）"维度 |

这是 harness agent 与 Fay 的根本分野——**前者活在一次会话里，后者活在一段关系里。**

### 2.2 一句话定位

> **Harness agent 的记忆，是为了让 agent 不忘事；Fay 的记忆，是为了让数字人成为一个"人"。**

---

## 三、两个仓库

| 项目 | 仓库地址 | 简介 |
|---|---|---|
| **Fay** | https://gitee.com/xszyou/fay | 开源数字人 agent 框架。本文记忆机制的全部代码都在这个仓库里。 |
| **Claude Code（harness agent）** | https://github.com/anthropics/claude-code | Anthropic 官方为开发者打造的通用代理脚手架，本文的"harness agent"参照对象。 |

> 想动手验证：克隆 Fay，启动 `faymcp/mcp_server.py`，用 Claude Code 通过 SSE 连上端口 8765，就能体会"外部 harness agent 通过 MCP 把记忆回写到 Fay"这条新通道。
>
> **两个仓库各司其职**：Claude Code 让开发者更高效地写代码，Fay 让你的数字人活得更长、更真。



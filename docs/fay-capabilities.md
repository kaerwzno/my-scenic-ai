# Fay 能力摸底

> 目的：在设计"主动能力"之前，先把 Fay **实际能做什么**搞清楚，避免设计出一个它做不到的东西。
> 方法：全部结论来自读源码，标注了文件与行号，可复核。
> 摸底时间：2026-09-25

---

## 一、总结论（先说最重要的）

> **Fay 本身不产生"主动意图"。它提供的是"被触发 → 结合记忆生成主动回应"的能力。**

也就是说：

- ✅ Fay 能做：收到一个**外部事件/观测**，结合记忆，生成一句**恰当的、个性化的**主动话语
- ❌ Fay 不能做：自己判断"现在该主动说点什么了"

**这个分工其实正好**——"什么时候该开口"是业务逻辑（景区场景 = 位置、时间、停留时长），
本就该由外部事件源决定；"开口说什么"才是 LLM 该干的事。

---

## 二、四条"主动"路径（已全部定位）

| # | 机制 | 触发方式 | 内容由谁定 | 走 LLM | 记忆参与 | 适用性 |
|---|---|---|---|---|---|---|
| **1** | `POST /to-greet` | **外部推事件** | **Fay** | ✅ | ✅ | ★ **我们主力用这条** |
| 2 | `auto_play` 轮询 | Fay 轮询外部服务 | 外部给死文本 | ❌ 透传 | ❌ | 广播固定话术，**不用** |
| 3 | 日程管理 MCP | 日程到点 | 日程里配的内容 | ✅ | ✅ | ★ 辅助用（时间触发） |
| 4 | `_auto_reply_after_execution` | 后台任务完成 | Fay（基于工具结果） | ✅ | 精简 | 内部机制，无需干预 |

---

## 三、路径 1 详解：`POST /to-greet`（★ 核心）

### 接口

```http
POST http://127.0.0.1:5000/to-greet
Content-Type: application/json

{
  "username": "User",
  "observation": "用户到达灵山大佛，在观景台停留了20分钟"
}
```

**位置**：`gui/flask_server.py:1483`

```python
@__app.route('/to-greet', methods=['POST'])
def to_greet():
    username = data.get('username', 'User')
    observation = data.get('observation', '')
    interact = Interact("hello", 1, {'user': username, 'msg': '按观测要求打个招呼', 'observation': str(observation)})
    text = fay_booter.feiFei.on_interact(interact)
```

**注意**：全仓库**没有任何地方调用它**——这是一个**专为外部系统留的接口**。

### 它内部做了什么

`core/fay_core.py:602-654` 的 `on_interact`：

```python
observation = interact.data.get("observation", None)
...
if obs_text:
    from llm import nlp_cognitive_stream
    nlp_cognitive_stream.record_observation(username, obs_text)   # ① 观测写进记忆
...
text = nlp_cognitive_stream.question(interact.data["msg"], username,
                                     interact.data.get("observation", None))  # ② 带着观测走完整对话
```

### ★ 关键发现：`observation` 是一等公民

观测进来之后有三个去处，每一处都在代码里：

| 去处 | 代码位置 | 效果 |
|---|---|---|
| **① 写入记忆** | `record_observation` → `remember_observation_thread` → `append_prepared_node(..., "observation", ...)` | 生成一个 **`observation` 类型**的记忆节点 |
| **② 注入 prompt** | `_format_context_section("其他观察", observation)`（`nlp_cognitive_stream.py:826, 880`） | 作为「其他观察」段落进入 LLM 上下文 |
| **③ 影响检索** | `_is_current_only_turn(content, observation)`（第 66 行） | 短问候语会**跳过记忆检索**，避免无意义召回 |

**记忆节点有三种类型**（`memory_sections`，`nlp_cognitive_stream.py:1734`）：

```python
memory_sections = [
    ("观察记忆", "observation"),
    ("对话记忆", "conversation"),
    ("反思记忆", "reflection"),
]
```

每次对话会**按类型各取最多 10 条**注入上下文（共 30 条候选），检索权重：

```python
agent.memory_stream.retrieve(
    [query], current_time_step,
    n_count=30,
    curr_filter="all",
    hp=[0.8, 0.5, 0.5],        # ← 三因子权重
    stateless=False,
)
```

这正是**斯坦福 Generative Agents 的"三因子打分"**（recency + relevance + importance）。

### 对我们的意义（★ 这就是"主动陪伴"的实现方式）

```
外部事件源
  │  传感器 / 位置服务 / 时间 / 业务系统
  │  POST /to-greet  {username, observation: "用户在观景台停留20分钟"}
  ▼
Fay：观测写进记忆 → 检索已有记忆（"他腿脚不方便"）→ 生成主动回应
  ▼
「您在观景台歇一会儿吧，前面 50 米就有座椅，不用爬台阶。」
```

**"主动说什么"由 Fay + 记忆决定，"什么时候主动"由我们的事件源决定。分工干净。**

---

## 四、路径 3 详解：日程管理（辅助）

`mcp_servers/schedule_manager/` 是一个**完整独立的日程系统**（自带 SQLite、Web 界面、调度器），
以 **MCP server** 形式接入 Fay。

### 主要能力

| 方法 | 作用 |
|---|---|
| `add_schedule(title, content, schedule_time, repeat_rule, uid)` | 添加日程（支持重复规则 `0000000` 格式） |
| `get_schedules(status, uid)` | 查询 |
| `parse_natural_language_schedule(text, uid)` | **自然语言解析日程**（"明天下午三点提醒我"） |
| `execute_schedule_task(schedule)` | 到点执行 |
| `send_to_fay(message, uid)` | **主动推消息给 Fay** |

### ★ 它的"主动"是怎么实现的

```python
# execute_schedule_task
message = f"【日程提醒】{schedule['title']}: {schedule['content']}"
self.send_to_fay(message, schedule['uid'])
```

而 `send_to_fay` 内部是**调用 Fay 的 `v1/chat/completions` 接口**：

```python
# 获取用户名
username = f"User{uid}" if uid > 0 else "User"
# 然后 POST 到 Fay 的接口
```

### ★★ 这是一个极妙的设计模式

> **日程管理器"伪装成用户"给 Fay 发一条消息，Fay 以为用户在说话，于是回应。**
> **"主动"就这样实现了——不需要 Fay 原生支持主动，外部系统"替用户说话"即可。**

**这个模式可以推广**：任何想让 Fay 主动开口的外部系统，都可以用同样的方式——
要么走 `/to-greet`（带观测，Fay 生成），要么走 `/v1/chat/completions`（当普通消息，Fay 回应）。

---

## 五、路径 2 详解：`auto_play`（结论：不用）

`fay_booter.py:269` 的 `start_auto_play_service`：Fay 轮询外部服务器
`{automatic_player_url}/get_auto_play_item`，拿到 `{text, audio}` 直接播报。

```python
interact = Interact("auto_play", 2, {'user': user, 'text': response_text, 'audio': audio_url})
```

`interact_type = 2` 是**透传模式**——不走 LLM，直接播报给定文本。

**另外**：任何非 auto_play 的交互发生后，会自动播报暂停 30 秒（`fay_core.py:2420`）。

**结论**：这是"广播"，不是"陪伴"——内容写死、不参与记忆。**我们不用它。**

---

## 六、其它已确认的能力（按我们的四个目标）

### 6.1 记忆 ✅（验收点 2 已通过）

| 能力 | 接口 | 备注 |
|---|---|---|
| 写入 | `memory_service.remember(...)` | 自动打 importance 分 + 生成 embedding |
| 检索 | `memory_service.search(...)` | 向量 + 三因子打分 |
| 最近记忆 | `memory_service.get_recent(...)` | |
| **反思结果** | `memory_service.get_reflections(...)` | ★ **自进化的产出通道** |
| **生效规则** | `memory_service.get_active_rules(...)` | ★ **"从经验中学到规则"** |
| 用户画像 | `memory_service.get_user_profile(...)` | |
| schema 自描述 | `memory_service.get_schema(...)` | 供外部程序运行时发现规约 |

**记忆按用户隔离**（`config.json` 的 `isolate_by_user: true`），**同一用户的所有会话共享一份记忆**。

### 6.2 可插拔知识库 ✅

- MCP 协议，管理服务端口 **5010**
- 官方范例：`mcp_servers/yueshen_rag/`（三个工具：`ingest` / `query` / `stats`）
- **预启动（prestart）机制**：工具可在 LLM 推理前自动执行，参数支持 `{{question}}` 占位符
- 外部可调用：`POST /api/mcp/servers/{id}/call`
- 支持目录自动重扫（`YUESHEN_AUTO_INGEST` / `YUESHEN_AUTO_INTERVAL`）

### 6.3 数字人选型 ✅

- 驱动接口：**WebSocket 10002**（`core/wsa_server.py` 的 `HumanServer`）
- **动作语义锚点**：`Action{code, behavior, affect, intensity, priority}` + `Sentiment`
- Fay **不输出** `MotionNo` / `TapBody` 等渲染细节，由各客户端自己映射
- 官方支持形态：Live2D / 3D / Unity / Unreal / 机器人硬件 / 仅语音

### 6.4 其它有用的现成能力

| 能力 | 位置 | 说明 |
|---|---|---|
| 打断说话 | `POST /to-stop-talking` | |
| 唤醒 | `POST /to-wake`、`config.json` 的 `wake_word` | |
| 麦克风开关 | `POST /api/toggle-microphone` | 网页端可控 |
| 人格设定 | `config.json` 的 `attribute` | name / job / position / goal / voice 等 |
| 对外 OpenAI 兼容接口 | `/v1/chat/completions` | `model="fay"` 走 Agent；其他模型名则纯透传 |
| 说话加风格 | `Action.affect` / `Sentiment` | 情绪表达（陪伴场景重要） |

---

## 七、已知短板（摸底中发现，必须记）

| # | 短板 | 影响 | 参考 |
|---|---|---|---|
| 1 | **记忆落盘只在「每天0点」或「优雅退出」** | 崩溃/强杀会丢当天记忆 | `acceptance.md` 问题 1 |
| 2 | **无细粒度记忆管理** | 不能查看/纠正单条记忆 | `acceptance.md` 问题 2 |
| 3 | **Fay 不产生主动意图** | "什么时候开口"必须自己做事件源 | 本文第一条结论 |
| 4 | **`use_bionic_memory` 是空开关** | 这个配置项在 Python 逻辑里**没有任何引用**，只是个 Web UI 的联动标志 | 全仓库 grep 确认 |
| 5 | **会话是推送式** | 小程序类客户端需要轮询 `/api/get-msg` | —— |

---

## 八、基于摸底，第 2 期（主动能力）的建议设计

### 8.1 主链路：事件 → `/to-greet` → 主动回应

```
事件源（自建，可插拔）
  ├─ 位置事件：用户到达 / 离开某景点
  ├─ 停留事件：在某点停留超过 N 分钟
  ├─ 时间事件：用户说过的"下周还要来"到期
  └─ 其它：天气变化、排队过长、闭园临近
        │
        │  POST /to-greet  {username, observation: "<事件描述>"}
        ▼
      Fay：观测入记忆 → 结合记忆生成主动话语
        ▼
   数字人播报（WebSocket 10002）
```

### 8.2 关键设计点

1. **事件源要可插拔**——每个事件类型一个独立的小服务/插件，新增事件不改 Fay
2. **observation 的措辞很重要**——因为它会**作为记忆节点长期保存**，写得太随意会污染记忆
3. **要有防打扰机制**——不能每 5 秒主动一次。参考 Fay 自己的做法：交互后 30 秒内不再主动
4. **主动内容必须结合记忆**——否则就是"广播"，验收点 3 不算通过

### 8.3 验收点 3 的验证方式（对应 README）

```bash
# 终端 1：造一个事件（模拟"用户到达灵山大佛并停留 20 分钟"）
curl -X POST http://127.0.0.1:5000/to-greet \
  -H "Content-Type: application/json" \
  -d '{"username":"User","observation":"用户到达灵山大佛，已在观景台停留20分钟"}'
```

**通过标准**：Fay 主动说了一句**结合了此前记忆**（如"腿脚不方便"）的话，
而不是一句与上下文无关的客套话。

---

## 九、待决策

| # | 决策点 | 选项 |
|---|---|---|
| 1 | 事件源怎么实现 | 独立小服务 / 复用旧项目的 Django / 先用脚本模拟 |
| 2 | 主动的触发时机 | 只做时间触发（简单）还是加位置触发（需要位置数据源） |
| 3 | 防打扰策略 | 间隔多久、什么条件下不打扰 |
| 4 | 主动内容是否要过审 | 陪伴场景建议加，景区可先跳过 |

# 灵灵 · 景区陪伴数字人

一个**个性化陪伴型景区数字人导览系统**。游客可以和数字人对话，它记得住你说过的话、答得出景区的问题、还会在你逛久了主动来关心你。

> 这个项目的**真实验证场景是景区导览**（无锡灵山胜境 / 拈花湾），
> 但技术形态是照着**孤独症儿童陪伴**设计的：长期记忆、主动关心、
> 可插拔知识库、多形象选型。景区只是先把这套东西跑通的练手场。

---

## 一、它现在能做什么

| 能力 | 状态 | 说明 |
|---|---|---|
| 语音对话 | ✅ | 打字或说话 → 数字人用合成的语音回答，**口型跟着真实音频走** |
| 长期记忆 | ✅ | 记住"我腿脚不好""我喜欢靛蓝色"这类信息，下次接着用 |
| 知识库问答 | ✅ | 挂着灵山、拈花湾两个景区的知识库，答不上来时会**老实说不知道**，不编 |
| 主动关心 | ✅ | 逛久了会来关心；闭园前会提醒；连着几句短回复也会追问 |
| 数字人形象 | ✅ | 12 个 Live2D 形象可切换 |
| 动作 | ✅ | 说话时有说话动作；点头/摇头/鞠躬/思考会跟着**说的内容**走 |
| 知识库自进化 | 🟡 半自动 | 管理端能看到知识缺口分析，但"自动补知识"还需要人工点确认 |

---

## 二、架构：三件事分工

```
┌──────────────────────────────────────────────────────────────┐
│  浏览器  /qa                                                  │
│  ├─ 数字人形象（Live2D）      ← 口型跟音频振幅走              │
│  └─ 聊天气泡 + 输入框                                        │
└───────────┬──────────────────────────────┬───────────────────┘
            │ ① 发问题（POST）              │ ② 收回答（WebSocket）
            ▼                              ▲
   /api/qa/chat  ──── 转发 ────▶  Fay  /api/send                 │
   （Next 路由，服务端）                        │                  │
                                              ▼                  │
                                   ┌─────────────────────┐       │
                                   │  Fay 框架（大脑）    │───────┘
                                   │  · 记忆             │
                                   │  · 对话编排         │
                                   │  · TTS 合成         │
                                   │  · 主动机制         │
                                   └──────────┬──────────┘
                                              │ 按需调用（MCP）
                              ┌───────────────┼───────────────┐
                              ▼               ▼               ▼
                        kb-lingshan    kb-nianhuawan     kb-graph
                        灵山知识库      拈花湾知识库      图谱增强问答
```

**一句话**：

- **Fay 是大脑** —— 记忆、说话、主动关心都在它那儿
- **前端是脸和嘴** —— 形象、口型、气泡
- **知识库是可插拔插件** —— 换景区只换插件，不动主程序

### 关键接口约定（踩过坑的，别改）

| 约定 | 为什么 |
|---|---|
| Fay 的 `/api/send` **只认 form-data**，字段名固定 `data`，内容是 `{username, msg}` | 发 JSON 会回"未提供数据" |
| 连上 WebSocket 后必须发 `{Username, "Output": true}` | 不登记身份 Fay 不知道有这个观众；不声明 Output，它**不会合成语音** |
| Fay 的流式文字在 **`Value`** 字段，不是 `Text` | `Text` 只有 audio 那条消息才有 |
| 回答**不从 HTTP 接口返回**，走 WebSocket 异步推回来 | Fay 是异步的：先回一句"稍等"，再从知识库出真答案 |
| 口型参数必须在 `update()` 里写 | 在外面写会被每帧的 `loadParameters()` 冲掉，嘴一动不动 |

---

## 三、目录结构

```
重构数字人/                        ← 仓库根目录（就是你现在这个文件夹）
├── README.md                      ← 你正在看的
├── 交接说明.md                     ★ 交给别人接手先看这份
├── .gitignore                     排除了 node_modules / .venv / .next 等缓存
├──
├── plugins/                       知识库插件：kb-lingshan / kb-nianhuawan / kb-graph
├── scripts/                       建索引、实验测量、知识分析等脚本
├── admin-api/  admin-web/         管理端（知识分析、图谱可视化）
├── docs/                          ★ 设计文档 + 实验数据（论文用）
│   └── 项目定位与技术选型.md        最初那份 352 行的设计说明
├── assets/                        数字人等素材
├── 示范景区公开资料包/             做知识库用的原始资料
├──
├── AI-guide-main/                 ★ 前端（游客看到的界面）
│   ├── src/app/qa/                 数字人对话页
│   ├── src/lib/fay/                连 Fay 的 WebSocket 客户端  ← 我们写的
│   ├── src/lib/live2d/             Live2D SDK（官方拷贝，日常别动）
│   ├── public/sentio/characters/free/   12 个 Live2D 形象
│   ├── 启动与操作.md                ★ 怎么把这套跑起来（先看这个）
│   └── README.md                   前端部分的说明
├──
├── Fay-main/                      Fay 框架本体（GPL-3.0，见「致谢与许可」）
│   ├── core/action_signal.py          ★ 我们加的：语义动作解析（点头/摇头/鞠躬…）
│   ├── config/action_rules.csv        ★ 我们加的：动作规则表（关键词 → 行为/情绪）
│   ├── config.json                    ★ 我们改的：灵灵的人设与音色
│   ├── system.conf.example            ★ 配置模板（真配置 system.conf 不进仓库，里面有 API Key）
│   ├── faymcp/data/mcp_servers.json   ★ 我们改的：挂上三个知识库插件
│   ├── mcp_servers/                   各类 MCP 服务端
│   ├── docs/                          我们写的 Fay 相关文档
│   └── LICENSE                        GPL-3.0（务必保留原样）
├──
├── 当前架构运行手册.md              旧项目的架构记录 + 7 条结构性问题（重构的背景）
├── 待办与改进清单.md                还没做的 9 件事，每件都写了现状和要改哪里
└── 部署文档和说明文档/              环境配置、使用手册、知识库测试说明

（外层 D:\PROJECT\scenic_ai\ 里的 灵山AI导游系统/、mate-human-main/、_backup/
  不属于本仓库，是历史项目和备份。）
```

---

## 四、怎么跑起来

**完整步骤写在 [AI-guide-main/启动与操作.md](AI-guide-main/启动与操作.md)**，这里只给顺序：

```powershell
# 1. 起 Fay（它会自己把三个知识库插件一起拉起来）
cd D:\PROJECT\scenic_ai\重构数字人\Fay-main
& 'D:\PROJECT\scenic_ai\重构数字人\Fay-main\.venv\Scripts\python.exe' main.py start

# 2. 另开一个窗口，先验证「Fay + 知识库」通不通（不用等前端）
cd D:\PROJECT\scenic_ai\重构数字人\AI-guide-main
node scripts\fay-e2e-test.mjs --direct "五印坛城在哪"

# 3. 再开一个窗口，起前端
cd D:\PROJECT\scenic_ai\重构数字人\AI-guide-main
npm run dev

# 4. 浏览器打开
#    http://127.0.0.1:3000/qa
```

### 环境要求

- **Node.js** 20+（前端）
- **Python 3.12** + `uv`（Fay；虚拟环境已建在 `Fay-main/.venv`）
- 百炼（DashScope）API Key —— 配在 `Fay-main/config*.json` 和 `.env.local`

> ⚠️ **不要提交 `.env` / `.env.local` / 任何 API Key**。`.gitignore` 已经挡住了，
> 但你自己在本地加文件时留意一下。

---

## 五、文档索引（论文会用到）

| 文档 | 是什么 |
|---|---|
| [交接说明.md](交接说明.md) | **★ 要交给别人接手，先看这份** —— 已完成的 / 没做到的 / 红线，都写全了 |
| [推送到GitHub.md](推送到GitHub.md) | 用 VS Code 把项目传到 GitHub 的步骤 |
| [docs/项目定位与技术选型.md](docs/项目定位与技术选型.md) | 项目定位、为什么选 Fay、四期验收点 |
| [docs/decisions.md](docs/decisions.md) | 决策记录 D1–D8（每条都写了为什么这么选） |
| [docs/experiment-data.md](docs/experiment-data.md) | **实验数据汇总**（问答准确率、延迟等） |
| [docs/test-questions.md](docs/test-questions.md) | 172 条标准测试问题集（含分类与出题意图） |
| [docs/test-results.md](docs/test-results.md) | 逐条回答记录 |
| [docs/latency-report.md](docs/latency-report.md) | 延迟测量报告（首字延迟 / 完整延迟 / 检索耗时占比） |
| [docs/knowledge-evolution.md](docs/knowledge-evolution.md) | 知识库自进化方案 |
| [docs/knowledge-evolution.json](docs/knowledge-evolution.json) | 知识缺口 / 冗余 / 冲突的分析结果 |
| [docs/digital-human-landscape.md](docs/digital-human-landscape.md) | 数字人技术路线调研 |
| [AI-guide-main/启动与操作.md](AI-guide-main/启动与操作.md) | 启动、验证、排查 |

---

## 六、已知限制（实话实说）

1. **一轮问答要等约 20 秒才出完整答案。** 其中首字（"稍等…"那句）约 3–4 秒，
   真答案要等知识库检索完，中间有约 15 秒空档。这是当前最大的体验问题。
2. **前端还用着作者的第三方平台（Eazo）做登录**，每次打开页面会往 `eazo.ai`
   发一个没用请求。计划里要整个拆掉（demo 不需要登录）。
3. **检索方案准备换成 RAG-Anything**（现在的切分对多模态资料不友好）。
4. **知识库自进化只做到"分析 + 人工确认"**，还没到自动补知识。
5. **`/routes`（行程规划）页面还是作者的"全国城市"演示数据**，跟景区不搭。
6. 数字化身的**口型只有"张嘴大小"一个参数**，做不到元音口型（这是 Live2D 的先天限制）。

---

## 七、致谢与许可

本项目站在三个开源/第三方成果上：

| 来源 | 用在哪 | 许可 |
|---|---|---|
| **[Fay](https://github.com/xszyou/Fay)** | 数字人框架（记忆、对话编排、TTS）—— 已随仓库一起提供，见 `Fay-main/` | **GPL-3.0** |
| **AI-guide** | 前端界面与 Live2D 集成方案 —— 见 `AI-guide-main/` | 见其原仓库 |
| **[Live2D Cubism SDK](https://www.live2d.com/eula/live2d-open-software-license-agreement_en.html)** | 渲染引擎；以及 `public/sentio/characters/free/` 下 12 个示例角色 | Live2D 自有条款 |

> ⚠️ **关于 GPL-3.0**：因为仓库里包含了 Fay 的完整源码，
> 本仓库整体按 **GPL-3.0** 分发，`Fay-main/LICENSE` 请保留原样、不要删。
> 想换成别的许可，就得先把 Fay 从仓库里拿出去（改成让使用者自己下载）。

> ⚠️ **上架/商用前请逐个核对授权**：Live2D 示例角色、Wav2Lip、LiveTalking 等
> 各有各的使用条款。本项目目前定位是**学习与论文用途**。

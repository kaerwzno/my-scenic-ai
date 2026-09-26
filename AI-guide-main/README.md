# 前端 · 游客看到的那个界面

这是整个项目的前端部分。游客在这里跟数字人对话：看着形象说话、看着气泡出字。

> **先看仓库根目录的 [README.md](../README.md)**，那里讲了整个系统怎么组装。
> 这一份只讲前端自己。
>
> 想跑起来 → 看 [启动与操作.md](启动与操作.md)。

---

## 一、这个前端是从哪来的

界面**不是我们写的**，是在一个开源前端项目（内部代号 "AI-guide"，作者做的「旅行家Pro」）
基础上改的 —— 它的数字人舞台、气泡、Live2D 集成、适老化配色都很成熟，
没必要重造。我们在这上面做的改造如下。

### 我们改了什么

| 文件 | 改动 | 为什么 |
|---|---|---|
| [`src/app/api/qa/chat/route.ts`](src/app/api/qa/chat/route.ts) | **重写**：从"查它自己的 Postgres 做 RAG"改成"转发给 Fay" | 知识库换成我们的了（灵山 / 拈花湾 / 图谱） |
| [`src/lib/fay/client.ts`](src/lib/fay/client.ts) | **新增**：连 Fay 的 WebSocket 客户端 | 回答是异步从 WebSocket 推回来的，不是 HTTP 返回的 |
| [`src/components/screens/QAScreen.tsx`](src/components/screens/QAScreen.tsx) | 接线：接 Fay 的文本/音频/动作；关掉浏览器自带朗读 | 让声音和口型都来自 Fay |
| [`src/components/ui/Live2DViewer.tsx`](src/components/ui/Live2DViewer.tsx) | 修内核加载时序 + 加载失败重试 + 错误提示 | 原来刷新页面会 `Live2DCubismCore is not defined`，画布空白 |
| [`src/lib/live2d/src/lappmodel.ts`](src/lib/live2d/src/lappmodel.ts) | 动作选择改成"按中文语义关键词从全部动作里挑" | 原来多数模型的待机动作只有 1 个，看着一直不动 |
| [`src/app/api/qa/avatars/route.ts`](src/app/api/qa/avatars/route.ts) | 去掉 10 个"照片风"预设，只留 12 个 Live2D | 那 10 个的代号在前台渲染出来是同一张卡通脸，等于没得选 |
| 12 个 `public/sentio/characters/free/*/*.model3.json` | 补上 78 个未被声明的动作 | 动作文件本来就在本地，只是没写进声明，白白浪费 |
| `src/app/home/page.tsx` | **清空** | 原来整页是作者的"城市旅游"demo，跟景区不搭 |
| `src/components/screens/RoutesScreen.tsx`、`RouteDetailScreen.tsx` | 地图**留空**（`MAP_ENABLED = false`） | 原来的高德地图没配 Key 就报错；以后接自己的地图 |
| `Navigation.tsx` / `LayoutShell.tsx` | 去掉「我的」标签、「邀请有礼 · 积分」按钮、个人中心 | 精简到只剩数字人这条主线 |

### 删掉的文件（备份在 `../_backup/removed/`）

`ProfileScreen.tsx`（个人中心，181KB）、`HomeScreen.tsx`（旧首页，727 行）、
`PointsInviteModal.tsx`（积分弹窗）、`app/profile/`（路由）。

---

## 二、目录里哪些该看、哪些别碰

```
AI-guide-main/
├── 启动与操作.md          ★ 跑起来看这个
├── README.md              ← 你正在看的
├── src/
│   ├── app/                    路由。每个目录一个页面
│   │   ├── qa/page.tsx         ★ 数字人对话页（主战场）
│   │   ├── api/qa/chat/        ★ 转发给 Fay 的接口
│   │   └── api/qa/avatars/     形象列表
│   ├── components/
│   │   ├── screens/QAScreen.tsx    ★ 数字人界面主体（1986 行，作者的）
│   │   ├── ui/DigitalAvatar.tsx    形象渲染（Live2D / 图片 / 兜底 SVG 三分支）
│   │   ├── ui/Live2DViewer.tsx     Live2D 画布 + 加载重试
│   │   └── layout/                 侧栏、顶栏、外壳
│   └── lib/
│       ├── fay/client.ts        ★ 我们写的，连 Fay
│       ├── live2d/              Live2D SDK 全套（官方拷贝，76 个文件，「别碰区」）
│       └── db/                  Drizzle/Postgres（**现在没用，删 Eazo 时一起清**）
├── public/sentio/characters/free/   12 个 Live2D 形象 + 动作 + 表情
├── scripts/fay-e2e-test.mjs     ★ 一行命令验证整条链路
└── doc/ docs/                   作者留下的设计与 PPT 素材
```

### `src/lib/live2d/` 是「别碰区」

它是 Live2D 官方 SDK 的原样拷贝（约 2.5 万行、76 个文件）。日常调试时搜东西会被它淹没。
**唯一值得看的是 `src/live2d/src/lappmodel.ts`**（动作选择、口型、表情都在这里）。

---

## 三、技术栈

Next.js 16（App Router）+ React 19 + TypeScript 5 + Tailwind v4 + framer-motion + Live2D Cubism 5。

跑起来：`npm run dev`（首次要先 `npm install`）。

---

## 四、还没做的（前端侧）

1. **整个拆掉 Eazo**。现在每次打开页面都会往 `https://eazo.ai/api/apps-open/...` 发一个
   404 请求；11 个组件在用它的登录状态。demo 不需要登录，计划全部换成本地"游客身份"。
2. **`QAScreen.tsx` 有 1986 行**，好几块能拆出去（历史抽屉、音色表、消息列表、输入栏）。
3. **一轮问答要等约 20 秒**才出完整答案（中间 15 秒静默，这是体验上最大的问题）。
4. **`/routes` 页面还是作者的"全国城市"演示数据**，要换成景区内容。
5. `/search`、`/ai-settings` 两个页面已经没人能走到（没有任何链接指向它们）。

完整清单见仓库根目录的 [待办与改进清单.md](../待办与改进清单.md)。

---

## 五、许可

前端部分的原始版权归原作者所有。界面里用的 Live2D 示例角色有各自的使用条款，
**上架/商用前请逐个核对**。本仓库目前定位是学习与论文用途。

原始 README（作者写的，含他们项目自己的介绍）备份在
`../_backup/removed/AI-guide-README.original.md`。

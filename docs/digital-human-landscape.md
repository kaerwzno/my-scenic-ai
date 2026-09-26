# 数字人技术路线调研（2026-09-25）

> 调研方法：GitHub 搜索 API + Bing HTML 结果页。
> 说明：会话里没有专门的搜索工具，但**网络是通的**，直接发 HTTP 请求即可。
> 实测 Bing 质量很差（搜技术词返回看番站和流行歌曲），**GitHub API 才是可靠来源**。

---

## 一、先纠正一个记录错误

我先前说"没有搜索工具，所以没法调研"，把"没有专门的搜索工具"当成了"不能联网"。
实际上网络一直是开的——之前下载人脸检测模型就成功了（901 KB）。

现在的做法：`scripts/web_search.py`（走 Bing，质量有限）+ 直接调 GitHub 搜索 API（质量好）。

---

## 二、四条路线，和各自最适合的位置

### A. 移动端 / 轻量实时 —— 我们最该看的

| 项目 | 星 | 说明 |
|---|---|---|
| `anliyuan/Ultralight-Digital-Human` | 2647 | **可在移动端实时运行**的数字人模型 |
| `Henry-23/VideoChat` | 1313 | 实时交互数字人，可自定义形象与音色、支持音色克隆、**延迟低至 3s** |
| `Voine/ChatWaifu_Mobile` | 1428 | 移动端二次元 AI 聊天器（带口型） |
| `minivision-ai/MiniMeta` | 163 | 轻量数字人 |

**重要发现**：第一个就是**旧项目已经在用的那个**。旧项目 `digital-human-service` 的
`config.yaml` 里 `model: wav2lip / musetalk / ultralight` 三选一——`ultralight` 这个选项
对应的正是 `Ultralight-Digital-Human`。也就是说**旧项目其实已经握着一个移动端友好的方案**，
只是当时选了 wav2lip/musetalk（要 GPU 的重方案）。

### B. Live2D / VRM —— 二次元形象的正确答案

| 项目 | 星 | 说明 |
|---|---|---|
| `YuriCrystal/ai-avatar-bot` | 203 | Live2D 语音助理，架构是「引擎（肉）＋可换的皮（角色模型）＋内容（知识库）」 |
| `organics2016/pymouth` | 10 | Python 的 Live2D 口型同步库 |
| `Heonys/live2d-web` | 2 | 纯 JS 的 Cubism 运行时，**自带 WebGL2 渲染器，不依赖官方 SDK** |
| `Maski0/Live2D-lipSync-Pixijs` / `NyarchLinux/live2d-lipsync-viewer` | 8 / 8 | 网页端口型演示 |

`ai-avatar-bot` 值得细看，它的几个设计和我们高度一致：

- **三层分离**：引擎 / 可换的皮 / 知识库 —— 我们的「换皮不换人」不是自创，是这条路线的共识
- **逐句开讲**：长回答切句，讲第一句时预抓下一句 —— 正是我们列的延迟优化第 1 条
- **口型由实际音量驱动**（不是 viseme）—— 说明包络方案在业界普遍
- 有运营管理后台（对话分析、角色与知识库状态）、可一行 `<script>` 嵌入、纯前端免后端

### C. 神经渲染重模型 —— 要 GPU，管理端演示用

| 项目 | 星 | 说明 |
|---|---|---|
| `antgroup/ditto-talkinghead` | 894 | **Ditto**：ACM MM 2025，运动空间扩散的可控实时说话头 |
| `fudan-generative-vision/hallo` | 8667 | 音频驱动肖像动画 |
| `Zejun-Yang/AniPortrait` | 5016 | 音频驱动写实肖像 |
| `antgroup/echomimic` | 4303 | 可编辑关键点条件的人像动画 |
| `fudan-generative-vision/hallo2` | 3741 | 长时长高分辨率 |

这一族**全部面向真实人脸**，而且都要 GPU。对二次元和 Q 版形象的支持是未知数。

### D. 「集成多种数字人模型」的现成范例 ★

| 项目 | 星 | 说明 |
|---|---|---|
| `Ikaros-521/digital_human_video_player` | 173 | 通过 HTTP API 传视频、排队播放；**后端对接 Easy-Wav2Lip / SadTalker / GeneFace++ / MuseTalk 四个模型**（各自跑 gradio API）|
| `Ikaros-521` 同作者还有数字人整合包系列 | — | 面向非开发的整合包 |
| `xhadmincn/GenHuman` | 178 | 基于 API 的数字人产品 |

**第一个直接回答了「怎么集成多种数字人模型」**：不是找一个万能框架，而是
**每个模型跑自己的服务 + 写一个适配器 + 统一对外一个接口**。这和我们设想的
「驱动适配层」是同一个思路，可以直接参考它的接口设计。

---

## 三、对三个既有判断的印证

| 我们的判断 | 调研结果 |
|---|---|
| 二次元/Q 版不能用神经渲染 | 印证：C 类项目全部面向真实人脸，无一宣称支持插画 |
| 口型该由服务端算好下发 | 部分印证：`ai-avatar-bot` 用的是**音量驱动**（和我们最初设计一致）。但**Fay 能给 viseme，比音量更精确**，所以我们应该用 viseme——这是我们可以比它做得更好的地方 |
| 延迟优化要先做流式分句 | 印证：`ai-avatar-bot` 把「逐句开讲」当成核心特性在讲 |
| 形象要可插拔 | 印证：`ai-avatar-bot` 就是「引擎＋可换的皮」；D 类项目也是适配器模式 |

---

## 四、路线建议

**短期不要引入重模型**，理由：小程序端放视频流的流量和审核成本高；重模型要 GPU；
二次元/Q 版在重模型上效果不可控。

| 端 | 建议驱动 | 理由 |
|---|---|---|
| **小程序** | 分层 2D 或 Live2D，用 Fay 的 viseme 驱动 | 零依赖、零 GPU、流量极小 |
| **管理端 / 大屏** | `emo-v1` 云 API（最省事）或本地 `ultralight`（最省资源） | 效果优先，不计流量 |

**驱动适配层**参考 `digital_human_video_player`：每个驱动一个适配器，统一对外一个接口。

---

## 五、下一步要验证的三件事

1. **小程序能不能跑 Live2D**：`Heonys/live2d-web` 是纯 JS + WebGL2 自带渲染器，
   比官方 Cubism SDK 更可能搬进小程序（小程序有 `<canvas type="webgl">`）。值得一试。
2. **拿我们的立绘去试 `emo-v1`**：看古风插画能不能驱动，二次元/Q 版会怎样。
   这决定了云端方案的可覆盖范围。
3. **`ai-avatar-bot` 的逐句开讲实现**：思路可以直接借，尤其是「讲第一句时预抓下一句」。

---

## 六、两个补充发现（第二轮检索）

### 6.1 美术素材瓶颈有工具了

| 项目 | 星 | 说明 |
|---|---|---|
| `0ran/puppet-part-splitter` | 3 | 浏览器端素材拆分器，面向 Live2D / Spine / Wallpaper Engine，**把插画拆成部件** |
| `richardmaillot/ColorLayerSplitter` | 1 | Photoshop 插件，按颜色把图层拆开 |

第一个正好对着我们「美术素材是最大障碍」这个判断。星数很低（3 星），说明
要么这类需求小众、要么成熟方案都在商业软件里（Photoshop 的自动化脚本、Live2D 官方工具）。
值得看一眼它的拆分粒度够不够我们用——但我们需要的「身体 / 眼睛开合 / 嘴型 5 张」
是**按表情拆**，不是按部件拆，未必直接可用。

### 6.2 小程序跑 Live2D：GitHub 上零结果

搜 `wechat miniprogram live2d` → **0 个仓库**。

这是个有价值的负面结论：**没有人在 GitHub 上开源过"微信小程序 + Live2D"的集成**。
说明这条路要么很折腾、要么做的人不分享。结合之前查到的小程序不支持标准 WebRTC、
Cubism Core 依赖 DOM，**我倾向判断小程序跑 Live2D 风险很高**，
我们的分层 2D 方案（原生 canvas 2d）应该是小程序端更稳的选择。

> 这一条要留给用户判断：如果他的同学/老师见过小程序里跑 Live2D 的案例，那我的判断要修正。

---

## 六、检索记录

原始结果都存在 `docs/research/*.json`。可复现的检索命令：

```powershell
cd D:\PROJECT\scenic_ai\重构数字人\scripts
& 'D:\PROJECT\scenic_ai\重构数字人\Fay-main\.venv\Scripts\python.exe' web_search.py "关键词" --count 10
```

GitHub 搜索（效果最好）：

```
GET https://api.github.com/search/repositories?q=<关键词>&sort=stars&order=desc
GET https://api.github.com/repos/<owner>/<repo>/readme   (Accept: application/vnd.github.raw)
```

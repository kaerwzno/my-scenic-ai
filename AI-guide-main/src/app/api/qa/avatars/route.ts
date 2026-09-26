import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db/client";
import { avatarConfigs } from "@/lib/db/schema/admin";
import { eq, desc } from "drizzle-orm";

// 内置形象预设：全部是 Live2D 模型（public/sentio/characters/free/ 下那 12 个）。
//
// 这里以前还挂着 10 个 female_* / male_* 的"照片风"预设，已经去掉了。原因：
// 那 10 个的 avatarStyle 是代号不是图片地址，DigitalAvatar 判断
// `isLive2D=false`、`isUrl=false`，最后落到代码画的 AvatarSVG 卡通脸 ——
// 点不同的预设出来长得几乎一样（其中 AI数字人和古风女子代号都是 female_hanfu，
// 完全一模一样），自带的 imageUrl 只在选择面板的小卡片上用到。
const DEFAULT_PRESETS = [
  {
    name: "HaruGreeter (Live2D)",
    avatarStyle: "live2d_HaruGreeter",
    voiceStyle: "lively",
    speechRate: 100,
    pitch: 100,
    greeting: "Hello, welcome! I am HaruGreeter, your Live2D guide. How can I help you today?",
    isDefault: false,
    isActive: true,
    imageUrl: "/sentio/characters/free/HaruGreeter/HaruGreeter.png"
  },
  {
    name: "Haru (Live2D)",
    avatarStyle: "live2d_Haru",
    voiceStyle: "lively",
    speechRate: 100,
    pitch: 100,
    greeting: "Hi, I am Haru! Nice to meet you. Let's start our journey!",
    isDefault: false,
    isActive: true,
    imageUrl: "/sentio/characters/free/Haru/Haru.png"
  },
  {
    name: "Kei (Live2D)",
    avatarStyle: "live2d_Kei",
    voiceStyle: "professional",
    speechRate: 100,
    pitch: 90,
    greeting: "Hello, my name is Kei. I am here to assist you with a professional guide.",
    isDefault: false,
    isActive: true,
    imageUrl: "/sentio/characters/free/Kei/Kei.png"
  },
  {
    name: "Chitose (Live2D)",
    avatarStyle: "live2d_Chitose",
    voiceStyle: "warm",
    speechRate: 100,
    pitch: 100,
    greeting: "Welcome! My name is Chitose. I will share the beautiful stories of this place.",
    isDefault: false,
    isActive: true,
    imageUrl: "/sentio/characters/free/Chitose/Chitose.png"
  },
  {
    name: "Epsilon (Live2D)",
    avatarStyle: "live2d_Epsilon",
    voiceStyle: "professional",
    speechRate: 100,
    pitch: 100,
    greeting: "Greetings, I am Epsilon. I will guide you through our tour highlights.",
    isDefault: false,
    isActive: true,
    imageUrl: "/sentio/characters/free/Epsilon/Epsilon.png"
  },
  {
    name: "Hibiki (Live2D)",
    avatarStyle: "live2d_Hibiki",
    voiceStyle: "lively",
    speechRate: 100,
    pitch: 105,
    greeting: "Hello! I am Hibiki. Let's explore all the amazing spots together!",
    isDefault: false,
    isActive: true,
    imageUrl: "/sentio/characters/free/Hibiki/Hibiki.png"
  },
  {
    name: "Hiyori (Live2D)",
    avatarStyle: "live2d_Hiyori",
    voiceStyle: "warm",
    speechRate: 100,
    pitch: 105,
    greeting: "你好，我是日和！很高兴在这个美好的天气里遇见你，今天想听我介绍哪个景点呢？",
    isDefault: true,
    isActive: true,
    imageUrl: "/sentio/characters/free/Hiyori/Hiyori.png"
  },
  {
    name: "Izumi (Live2D)",
    avatarStyle: "live2d_Izumi",
    voiceStyle: "warm",
    speechRate: 100,
    pitch: 100,
    greeting: "Welcome, I am Izumi. Let's make this trip a wonderful memory.",
    isDefault: false,
    isActive: true,
    imageUrl: "/sentio/characters/free/Izumi/Izumi.png"
  },
  {
    name: "Mao (Live2D)",
    avatarStyle: "live2d_Mao",
    voiceStyle: "lively",
    speechRate: 100,
    pitch: 95,
    greeting: "哈罗！我是真央，欢迎来到这里！有什么好玩的尽管问我吧！",
    isDefault: false,
    isActive: true,
    imageUrl: "/sentio/characters/free/Mao/Mao.png"
  },
  {
    name: "Rice (Live2D)",
    avatarStyle: "live2d_Rice",
    voiceStyle: "lively",
    speechRate: 100,
    pitch: 100,
    greeting: "Hi there! I am Rice. Hope you enjoy the guide today!",
    isDefault: false,
    isActive: true,
    imageUrl: "/sentio/characters/free/Rice/Rice.png"
  },
  {
    name: "Shizuku (Live2D)",
    avatarStyle: "live2d_Shizuku",
    voiceStyle: "warm",
    speechRate: 95,
    pitch: 95,
    greeting: "Hello, I am Shizuku. I will introduce you to the details of each spot.",
    isDefault: false,
    isActive: true,
    imageUrl: "/sentio/characters/free/Shizuku/Shizuku.png"
  },
  {
    name: "Tsumiki (Live2D)",
    avatarStyle: "live2d_Tsumiki",
    voiceStyle: "lively",
    speechRate: 100,
    pitch: 100,
    greeting: "Welcome! I am Tsumiki. Let's start the digital human guide journey!",
    isDefault: false,
    isActive: true,
    imageUrl: "/sentio/characters/free/Tsumiki/Tsumiki.png"
  }
];

export async function GET() {
  try {
    let configs = await db.select().from(avatarConfigs).where(eq(avatarConfigs.isActive, true)).orderBy(desc(avatarConfigs.createdAt));
    
    const hasHiyoriDefault = configs.some(c => c.avatarStyle === "live2d_Hiyori" && c.isDefault);
    
    // Seed database if configurations are incomplete, outdated, using URL avatarStyle, or default is not Hiyori
    // ⚠️ 数量这里原来写死 22，减去那 10 个照片风预设之后就对不上了 ——
    //    那样每次请求都会判定"配置不完整"然后重灌一遍库。改成跟着数组长度走。
    if (configs.length < DEFAULT_PRESETS.length || !hasHiyoriDefault || (configs[0] && configs[0].avatarStyle.startsWith("http"))) {
      await db.delete(avatarConfigs);
      await db.insert(avatarConfigs).values(DEFAULT_PRESETS);
      configs = await db.select().from(avatarConfigs).where(eq(avatarConfigs.isActive, true)).orderBy(desc(avatarConfigs.createdAt));
    }
    
    return NextResponse.json(configs);
  } catch (err: any) {
    // ⚠️ 原来是直接 500，于是连不上数据库时前端形象列表整个是空的 ——
    //    前面这份 DEFAULT_PRESETS 白定义了。
    //    这里改成"连不上库就把预设给出去"，用的还是作者自己那 22 个
    //    （10 个照片形象 + 12 个 Live2D），默认形象仍是 live2d_Hiyori，
    //    没有另造一份清单。
    //    只补一个 id：预设里本来没有 id，而前端拿它当 React 列表的 key，
    //    22 条都是 undefined 会触发重复 key 的警告。
    const presets = DEFAULT_PRESETS.map((preset, index) => ({
      ...preset,
      id: index + 1,
    }));
    console.warn("[qa/avatars] 数据库不可用，改用内置预设：", err?.message);
    return NextResponse.json(presets);
  }
}

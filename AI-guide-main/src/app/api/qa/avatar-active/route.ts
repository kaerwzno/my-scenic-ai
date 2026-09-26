import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db/client";
import { avatarConfigs } from "@/lib/db/schema/admin";
import { eq } from "drizzle-orm";

export async function GET() {
  try {
    const config = await db.select().from(avatarConfigs).where(eq(avatarConfigs.isDefault, true)).limit(1);
    if (config[0]) {
      return NextResponse.json(config[0]);
    }
    // 库里没有默认形象配置 → 什么都不返回。
    // 前端 QAScreen 自己的初始值就是作者的默认形象 live2d_Hiyori，
    // 留空它就会用那个，不会被顶掉。
    return NextResponse.json({});
  } catch {
    // ⚠️ 原来这里兜底返回 { avatarStyle: "default" }。
    //    "default" 不是 Live2D 也不是图片地址，前端 DigitalAvatar 判断
    //    avatarStyle.startsWith("live2d_") 不成立，就会掉进作者用 SVG 代码画的
    //    兜底头像 —— 看着像"没有形象"，其实形象一直在，只是被这个值顶掉了。
    //    连不上库时干脆不返回形象，让前端保留自己的默认 live2d_Hiyori。
    return NextResponse.json({});
  }
}

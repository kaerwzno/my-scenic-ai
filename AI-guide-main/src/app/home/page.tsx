/**
 * 首页 —— 暂时留空。
 *
 * 原来的内容整页清掉了。清掉的东西（都在原作者那个 727 行的 HomeScreen 里）：
 *   「十座名山大川」大 banner、城市切换、智能导览官卡片、
 *   「全国热门 / 伴游FM / VR 3D 识景」三张卡、热门景区推荐、页脚。
 * 那些是原作者的"城市旅游"demo，跟景区数字人这条线不搭，
 * 而且它是当时首页加载不出来的原因之一（一张大图 + 一堆外链图片）。
 *
 * 下面只保留配色和排版基调，方便你后面直接往里放东西：
 *   背景 #FAF8F5 · 主文字 #1E2522 · 次要文字 #8F9F8F · 标题用 noto-serif
 *
 * 原实现备份在 D:\PROJECT\scenic_ai\_backup\removed\HomeScreen.tsx
 */

export default function HomePage() {
  return (
    <div className="min-h-svh w-full" style={{ background: "#FAF8F5" }}>
      <div className="mx-auto w-full max-w-4xl px-6 py-20 md:py-28">
        <p
          className="text-[11px] tracking-[0.3em] mb-3"
          style={{ color: "#8F9F8F" }}
        >
          HOME
        </p>

        <h1
          className="text-3xl md:text-4xl font-bold mb-4"
          style={{ fontFamily: "var(--font-noto-serif)", color: "#1E2522" }}
        >
          首页内容待定
        </h1>

        <p className="text-sm leading-relaxed max-w-xl" style={{ color: "#8F9F8F" }}>
          原来的内容已经清空，这里等你想好放什么。数字人导览在
          <span style={{ color: "#4F6F52", fontWeight: 600 }}>「AI数字人导游」</span>
          那一页。
        </p>
      </div>
    </div>
  );
}

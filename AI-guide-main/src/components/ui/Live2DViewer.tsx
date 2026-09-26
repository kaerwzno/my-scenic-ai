"use client";

import { useEffect, useId, useState, memo } from "react";
import { LAppDelegate } from "@/lib/live2d/src/lappdelegate";
import { Live2dManager } from "@/lib/live2d/live2dManager";
import { ResourceModel, RESOURCE_TYPE } from "@/lib/protocol";

/**
 * 确保 Live2D 内核（live2dcubismcore.min.js）已经加载完成。
 *
 * ⚠️ 为什么需要这一步：
 *   layout.tsx 里用 next/script 挂了这个内核，但它和页面 hydration 是竞争的 ——
 *   实测刷新页面时，React 已经开始初始化 Live2D 了，内核还没就绪，于是报
 *   `ReferenceError: Live2DCubismCore is not defined`，canvas 是空白。
 *   所以这里不假设"它一定加载好了"，而是自己确认一遍再往下走。
 */
function ensureCubismCore(): Promise<void> {
  if (typeof window === "undefined") return Promise.resolve();
  if ((window as unknown as { Live2DCubismCore?: unknown }).Live2DCubismCore) {
    return Promise.resolve();
  }

  const CORE_SRC = "/sentio/core/live2dcubismcore.min.js";
  // 页面上可能已经有这个 script（layout 里那个），别重复插一份
  const existing = Array.from(document.querySelectorAll("script")).find((s) =>
    (s as HTMLScriptElement).src.includes("live2dcubismcore")
  ) as HTMLScriptElement | undefined;

  if (existing) {
    return new Promise((resolve, reject) => {
      if ((window as unknown as { Live2DCubismCore?: unknown }).Live2DCubismCore) {
        resolve();
        return;
      }
      existing.addEventListener("load", () => resolve(), { once: true });
      existing.addEventListener(
        "error",
        () => reject(new Error("Live2D 内核脚本加载失败")),
        { once: true }
      );
    });
  }

  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = CORE_SRC;
    script.async = false;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("Live2D 内核脚本加载失败"));
    document.head.appendChild(script);
  });
}

interface Live2DViewerProps {
  avatarStyle: string; // e.g., "live2d_Haru"
}

function Live2DViewer({ avatarStyle }: Live2DViewerProps) {
  const reactId = useId();
  const canvasId = `live2dCanvas-${reactId.replace(/:/g, "")}`;
  const [ready, setReady] = useState(false);
  const [inited, setInited] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** 只重试"换角色"（SDK 已经起来了，只是这个模型没加载出来） */
  const [charKey, setCharKey] = useState(0);
  /** 整条链重来（连 SDK 一起重新初始化） */
  const [bootKey, setBootKey] = useState(0);

  const characterName = avatarStyle.replace("live2d_", "");

  /* ── 1. 初始化 SDK ───────────────────────────────────────────
   *
   * 这一步只管"SDK 本身起没起来"：内核脚本、CubismFramework、WebGL 画布。
   * "模型有没有加载出来"放在下面第 2 步管，因为那是另一回事、另一种重试。
   */
  useEffect(() => {
    let active = true;

    const boot = async () => {
      setError(null);
      setInited(false);

      // 1. 先确认 Live2D 内核在，再初始化 —— 否则会报
      //    "Live2DCubismCore is not defined"，canvas 一片空白
      try {
        await ensureCubismCore();
      } catch (err) {
        console.error("Live2D 内核加载失败:", err);
        if (active) setError("Live2D 内核脚本没加载上。检查一下网络，然后点下面的「重试」。");
        return;
      }
      if (!active) return;

      // 2. 初始化 LAppDelegate
      try {
        // Release any previous singleton instance to ensure clean canvas binding on first click
        LAppDelegate.releaseInstance();
        if (LAppDelegate.getInstance().initialize(canvasId) === false) {
          console.error("Failed to initialize LAppDelegate");
          if (active) setError("Live2D 初始化失败（拿不到 WebGL 画布）。");
          return;
        }
        LAppDelegate.getInstance().run();
      } catch (err) {
        console.error("Error during LAppDelegate initialization:", err);
        if (active) {
          setError(`Live2D 初始化异常：${(err as Error)?.message ?? err}`);
        }
        return;
      }

      // 3. SDK 起来了，交给下面那个 effect 去换角色
      if (active) setInited(true);
    };

    void boot();

    // 3. 窗口尺寸变化
    const handleResize = () => {
      try {
        LAppDelegate.getInstance().onResize();
      } catch (err) {
        console.warn("Resize error:", err);
      }
    };
    window.addEventListener("resize", handleResize);

    return () => {
      active = false;
      window.removeEventListener("resize", handleResize);
      try {
        LAppDelegate.releaseInstance();
      } catch (err) {
        console.warn("Release error:", err);
      }
    };
  }, [canvasId, bootKey]);

  /* ── 2. 换角色，并盯着它有没有真的加载出来 ─────────────────────
   *
   * ⚠️ 为什么要盯：
   *   一个 Live2D 模型要依次拉 model3.json → moc3 → 贴图 → 物理 → 姿势
   *   → 表情 → 动作，HaruGreeter 那种有 43 个文件。
   *   任何一环失败或太慢，原来的代码是**静默放弃**的 ——
   *   画布就一直空白，既没提示也不重试，看着就像"这个形象加载不出来"。
   *   这里加了超时重试（2 次）和最终的错误提示。
   */
  useEffect(() => {
    if (!inited) return;

    // 12 秒 × 3 次 ≈ 36 秒。再长用户就等得难受了。
    const TIMEOUT_MS = 12000;
    const character: ResourceModel = {
      resource_id: characterName,
      name: characterName,
      type: RESOURCE_TYPE.CHARACTER,
      link: `/sentio/characters/free/${characterName}/${characterName}.model3.json`
    };

    let active = true;
    let timer: ReturnType<typeof setTimeout> | null = null;

    try {
      Live2dManager.getInstance().changeCharacter(character);
    } catch (err) {
      console.error("Error changing character:", err);
      setError(`切换到「${characterName}」时报错：${(err as Error)?.message ?? err}`);
      return;
    }

    const startedAt = Date.now();
    const check = () => {
      if (!active) return;
      if (Live2dManager.getInstance().isReady()) {
        setReady(true);
        setError(null);
        return;
      }
      if (Date.now() - startedAt > TIMEOUT_MS) {
        if (charKey < 2) {
          console.warn(`[Live2D]「${characterName}」加载超时，重试第 ${charKey + 1} 次`);
          setCharKey((k) => k + 1); // 触发本 effect 重跑
        } else {
          setReady(false);
          setError(
            `「${characterName}」加载不出来。检查 public/sentio/characters/free/${characterName}/ 里的文件是否齐全。`
          );
        }
        return;
      }
      timer = setTimeout(check, 300);
    };
    // changeCharacter 会先把 ready 置 false，等一拍再开始轮询
    timer = setTimeout(check, 600);

    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
  }, [avatarStyle, characterName, inited, charKey]);

  const retry = () => {
    setError(null);
    setReady(false);
    setInited(false);
    setCharKey(0);
    // 改 bootKey 会让上面的初始化 effect 重跑：
    // 它会先 releaseInstance() 释放旧的 SDK，再重新 initialize。
    setBootKey((k) => k + 1);
  };

  return (
    <div className="w-full h-full relative flex items-center justify-center">
      <canvas
        id={canvasId}
        className="w-full h-full max-w-full max-h-full block bg-transparent"
        style={{ outline: "none" }}
      />

      {/* 加载中 / 失败提示。
          以前加载失败是"静默"的：既没提示也不重试，用户只看到一块空白，
          根本不知道是没加载出来、还是加载出来了但没动。 */}
      {!ready && !error && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
          <span className="text-[11px] tracking-wider text-white/45">
            数字人加载中…
          </span>
        </div>
      )}

      {error && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 px-4 text-center">
          <span className="text-[11px] leading-relaxed text-red-300">
            {error}
          </span>
          <button
            onClick={retry}
            className="px-3 py-1 rounded-full text-[11px] font-bold text-white bg-indigo-600 hover:bg-indigo-500 transition-colors"
          >
            重试
          </button>
        </div>
      )}
    </div>
  );
}

export default memo(Live2DViewer);

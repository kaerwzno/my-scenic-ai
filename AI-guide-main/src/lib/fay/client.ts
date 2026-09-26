/**
 * Fay 框架的 WebSocket 客户端。
 *
 * ── 为什么需要它 ────────────────────────────────────────────
 * 数字人的回答**不是**从 HTTP 接口回来的。
 * 我们把问题发给 Fay 之后（见 src/app/api/qa/chat/route.ts），
 * Fay 内部会走完「知识库检索 → 大模型生成 → TTS 合成」，
 * 然后把结果从 WebSocket（默认 10002 端口）推给所有登记过的客户端：
 *
 *   Key="text"      一段文本，带 IsFirst / IsEnd 标记
 *                   （一句话常常分几段推过来，所以要拼）
 *   Key="audio"     一段音频，HttpValue 是能直接播的 wav 地址
 *   Key="question"  用户自己那句话的回显
 *
 * 我们只做两件事：把 text 拼进气泡；把 audio 交给数字人去播。
 *
 * ── 一个必须记住的坑 ────────────────────────────────────────
 * 连上之后必须马上发 `{Username, Output:true}` 登记身份。
 * 不登记的话 Fay 根本不知道有这个观众；Output 不发 true，
 * 它就**不会为这个连接合成语音**（Fay 里判断"要不要出声"就是看这个）。
 */

/**
 * Fay 的语义动作。她除了文字和语音，还会告诉你"这句话她在做什么"：
 *   behavior  动作类别，如 nod / wave / think / bow / reject …
 *   affect    情绪，如 smile / serious / sad / surprised …
 *   intensity 强度 0~1
 * 后端定义在 Fay 的 config/action_rules.csv 里。
 */
export type FayAction = {
  code?: string;
  behavior?: string;
  affect?: string;
  intensity?: number;
  matchedKeywords?: string[];
};

export type FayData = {
  Key?: string;
  Text?: string;
  Value?: string;
  HttpValue?: string;
  IsFirst?: number;
  IsEnd?: number;
  Action?: FayAction;
  [key: string]: unknown;
};

export type FayMessage = {
  Topic?: string;
  Data?: FayData;
  Username?: string;
};

type Handlers = {
  /** 收到一段回答文本 */
  onText?: (text: string, isFirst: boolean, isEnd: boolean) => void;
  /** 收到一段合成好的音频 */
  onAudio?: (url: string, isEnd: boolean) => void;
  /** 收到用户自己那句话的回显 */
  onQuestion?: (text: string) => void;
  /** 收到语义动作（点头/摇头/鞠躬…） */
  onAction?: (action: FayAction) => void;
  /** 连接状态变化 */
  onStatus?: (connected: boolean) => void;
};

const FAY_WS =
  process.env.NEXT_PUBLIC_FAY_WS || "ws://127.0.0.1:10002";
const FAY_USERNAME = process.env.NEXT_PUBLIC_FAY_USERNAME || "User";

export class FayClient {
  private static _instance: FayClient | null = null;

  public static getInstance(): FayClient {
    if (!FayClient._instance) FayClient._instance = new FayClient();
    return FayClient._instance;
  }

  private _ws: WebSocket | null = null;
  private _handlers: Handlers = {};
  private _retry = 0;
  private _timer: ReturnType<typeof setTimeout> | null = null;
  private _closingByUs = false;

  /** 注册回调。可以多次调用，后面的会覆盖前面的同名字段。 */
  public on(handlers: Handlers): void {
    this._handlers = { ...this._handlers, ...handlers };
  }

  public isConnected(): boolean {
    return !!this._ws && this._ws.readyState === WebSocket.OPEN;
  }

  public connect(): void {
    if (typeof window === "undefined") return;
    if (
      this._ws &&
      (this._ws.readyState === WebSocket.OPEN ||
        this._ws.readyState === WebSocket.CONNECTING)
    ) {
      return; // 已经连上 / 正在连
    }

    this._closingByUs = false;
    try {
      const ws = new WebSocket(FAY_WS);
      this._ws = ws;

      ws.onopen = () => {
        this._retry = 0;
        // ⚠️ 登记身份 + 声明需要音频，否则 Fay 不会为这个连接合成语音
        ws.send(JSON.stringify({ Username: FAY_USERNAME, Output: true }));
        console.log("[Fay] ✓ 已连上", FAY_WS);
        this._handlers.onStatus?.(true);
      };

      ws.onmessage = (ev) => {
        let msg: FayMessage;
        try {
          msg = JSON.parse(String(ev.data));
        } catch {
          return; // Fay 偶尔会推非 JSON 的心跳，忽略
        }
        this._dispatch(msg);
      };

      ws.onclose = () => {
        this._handlers.onStatus?.(false);
        if (!this._closingByUs) this._scheduleReconnect();
      };

      ws.onerror = () => {
        // 出错之后 onclose 一定会跟上来，重连逻辑放在那里
      };
    } catch (e) {
      console.warn("[Fay] WebSocket 建立失败", e);
      this._scheduleReconnect();
    }
  }

  public close(): void {
    this._closingByUs = true;
    if (this._timer) {
      clearTimeout(this._timer);
      this._timer = null;
    }
    try {
      this._ws?.close();
    } catch {
      /* ignore */
    }
    this._ws = null;
  }

  private _scheduleReconnect(): void {
    if (this._timer) return;
    this._retry += 1;
    const delay = Math.min(1000 * 2 ** this._retry, 15000);
    console.warn(`[Fay] ${delay}ms 后重连（第 ${this._retry} 次）`);
    this._timer = setTimeout(() => {
      this._timer = null;
      this.connect();
    }, delay);
  }

  private _dispatch(msg: FayMessage): void {
    const data = msg?.Data;
    if (!data) return;

    const key = data.Key;
    const isFirst = data.IsFirst === 1;
    const isEnd = data.IsEnd === 1;

    // 语义动作跟着每条消息一起来（通常挂在 audio 那条上），
    // 这里统一先取出来，交给数字人去做表情/动作。
    const action = data.Action as FayAction | undefined;
    if (action && (action.behavior || action.affect)) {
      this._handlers.onAction?.(action);
    }

    if (key === "question") {
      this._handlers.onQuestion?.(String(data.Text || data.Value || ""));
      return;
    }

    if (key === "text") {
      // ⚠️ Fay 的流式文字放在 **Value** 里，不是 Text。
      //    （Text 只有 audio 那条消息才有，装的是整句。）
      //    实测漏了这一步会拿到 undefined，气泡一片空白。
      const text = String(data.Text || data.Value || "");
      if (text) this._handlers.onText?.(text, isFirst, isEnd);
      return;
    }

    if (key === "audio") {
      const url = data.HttpValue || data.Value;
      if (typeof url === "string" && url.startsWith("http")) {
        this._handlers.onAudio?.(url, isEnd);
      }
      return;
    }

    // Key = "log" / "robot" 之类的不处理
  }
}

const api = require('../../utils/api');

const app = getApp();
const POLL_INTERVAL = 1200;
const POLL_MAX = 90;          // 约 108 秒

// 把 Fay 给的 Lips 序列展开成时间轴：[{t0,t1,open,width}, ...]
// Lips 每项是 {"Lip":"FF","Time":165}，Time 是这一段的毫秒数，
// 所以累加就是整段话的时间轴——**它跟正在播的音频是同一份时长**，
// 因此不需要额外做音画对齐。
function buildTimeline(lips) {
  let t = 0;
  return (lips || []).map((x) => {
    const v = VISEME[x.Lip] || VISEME.sil;
    const dur = Math.max(20, Number(x.Time) || 33);
    const item = { t0: t, t1: t + dur, open: v.open, width: v.width };
    t += dur;
    return item;
  });
}

// 15 个标准 viseme（Oculus 体系）→ 嘴部参数。
// Fay 直接给这套标签（它内部用 OVR LipSync 从合成音频推出来的），
// 所以这里只需做一层映射，不用自己算音量包络——那套方案已经作废。
//
//   open  张嘴程度 0~1
//   width 嘴的横向宽度系数（圆唇变窄、咧嘴变宽）
const VISEME = {
  sil: { open: 0.00, width: 1.00 },   // 静音
  PP: { open: 0.02, width: 0.95 },   // 双唇音 p/b，闭着
  FF: { open: 0.15, width: 1.05 },   // 唇齿音 f
  TH: { open: 0.25, width: 1.00 },
  DD: { open: 0.35, width: 1.00 },   // 齿音 d/t
  kk: { open: 0.35, width: 0.95 },
  CH: { open: 0.30, width: 0.85 },   // 圆一点的 ch/sh
  SS: { open: 0.20, width: 1.10 },   // 齿音 s，嘴扁
  nn: { open: 0.25, width: 0.95 },
  RR: { open: 0.35, width: 0.85 },
  aa: { open: 0.90, width: 1.05 },   // 低元音 a，张大
  E: { open: 0.55, width: 1.08 },
  ih: { open: 0.40, width: 1.05 },
  oh: { open: 0.65, width: 0.78 },   // 圆唇 o
  ou: { open: 0.50, width: 0.68 },   // 嘟唇 ou
};

// 占位段（"我来帮你查一下，稍等…"）要不要念出来。
// 念出来更像真人；但它同时和界面的「正在想…」重复，所以留个开关。
const SPEAK_PLACEHOLDER = true;

Page({
  data: {
    userId: '',
    avatarId: '',
    avatarName: '',
    anchors: null,
    messages: [],
    draft: '',
    sending: false,
    thinking: false,
    speaking: false,
    affect: 'neutral',        // 当前表情，由 Fay 的 Action.affect 驱动
    scrollTop: 0
  },

  // 渲染用的状态不放进 data，避免每帧 setData（小程序 setData 很贵）
  openness: 0,
  targetOpen: 0,
  width: 1,
  targetWidth: 1,
  timeline: [],              // 当前段的口型时间轴 [{t0,t1,open,width}]
  timelineStart: 0,

  onLoad() {
    const userId = app.globalData.userId || wx.getStorageSync('fmp_user_id');
    const avatarId = app.globalData.avatarId || wx.getStorageSync('fmp_avatar_id');
    this.setData({ userId, avatarId });
  },

  onReady() {
    this.initCanvas();
  },

  onUnload() {
    if (this.canvas && this.rafId) this.canvas.cancelAnimationFrame(this.rafId);
    if (this._envTimer) clearInterval(this._envTimer);
    if (this._pollTimer) clearTimeout(this._pollTimer);
    if (this._audio) { this._audio.stop(); this._audio.destroy(); this._audio = null; }
  },

  // ── 画布 ──────────────────────────────────────────────

  initCanvas() {
    wx.createSelectorQuery()
      .select('#stage')
      .fields({ node: true, size: true })
      .exec((res) => {
        const item = res && res[0];
        if (!item || !item.node) {
          console.error('拿不到 canvas 节点');
          return;
        }
        const canvas = item.node;
        const dpr = (wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync()).pixelRatio || 2;
        canvas.width = item.width * dpr;
        canvas.height = item.height * dpr;
        const ctx = canvas.getContext('2d');
        ctx.scale(dpr, dpr);

        this.canvas = canvas;
        this.ctx = ctx;
        this.cw = item.width;
        this.ch = item.height;
        this._loop = this.renderLoop.bind(this);

        this.loadAvatar();
      });
  },

  async loadAvatar() {
    try {
      const list = await api.getAvatars();
      const all = list.avatars || [];
      const a = all.find((x) => x.id === this.data.avatarId) || all[0];
      if (!a) {
        this.setData({ avatarName: '（形象池是空的）' });
        return;
      }
      this.setData({ avatarId: a.id, avatarName: a.name, anchors: a.anchors });
      app.globalData.avatarId = a.id;

      const img = this.canvas.createImage();
      img.onload = () => {
        this.img = img;
        this.preloadExpressions(a);
        this.canvas.requestAnimationFrame(this._loop);
      };
      img.onerror = () => console.error('立绘加载失败', a.portrait_url);
      img.src = api.BASE + a.portrait_url;
    } catch (e) {
      this.setData({ avatarName: '加载失败' });
      wx.showToast({ title: e.message, icon: 'none', duration: 4000 });
    }
  },

  // 表情差分预加载。key 跟 Fay 的 affect 对齐，收到 affect 直接查表。
  // 注意：这些图是 1MB 级的 PNG，7 张就是 7MB。开发阶段够用，
  // 正式版要压到 200KB 以下再上线（小程序包限制 2MB，虽然这是网络加载，
  // 但手机流量和首屏时间都吃不消）。
  preloadExpressions(avatar) {
    const urls = avatar.expressions || {};
    this.exprImages = {};
    Object.keys(urls).forEach((key) => {
      const im = this.canvas.createImage();
      im.onload = () => { this.exprImages[key] = im; };
      im.onerror = () => console.warn('表情图加载失败', key);
      im.src = api.BASE + urls[key];
    });
    console.log('[表情] 开始加载', Object.keys(urls).length, '张');
  },

  // affect -> 用哪张表情图；没有对应图就退回 neutral
  expressionFor(affect) {
    const imgs = this.exprImages || {};
    return imgs[affect] || imgs.neutral || null;
  },

  renderLoop() {
    this.draw();
    this.rafId = this.canvas.requestAnimationFrame(this._loop);
  },

  draw() {
    const ctx = this.ctx;
    if (!ctx) return;
    ctx.clearRect(0, 0, this.cw, this.ch);

    // 表情优先：有对应 affect 的表情图就用它，否则用基础立绘
    const face = this.expressionFor(this.data.affect) || this.img;
    if (!face) return;

    // 立绘铺满（cover），略微上移让脸更居中
    const iw = face.width;
    const ih = face.height;
    const s = Math.max(this.cw / iw, this.ch / ih);
    const dw = iw * s;
    const dh = ih * s;
    const dx = (this.cw - dw) / 2;
    const dy = (this.ch - dh) * 0.12;
    ctx.drawImage(face, dx, dy, dw, dh);

    // 口型时间轴：按已经过的时间找当前该张多大嘴。
    // 时间轴由 Fay 的 Lips（viseme + 毫秒时长）展开而来，跟正在放的音频同一份时长，
    // 所以不需要额外做音画对齐。
    if (this.timeline.length) {
      const t = Date.now() - this.timelineStart;
      const cur = this.timeline.find((x) => t >= x.t0 && t < x.t1);
      if (cur) {
        this.targetOpen = cur.open;
        this.targetWidth = cur.width;
      } else if (t >= this.timeline[this.timeline.length - 1].t1) {
        this.targetOpen = 0;
        this.targetWidth = 1;
      }
    }

    // 口型：按开合度在锚点位置画一个椭圆。
    // 占位阶段用这个办法，是因为文本生成图像做不出像素对齐的分层素材。
    const anchors = this.data.anchors || {};
    const mouth = anchors.mouth;
    if (!mouth) return;

    // 平滑：直接用目标值会高频抽搐，这里做一次低通
    this.openness += (this.targetOpen - this.openness) * 0.35;
    this.width += (this.targetWidth - this.width) * 0.25;
    const faceH = (anchors.face_height || 0.26) * dh;
    const cx = dx + mouth.x * dw;
    const cy = dy + mouth.y * dh;
    const rw = faceH * 0.085 * (this.width || 1);
    const rh = faceH * 0.016 + faceH * 0.085 * this.openness;

    // 用 save/scale/arc 画椭圆，比 ellipse() 兼容性稳
    ctx.save();
    ctx.translate(cx, cy);
    ctx.scale(1, Math.max(rh / rw, 0.12));
    ctx.beginPath();
    ctx.arc(0, 0, rw, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(74, 38, 46, 0.72)';
    ctx.fill();
    ctx.restore();

    // 张嘴时加一道浅色高光，让"在说话"更明显
    if (this.openness > 0.35) {
      ctx.beginPath();
      ctx.arc(cx, cy + rh * 0.35, rw * 0.34, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(214, 132, 138, 0.55)';
      ctx.fill();
    }
  },

  // ── 口型演示（暂时没有 TTS，先用合成包络验证渲染链路） ──

  onDemoMouth() {
    const env = [];
    for (let i = 0; i < 60; i++) {
      // 模拟说话的节奏：有起伏、偶尔停顿
      const base = 0.35 + 0.45 * Math.abs(Math.sin(i * 0.7));
      env.push(i % 17 < 3 ? 0.02 : base * (0.7 + Math.random() * 0.3));
    }
    this.playEnvelope(env, 60);
  },

  playEnvelope(env, intervalMs) {
    if (this._envTimer) clearInterval(this._envTimer);
    let i = 0;
    this.setData({ speaking: true });
    this._envTimer = setInterval(() => {
      if (i >= env.length) {
        clearInterval(this._envTimer);
        this._envTimer = null;
        this.targetOpen = 0;
        this.setData({ speaking: false });
        return;
      }
      this.targetOpen = env[i];
      i += 1;
    }, intervalMs || 50);
  },

  // ── 对话 ──────────────────────────────────────────────

  onInput(e) {
    this.setData({ draft: e.detail.value });
  },

  goAvatar() {
    wx.navigateBack({ delta: 1 });
  },

  pushMessage(role, content) {
    const messages = this.data.messages.concat([{
      key: `${role}-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      role,
      content
    }]);
    this.setData({ messages, scrollTop: 999999 });
  },

  async onSend() {
    const text = (this.data.draft || '').trim();
    if (!text || this.data.sending) return;
    // 新一轮开始，重置本轮的状态
    this._queuedSegs = 0;
    this._answered = false;
    this.speechQueue = [];
    this.setData({ draft: '', sending: true, affect: 'neutral' });
    this.pushMessage('user', text);

    try {
      const res = await api.sendChat(this.data.userId, text);
      this.setData({ thinking: true });
      this.poll(res.sent_at, 0);
    } catch (e) {
      this.setData({ sending: false, thinking: false });
      this.pushMessage('assistant', '（没发出去：' + e.message + '）');
    }
  },

  poll(since, tries) {
    if (tries >= POLL_MAX) {
      this.setData({ thinking: false, sending: false });
      this.pushMessage('assistant', '（等太久了，没等到回复）');
      return;
    }
    this._pollTimer = setTimeout(async () => {
      try {
        const res = await api.pollChat(this.data.userId, since);
        if (res.answer && res.answer.content && !this._answered) {
          this._answered = true;
          this.setData({ thinking: false });
          this.pushMessage('assistant', res.answer.content);
        }
        // ★ 口型是分段推的，不能一看到 answer 就收工。
        //   实测：answer 到的时候只推了 1 段口型，6 秒后才凑齐。
        //   这里把新到的段追加进播放队列，边到边播。
        const speech = res.speech || [];
        if (speech.length > (this._queuedSegs || 0)) {
          const fresh = speech.slice(this._queuedSegs || 0);
          this._queuedSegs = speech.length;
          this.enqueueSpeech(fresh);
        }
        const last = speech[speech.length - 1];
        if (this._answered && last && last.is_end) {
          this.setData({ sending: false });
          return;
        }
      } catch (e) {
        // 网络抖动就继续轮询，不打断用户
      }
      this.poll(since, tries + 1);
    }, POLL_INTERVAL);
  },

  // ── 说话：真实音频 + 真实口型 ──

  ensureAudio() {
    if (this._audio) return this._audio;
    const a = wx.createInnerAudioContext();
    // 一段播完接下一段——这样长回答会一句一句地说出来，
    // 而不是等整段音频下载完才开始。
    a.onEnded(() => this.playNextSegment());
    a.onError((e) => {
      console.warn('音频播放失败', e);
      setTimeout(() => this.playNextSegment(), 600);
    });
    this._audio = a;
    return a;
  },

  enqueueSpeech(segments) {
    if (!this.speechQueue) this.speechQueue = [];
    this.speechQueue = this.speechQueue.concat(segments || []);
    if (!this._playingSeg) this.playNextSegment();
  },

  playNextSegment() {
    this._playingSeg = false;
    if (!this.speechQueue || !this.speechQueue.length) {
      this.timeline = [];
      this.targetOpen = 0;
      this.targetWidth = 1;
      this.setData({ speaking: false });
      return;
    }
    const seg = this.speechQueue.shift();
    const text = seg.text || '';
    if (!SPEAK_PLACEHOLDER && text.indexOf('稍等') >= 0) {
      return this.playNextSegment();          // 跳过占位段
    }

    this._playingSeg = true;
    const act = seg.action || {};
    if (act.affect) this.setData({ affect: act.affect });

    this.timeline = buildTimeline(seg.lips);
    this.timelineStart = Date.now();
    this.setData({ speaking: true });

    const audio = this.ensureAudio();
    if (seg.audio_url) {
      audio.src = seg.audio_url;
      audio.play();
    } else {
      // 没有音频（理论上不该发生）就按口型时长空放，至少动作是对的
      const dur = this.timeline.length
        ? this.timeline[this.timeline.length - 1].t1 : 1200;
      setTimeout(() => this.playNextSegment(), dur);
    }
  }
});

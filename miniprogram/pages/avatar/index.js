const api = require('../../utils/api');

const app = getApp();

Page({
  data: {
    base: api.BASE,
    userId: '',
    avatars: [],
    picked: '',
    loading: true,
    error: ''
  },

  onLoad() {
    const userId = app.globalData.userId || wx.getStorageSync('fmp_user_id');
    this.setData({ userId });
    this.load();
  },

  async load() {
    try {
      const [list, me] = await Promise.all([
        api.getAvatars(),
        api.getUser(this.data.userId)
      ]);
      this.setData({
        avatars: list.avatars || [],
        picked: me.avatar_id || list.default_avatar || '',
        loading: false,
        error: ''
      });
    } catch (e) {
      this.setData({ loading: false, error: e.message });
    }
  },

  onPick(e) {
    this.setData({ picked: e.currentTarget.dataset.id });
  },

  async onConfirm() {
    if (!this.data.picked) return;
    const name = (this.data.avatars.find(a => a.id === this.data.picked) || {}).name || '';
    try {
      const res = await api.setAvatar(this.data.userId, this.data.picked);
      app.globalData.avatarId = this.data.picked;
      wx.setStorageSync('fmp_avatar_id', this.data.picked);
      wx.showToast({ title: `已换成${name}`, icon: 'none' });
      // 让用户可以马上看到这一步确实换成功了（记忆保留的提示来自后端）
      wx.showModal({
        title: '换好了',
        content: res.message || '记忆和偏好都保留着',
        showCancel: false,
        success: () => wx.navigateTo({ url: '/pages/chat/index' })
      });
    } catch (e) {
      wx.showToast({ title: e.message, icon: 'none' });
    }
  }
});

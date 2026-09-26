const STORAGE_USER = 'fmp_user_id';

App({
  globalData: {
    userId: '',
    avatarId: ''
  },

  onLaunch() {
    // 先用本地生成的游客 ID。
    // 正式上线要换成 wx.login + 后端 code2session，这里保持简单，
    // 但**ID 一旦生成就不能变**——它对应 Fay 那边整份记忆。
    let uid = wx.getStorageSync(STORAGE_USER);
    if (!uid) {
      uid = 'v' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
      wx.setStorageSync(STORAGE_USER, uid);
    }
    this.globalData.userId = uid;
  }
});

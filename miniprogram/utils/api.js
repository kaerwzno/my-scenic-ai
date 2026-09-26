// BFF 的地址。
// 开发者工具里要在「详情 → 本地设置」勾上「不校验合法域名」，
// 否则 http://127.0.0.1 这种地址会被拦掉（project.config.json 里已设 urlCheck:false）。
// 真机调试要换成局域网 IP，例如 http://192.168.1.10:5180
const BASE = 'http://127.0.0.1:5180';

function request(path, options = {}) {
  const { method = 'GET', data } = options;
  return new Promise((resolve, reject) => {
    wx.request({
      url: BASE + path,
      method,
      data,
      header: { 'content-type': 'application/json' },
      timeout: 20000,
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data);
        } else {
          reject(new Error(`HTTP ${res.statusCode} ${path}`));
        }
      },
      fail(err) {
        reject(new Error(`${path} 请求失败：${err.errMsg}。BFF 起了吗（端口 5180）？`));
      }
    });
  });
}

module.exports = {
  BASE,
  request,
  getAvatars: () => request('/api/avatars'),
  getUser: (userId) => request(`/api/user/${userId}`),
  setAvatar: (userId, avatarId) =>
    request(`/api/user/${userId}/avatar`, { method: 'POST', data: { avatar_id: avatarId } }),
  sendChat: (userId, text) =>
    request('/api/chat/send', { method: 'POST', data: { user_id: userId, text } }),
  pollChat: (userId, since) =>
    request(`/api/chat/poll?user_id=${encodeURIComponent(userId)}&since=${since}`)
};

// xhs_login.js — 用独立持久化配置目录打开小红书，等待真实扫码登录
// 判定：登录弹窗消失 && (拿到昵称 || 侧边栏出现用户区)
// 登录成功后优雅关闭浏览器（Browser.close），确保 cookie 落盘
const { spawn } = require('child_process');
const http = require('http');
const fs = require('fs');
const os = require('os');
const path = require('path');

const CHROME_CANDIDATES = [
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  path.join(process.env.LOCALAPPDATA || '', 'Google\\Chrome\\Application\\chrome.exe'),
];
const findChrome = () => {
  for (const p of CHROME_CANDIDATES) if (fs.existsSync(p)) return p;
  throw new Error('找不到 chrome.exe');
};

const PROFILE = path.join(os.homedir(), '.workbuddy', 'browser-profile', 'xhs');
const PORT = 9222;
const sleep = ms => new Promise(r => setTimeout(r, ms));

function httpReq(method, p) {
  return new Promise((resolve, reject) => {
    const r = http.request({ host: '127.0.0.1', port: PORT, path: p, method }, res => {
      let d = ''; res.on('data', c => d += c); res.on('end', () => resolve(d));
    });
    r.on('error', reject); r.end();
  });
}
class WS {
  constructor(url) {
    this.ws = new WebSocket(url); this.id = 0; this.pending = new Map();
    this.ready = new Promise((res, rej) => { this.ws.onopen = res; this.ws.onerror = rej; });
    this.ws.onmessage = ev => {
      const m = JSON.parse(ev.data);
      if (m.id && this.pending.has(m.id)) {
        const { resolve, reject } = this.pending.get(m.id); this.pending.delete(m.id);
        m.error ? reject(new Error(JSON.stringify(m.error))) : resolve(m.result);
      }
    };
  }
  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
      setTimeout(() => { if (this.pending.has(id)) { this.pending.delete(id); reject(new Error('timeout ' + method)); } }, 20000);
    });
  }
  async eval(expr) {
    const r = await this.send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error('eval error');
    return r.result.value;
  }
}

const STATE = `(() => {
  const s = window.__INITIAL_STATE__ || {};
  const nick = (s.user && s.user.userInfo && s.user.userInfo.nickname) || '';
  const sideUser = !!document.querySelector('.side-bar .user, .user.side-bar');
  const modal = !!document.querySelector('.login-container, .login-modal, [class*="login-container"]');
  const nameEl = document.querySelector('.side-bar .user .name, .user-info .name');
  const nameText = nameEl ? (nameEl.innerText || '').trim().slice(0, 20) : '';
  return { nick, sideUser, modal, nameText, url: location.href.slice(0, 50) };
})()`;

(async () => {
  fs.mkdirSync(PROFILE, { recursive: true });
  console.log('配置目录: ' + PROFILE);
  spawn(findChrome(), [
    `--remote-debugging-port=${PORT}`, `--user-data-dir=${PROFILE}`,
    '--no-first-run', '--no-default-browser-check', '--disable-extensions',
    '--window-size=1280,920', 'https://www.xiaohongshu.com/explore'
  ], { detached: true, stdio: 'ignore' }).unref();

  let ok = false;
  for (let i = 0; i < 60; i++) { try { await httpReq('GET', '/json/version'); ok = true; break; } catch (e) { await sleep(400); } }
  if (!ok) throw new Error('CDP 未就绪');

  const tabs = JSON.parse(await httpReq('GET', '/json/list'));
  const tab = tabs.find(t => t.url.includes('xiaohongshu')) || tabs.find(t => t.type === 'page');
  const ws = new WS(tab.webSocketDebuggerUrl); await ws.ready;
  await ws.send('Runtime.enable');

  console.log('请用手机小红书 App 扫码登录…（最多 10 分钟）');
  const deadline = Date.now() + 600000;
  let last = '';
  while (Date.now() < deadline) {
    await sleep(3000);
    let st = null;
    try { st = await ws.eval(STATE); } catch (e) { continue; }
    const logged = !st.modal && (!!st.nick || st.sideUser);
    const label = (logged ? '已登录' : '未登录') + (st.nameText ? ' · ' + st.nameText : '') +
      ' (modal=' + st.modal + ' side=' + st.sideUser + ' nick=' + (st.nick || '空') + ')';
    if (label !== last) { console.log('  ' + label); last = label; }
    if (logged) {
      console.log('OK 登录成功，等待 cookie 落盘…');
      await sleep(5000);
      try { await ws.send('Browser.close'); } catch (e) { }
      await sleep(2500);
      console.log('LOGIN_OK');
      process.exit(0);
    }
  }
  console.log('TIMEOUT 超时未完成登录');
  process.exit(2);
})();

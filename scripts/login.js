// login.js — 通用浏览器登录：打开目标站 → 用户手动登录 → 探测到登录 cookie → 优雅关闭落盘
// 用法: node login.js <bili|douyin>
// 判定用 httpOnly cookie（document.cookie 看不到），必须走 CDP Network.getAllCookies
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

const PLATFORMS = {
  bili: {
    url: 'https://www.bilibili.com',
    profile: path.join(os.homedir(), '.workbuddy', 'browser-profile', 'bili'),
    cookies: ['SESSDATA'],          // 主登录态，缺了等于没登录
    hint: '请用手机 B站 App 扫码，或直接输入账号密码登录…',
  },
  douyin: {
    url: 'https://www.douyin.com',
    profile: path.join(os.homedir(), '.workbuddy', 'browser-profile', 'douyin'),
    cookies: ['sessionid', 'sessionid_ss'],
    hint: '请用手机抖音 App 扫码登录…',
  },
};

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
}

(async () => {
  const key = process.argv[2];
  const p = PLATFORMS[key];
  if (!p) throw new Error('用法: node login.js <bili|douyin>，未知平台 ' + key);

  fs.mkdirSync(p.profile, { recursive: true });
  console.log('平台: ' + key);
  console.log('配置目录: ' + p.profile);
  spawn(findChrome(), [
    `--remote-debugging-port=${PORT}`, `--user-data-dir=${p.profile}`,
    '--no-first-run', '--no-default-browser-check', '--disable-extensions',
    '--window-size=1280,920', p.url
  ], { detached: true, stdio: 'ignore' }).unref();

  let ok = false;
  for (let i = 0; i < 60; i++) { try { await httpReq('GET', '/json/version'); ok = true; break; } catch (e) { await sleep(400); } }
  if (!ok) throw new Error('CDP 未就绪');

  const tabs = JSON.parse(await httpReq('GET', '/json/list'));
  const tab = tabs.find(t => t.url.startsWith(p.url)) || tabs.find(t => t.type === 'page');
  const ws = new WS(tab.webSocketDebuggerUrl); await ws.ready;
  await ws.send('Network.enable');

  console.log(p.hint + '（最多 10 分钟）');
  const deadline = Date.now() + 600000;
  let last = '';
  while (Date.now() < deadline) {
    await sleep(3000);
    let have = [];
    try {
      const c = await ws.send('Network.getAllCookies', {});
      have = (c.cookies || []).filter(x => p.cookies.includes(x.name)).map(x => x.name);
    } catch (e) { continue; }
    const label = have.length ? '已登录（cookie: ' + have.join(', ') + '）' : '未登录';
    if (label !== last) { console.log('  ' + label); last = label; }
    if (have.length >= p.cookies.length || have.includes(p.cookies[0])) {
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
})().catch(e => { console.error('ERR ' + e.message); process.exit(1); });

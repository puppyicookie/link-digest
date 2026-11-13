// xhs_comments.js — 自包含：启动 Chrome → 打开笔记页 → 滚动评论区 → 结构化提取 → 写 JSON
// 用法: node xhs_comments.js <笔记URL> <输出JSON> [--rounds 40] [--keep]
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

function findChrome() {
  for (const p of CHROME_CANDIDATES) if (fs.existsSync(p)) return p;
  throw new Error('找不到 chrome.exe');
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

function httpReq(method, p, port) {
  return new Promise((resolve, reject) => {
    const r = http.request({ host: '127.0.0.1', port, path: p, method }, res => {
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
      setTimeout(() => { if (this.pending.has(id)) { this.pending.delete(id); reject(new Error('timeout ' + method)); } }, 60000);
    });
  }
  async eval(expr) {
    const r = await this.send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 300));
    return r.result.value;
  }
}

const EXTRACT = `(() => {
  const norm = s => (s || '').replace(/[\\u00a0\\u200b]/g, ' ').replace(/\\s+/g, ' ').trim();
  const pick = (root, sels) => {
    for (const s of sels) { const e = root.querySelector(s); if (e) { const t = norm(e.innerText); if (t) return t; } }
    return '';
  };
  const F = {
    author: ['.author .name', '.name', '.author-wrapper .name', 'a.name'],
    text: ['.content .note-text', '.note-text', '.content'],
    like: ['.like-wrapper .count', '.like-wrapper', '.interactions .count'],
    date: ['.date', '.info .date', '.author-wrapper .date']
  };
  const total = norm((document.querySelector('.comments-container') || {}).innerText || '').match(/共\\s*(\\d+|\\d+\\+)\\s*条评论/);
  const rows = [];
  const mk = (el, isSub, pidx, pauthor) => ({
    author: pick(el, F.author),
    text: pick(el, F.text),
    like: pick(el, F.like),
    date: pick(el, F.date),
    is_reply: isSub,
    parent_idx: pidx,
    parent_author: pauthor,
    raw: norm(el.innerText).slice(0, 300)
  });
  const seen = new Set();
  // 以 .parent-comment 为单位，回复挂在精确的父级序号下（不靠作者名匹配）
  Array.from(document.querySelectorAll('.parent-comment')).forEach((p, pi) => {
    const main = p.querySelector(':scope > .comment-item:not(.comment-item-sub)')
              || p.querySelector('.comment-item:not(.comment-item-sub)');
    const pauthor = main ? pick(main, F.author) : '';
    if (main) { rows.push(mk(main, false, pi, '')); seen.add(main); }
    p.querySelectorAll('.comment-item-sub').forEach(s => { rows.push(mk(s, true, pi, pauthor)); seen.add(s); });
  });
  // 兜底：不在 .parent-comment 里的条目也不漏
  document.querySelectorAll('.comment-item').forEach(el => {
    if (!seen.has(el)) rows.push(mk(el, el.classList.contains('comment-item-sub'), -1, ''));
  });
  return { total_label: total ? total[1] : '', count: rows.length, rows };
})()`;

const SCROLL = `(() => {
  const s = document.querySelector('.note-scroller');
  if (s) s.scrollTop = Math.min(s.scrollTop + 800, s.scrollHeight);
  window.scrollBy(0, 800);
  return s ? Math.round(s.scrollTop) : -1;
})()`;

const CLICK_MORE = `(() => {
  const btns = Array.from(document.querySelectorAll('.show-more')).filter(b => /展开/.test(b.innerText || ''));
  btns.forEach(b => b.click());
  return btns.length;
})()`;

const LOGIN_WALL = `(() => {
  const el = document.querySelector('.comments-login');
  return el ? (el.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 40) : '';
})()`;

function parseLikes(s) {
  const t = String(s || '').replace(/赞/g, '').trim();
  if (!t) return 0;
  const m = t.match(/([\d.]+)\s*(万)?/);
  if (!m) return 0;
  let v = parseFloat(m[1]);
  if (m[2]) v *= 10000;
  if (/\+/.test(t)) v += 0.5;
  return v;
}

// 日期字段常混着地点，如 "2025-07-11浙江" / "06-16北京"，拆开
function splitDate(s) {
  const t = String(s || '').trim();
  const m = t.match(/(\d{4}-\d{2}-\d{2}|\d{2}-\d{2})/);
  if (!m) return { date: '', location: t };
  return { date: m[1], location: t.replace(m[1], '').trim() };
}

(async () => {
  const url = process.argv[2];
  const outFile = process.argv[3] || 'comments.json';
  const argv = process.argv.slice(4);
  const keep = argv.includes('--keep');
  const ri = argv.indexOf('--rounds');
  const maxRounds = ri >= 0 ? parseInt(argv[ri + 1], 10) : 70;
  const pi = argv.indexOf('--profile');
  const useProfile = pi >= 0 ? argv[pi + 1] : null;

  const profile = useProfile
    ? useProfile
    : fs.mkdtempSync(path.join(os.tmpdir(), 'xhs-cdp-'));
  if (useProfile) fs.mkdirSync(profile, { recursive: true });
  const tmpProfile = !useProfile;
  const port = 9333 + Math.floor(Math.random() * 400);
  const chrome = findChrome();
  const proc = spawn(chrome, [
    `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
    '--no-first-run', '--no-default-browser-check', '--disable-extensions',
    '--window-size=1400,1000', 'about:blank'
  ], { detached: true, stdio: 'ignore' });
  proc.unref();

  let ver = null;
  for (let i = 0; i < 60; i++) {
    try { ver = JSON.parse(await httpReq('GET', '/json/version', port)); break; } catch (e) { await sleep(400); }
  }
  if (!ver) throw new Error('CDP 未就绪');
  console.error('[cdp] ' + ver.Browser);

  const tab = JSON.parse(await httpReq('PUT', '/json/new?about:blank', port));
  const ws = new WS(tab.webSocketDebuggerUrl);
  await ws.ready;
  await ws.send('Page.enable');
  await ws.send('Runtime.enable');
  await ws.send('Page.navigate', { url });

  // 等评论区出现
  let ready = false;
  for (let i = 0; i < 40; i++) {
    await sleep(1000);
    try {
      if (await ws.eval(`!!document.querySelector('.comments-container') || document.readyState === 'complete' && document.body.innerText.length > 500`)) { ready = true; break; }
    } catch (e) { }
  }
  const title = await ws.eval(`document.title`).catch(() => '');
  console.error('[page] ' + title);

  let first = await ws.eval(EXTRACT);
  console.error('[init] 已渲染 ' + first.count + ' 条，标注总数 ' + (first.total_label || '?'));
  const targetTotal = parseInt(first.total_label, 10) || 0;

  let last = first.count, stall = 0;
  for (let i = 0; i < maxRounds; i++) {
    await ws.eval(SCROLL).catch(() => { });
    await ws.eval(CLICK_MORE).catch(() => { });
    await sleep(1300 + Math.random() * 500);
    const n = await ws.eval(`document.querySelectorAll('.comment-item').length`).catch(() => last);
    if (n !== last) { console.error(`[scroll ${i + 1}] ${n} 条`); stall = 0; }
    else { stall++; if (stall >= 8) { console.error('[stop] 连续 8 轮无新增'); break; } }
    last = n;
    if (targetTotal && n >= targetTotal) { console.error('[stop] 已达标注总数'); break; }
  }
  const wall = await ws.eval(LOGIN_WALL).catch(() => '');
  if (wall) console.error('[login-wall] ' + wall);

  const res = await ws.eval(EXTRACT);
  const rows = res.rows.filter(r => r.text || r.author);
  // 去重（作者+正文+日期）
  const seen = new Set(); const dedup = [];
  for (const r of rows) {
    const k = [r.author, r.text, r.date].join('|');
    if (seen.has(k)) continue;
    seen.add(k); dedup.push(r);
  }
  dedup.forEach(r => {
    r.likes = parseLikes(r.like);
    const d = splitDate(r.date);
    r.date = d.date;
    r.location = d.location;
  });

  const out = {
    url, title,
    total_label: res.total_label,
    collected: dedup.length,
    top_level: dedup.filter(r => !r.is_reply).length,
    replies: dedup.filter(r => r.is_reply).length,
    login_wall: wall || '',
    fetched_at: new Date().toISOString(),
    rows: dedup
  };
  fs.writeFileSync(outFile, JSON.stringify(out, null, 2), 'utf8');
  console.error(`[done] ${dedup.length} 条 → ${outFile}`);

  if (!keep) {
    try { await httpReq('GET', '/json/close/' + tab.id, port); } catch (e) { }
    try { proc.kill(); } catch (e) { }
    await sleep(1200);
    if (tmpProfile) { try { fs.rmSync(profile, { recursive: true, force: true }); } catch (e) { } }
  }
  console.log(JSON.stringify({ title, total_label: out.total_label, collected: out.collected, top_level: out.top_level, replies: out.replies }, null, 2));
})().catch(e => { console.error('ERR ' + e.message); process.exit(1); });

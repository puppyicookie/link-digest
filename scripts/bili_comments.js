// bili_comments.js — 自包含：启动 Chrome → 打开视频页 → 拿 aid → 在页面上下文里
// 用 B站自己的 cookie 调评论 API（绕开裸调风控 12002）→ 主评论按赞 + 楼中楼全拉 → 写 JSON
// 用法: node bili_comments.js <视频URL或BV号> <输出JSON> [--max N] [--profile <目录>]
//   --max 主评论上限，默认 300（B站热门视频几万条评论，必须设上限防跑飞）
//   --profile 传入持久化配置目录（login.js bili 扫码后的登录态）。不传 = 匿名，
//   匿名状态下 B站各接口都只给热门精选几条（实测 3 条 + is_end），结果会标 login_wall
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

function fmtLikes(n) {
  if (n >= 10000) {
    const v = (n / 10000).toFixed(1).replace(/\.0$/, '');
    return v + '万';
  }
  return String(n);
}

function rowFromApi(r) {
  // B站 API → 与 xhs_comments.js 相同的行结构（author/text/like/date/location/is_reply/...）
  let loc = (r.reply_control && r.reply_control.location) || '';
  loc = loc.replace(/^IP属地[：:]?/, '').trim();
  const msg = (r.content && r.content.message) || '';
  // 楼中楼里「回复 @某某 ：」前缀拆出来当 parent_author
  let parentAuthor = '';
  const pm = msg.match(/^回复\s*@([\u4e00-\u9fa5\w\-]+)\s*[：:]/);
  if (pm) parentAuthor = pm[1];
  const d = new Date((r.ctime || 0) * 1000);
  const date = isNaN(d.getTime()) ? '' : d.toISOString().slice(0, 10);
  return {
    rpid: r.rpid, author: (r.member && r.member.uname) || '', text: msg,
    like: fmtLikes(r.like || 0), likes: r.like || 0,
    date, location: loc, rcount: r.rcount || 0,
    is_reply: false, parent_idx: -1, parent_author: parentAuthor,
  };
}

(async () => {
  const target = process.argv[2];
  const outFile = process.argv[3] || 'comments.json';
  const argv = process.argv.slice(4);
  const mi = argv.indexOf('--max');
  const maxTop = mi >= 0 ? Math.max(1, parseInt(argv[mi + 1], 10)) : 300;
  const pi = argv.indexOf('--profile');
  const profileArg = pi >= 0 ? argv[pi + 1] : null;
  if (!target) throw new Error('用法: node bili_comments.js <视频URL或BV号> <输出JSON> [--max N] [--profile 目录]');

  const profile = profileArg || fs.mkdtempSync(path.join(os.tmpdir(), 'bili-cdp-'));
  if (profileArg) fs.mkdirSync(profile, { recursive: true });
  const tmpProfile = !profileArg;
  const headless = !profileArg; // 登录态抓取用有头模式更不容易被风控
  const port = 9333 + Math.floor(Math.random() * 400);
  const proc = spawn(findChrome(), [
    `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
    '--no-first-run', '--no-default-browser-check', '--disable-extensions',
    ...(headless ? ['--headless=new'] : []),
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
  let url = target;
  if (!/^https?:/.test(url)) url = 'https://www.bilibili.com/video/' + url;
  await ws.send('Page.navigate', { url });

  // b23.tv 短链跳转后读最终 URL
  for (let i = 0; i < 30; i++) {
    await sleep(1000);
    const u = await ws.eval('location.href').catch(() => '');
    if (/bilibili\.com\/(video|note)/.test(u)) { url = u; break; }
  }
  const bv = (url.match(/\/(?:video|note)\/(BV\w+)/) || [])[1];
  if (!bv) throw new Error('URL 里没有 BV 号: ' + url);

  // 等页面脚本就绪（__INITIAL_STATE__.aid）
  let aid = 0, title = '';
  for (let i = 0; i < 30; i++) {
    await sleep(1000);
    aid = await ws.eval(`(window.__INITIAL_STATE__||{}).aid || (window.__INITIAL_STATE__||{}).videoData?.aid || 0`).catch(() => 0);
    if (aid) break;
  }
  title = await ws.eval(`((window.__INITIAL_STATE__||{}).videoData||{}).title || document.title`).catch(() => '');
  if (!aid) throw new Error('拿不到 aid（页面可能加载失败或被风控）: ' + title);
  console.error('[page] ' + title + ' | aid ' + aid);

  // 在页面上下文 fetch（带页面 cookie，绕开裸调 12002 风控）
  const apiFetch = async (apiPath) => {
    const r = await ws.eval(`(async () => {
      const res = await fetch('${apiPath}', { credentials: 'include' });
      return await res.json();
    })()`);
    if (!r || r.code !== 0) throw new Error('API ' + apiPath.slice(0, 60) + ' 返回 code=' + (r && r.code) + ' ' + (r && r.message));
    return r.data;
  };

  const roots = [];
  const seenRpid = new Set();
  let totalLabel = '';
  let loginWall = '';
  let hotEndedEarly = false;

  // mode=3 热门排序 cursor 翻页。匿名时往往只回几条就 is_end（实测 3 条）
  const collectByCursor = async (mode, tag) => {
    let next = 0;
    for (let i = 0; i < Math.ceil(maxTop / 20) + 5; i++) {
      const d = await apiFetch(
        `https://api.bilibili.com/x/v2/reply/main?type=1&oid=${aid}&mode=${mode}&ps=20&next=${next}`);
      totalLabel = String((d.cursor && d.cursor.all_count) || totalLabel);
      const reps = d.replies || [];
      for (const r of reps) {
        if (seenRpid.has(r.rpid)) continue;
        seenRpid.add(r.rpid);
        roots.push(rowFromApi(r));
      }
      console.error(`[${tag} ${i}] +${reps.length} / 累计去重 ${roots.length}，总数 ${totalLabel}`);
      const cur = d.cursor || {};
      if (cur.is_end || !reps.length) return cur.is_end && roots.length < maxTop;
      next = cur.next;
      if (roots.length >= maxTop) return false;
      await sleep(350);
    }
    return false;
  };

  hotEndedEarly = await collectByCursor(3, 'hot');
  // 热门流提前断（常见于匿名）→ 用「最新」流补到上限，最后统一按赞排序
  if (hotEndedEarly && roots.length < maxTop) {
    console.error('[fallback] 热门流提前结束，改用最新流补页');
    await collectByCursor(2, 'new');
  }
  if (tmpProfile && totalLabel && roots.length < Math.min(maxTop, 10)
      && parseInt(totalLabel, 10) > 50) {
    loginWall = '未登录，B站只返回热门精选 ' + roots.length + ' 条（共 ' + totalLabel + ' 条）';
  }
  roots.sort((a, b) => b.likes - a.likes);
  const rootsTrimmed = roots.slice(0, maxTop);

  // 楼中楼：reply/reply 接口是老式 pn 翻页（实测无 cursor，ps 上限 20），单条上限 500
  const rows = rootsTrimmed.map((r, i) => Object.assign(r, { parent_idx: i }));
  let replyCount = 0;
  for (const root of rows) {
    if (!root.rcount) continue;
    const want = Math.min(root.rcount, 500);
    let got = 0;
    for (let pn = 1; got < want; pn++) {
      const d = await apiFetch(
        `https://api.bilibili.com/x/v2/reply/reply?type=1&oid=${aid}&root=${root.rpid}&ps=20&pn=${pn}`);
      const reps = d.replies || [];
      for (const r of reps) {
        const row = rowFromApi(r);
        row.is_reply = true; row.parent_idx = root.parent_idx;
        if (!row.parent_author) row.parent_author = root.author;
        rows.push(row); got++; replyCount++;
      }
      console.error(`[reply root#${root.parent_idx}] 累计 ${got}/${root.rcount}`);
      if (!reps.length) break;
      if ((d.page && d.page.count && got >= d.page.count) || pn > 40) break;
      await sleep(300);
    }
  }

  const out = {
    url, title, platform: 'bilibili',
    total_label: totalLabel,
    collected: rows.length,
    top_level: rootsTrimmed.length,
    replies: replyCount,
    login_wall: loginWall,
    fetched_at: new Date().toISOString(),
    rows,
  };
  fs.writeFileSync(outFile, JSON.stringify(out, null, 2), 'utf8');
  console.error(`[done] 主评 ${rootsTrimmed.length} + 回复 ${replyCount} → ${outFile}`);

  try { await httpReq('GET', '/json/close/' + tab.id, port); } catch (e) { }
  try { proc.kill(); } catch (e) { }
  await sleep(1200);
  if (tmpProfile) { try { fs.rmSync(profile, { recursive: true, force: true }); } catch (e) { } }
  console.log(JSON.stringify({ title, total_label: out.total_label, collected: out.collected, top_level: out.top_level, replies: out.replies }, null, 2));
})().catch(e => { console.error('ERR ' + e.message); process.exit(1); });

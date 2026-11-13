# -*- coding: utf-8 -*-
"""
link-digest 工作区管理 + 内容采集。

子命令:
    init    <项目目录>                     建目录骨架（文章/ 总结/ 素材/）
    collect <项目目录> <链接>              采集链接 → 素材/<n>/，并追加 ledger
    retitle <项目目录> <编号> <新标题>      把文章改成新标题，并联动素材目录、图片引用、ledger
    next    <项目目录> --type article|summary   输出下一个可用编号
    status  <项目目录>                     输出当前收录情况
    check   <项目目录> [--vault <路径>] [--links]   体检：图片、编号、台账、覆盖范围、孤儿文件
    comments <项目目录> <编号> [--force] [--top N]  抓小红书评论区 → 素材/<n>/comments.json + 评论.md
    login   [--timeout 秒]                打开浏览器等扫码登录小红书（登录态存在独立配置目录）
    libraries <父目录>                    列出该目录下的所有库
    publish <项目目录> --vault <路径> [--prune] [--dry-run]  镜像到 Obsidian（含冲突保护）

设计约定:
    - 文章/总结采用「分开编号」：文章/1.md、2.md…；总结/1.md、2.md…
    - 素材/<n>/ 与 文章/<n>.md 同号，一一对应
    - 脚本只负责「采集 + 落盘 + 编号」，正文文章与总结由宿主模型撰写
    - 所有输出为单行 JSON（便于宿主解析），失败退出码 2

依赖: Python 3 标准库 + curl；平台提取复用 media-extract 技能
"""
import json
import os
import re
import shutil
import subprocess
import sys
import datetime

HOME = os.path.expanduser('~')
MEDIA_SCRIPTS = os.path.join(HOME, '.workbuddy', 'skills', 'media-extract', 'scripts')

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')

PLATFORM_ALIAS = {
    'xiaohongshu': '小红书',
    'bilibili': 'B站',
    'xiaoyuzhou': '小宇宙',
    'douyin': '抖音',
    'wechat': '公众号',
}


def out(obj, code=0):
    print(json.dumps(obj, ensure_ascii=False))
    sys.exit(code)


def fail(msg, **extra):
    extra['ok'] = False
    extra['error'] = msg
    out(extra, 2)


def now():
    return datetime.datetime.now().astimezone().isoformat(timespec='seconds')


def ensure_project(project):
    for sub in ('文章', '总结', '素材'):
        os.makedirs(os.path.join(project, sub), exist_ok=True)


def safe_title(t, limit=40):
    """把标题变成可做文件名的片段：去 Windows 禁用字符，限长。"""
    t = re.sub(r'[\\/:*?"<>|\r\n\t]', ' ', t or '')
    t = re.sub(r'\s+', ' ', t).strip().rstrip('.')
    return t[:limit].strip()


NUM_RE = r'(\d+)(?:[ .].*)?\.md$'


def numbered(path):
    """返回目录里 N.md / N 标题.md 的编号集合。"""
    if not os.path.isdir(path):
        return []
    nums = []
    for f in os.listdir(path):
        m = re.match(r'^%s' % NUM_RE, f)
        if m:
            nums.append(int(m.group(1)))
    return sorted(nums)


def load_ledger(project):
    lp = os.path.join(project, '.ledger.json')
    if os.path.exists(lp):
        try:
            return json.load(open(lp, encoding='utf-8'))
        except Exception:
            return []
    return []


def next_idx(project, kind):
    if kind == 'article':
        a = numbered(os.path.join(project, '文章'))
        b = []
        med = os.path.join(project, '素材')
        if os.path.isdir(med):
            for d in os.listdir(med):
                m = re.match(r'^(\d+)(?:[ .].*)?$', d)
                if m and os.path.isdir(os.path.join(med, d)):
                    b.append(int(m.group(1)))
        c = [e.get('idx', 0) for e in load_ledger(project)]
        pool = a + b + [x for x in c if isinstance(x, int)]
    elif kind == 'summary':
        pool = numbered(os.path.join(project, '总结'))
    else:
        fail('kind 只能是 article 或 summary')
    return (max(pool) + 1) if pool else 1


def detect_platform(url):
    u = url.lower()
    if 'xiaohongshu.com' in u or 'xhslink.com' in u:
        return 'xiaohongshu'
    if 'bilibili.com' in u or 'b23.tv' in u:
        return 'bilibili'
    if 'xiaoyuzhoufm.com' in u:
        return 'xiaoyuzhou'
    if 'douyin.com' in u or 'iesdouyin.com' in u:
        return 'douyin'
    if 'mp.weixin.qq.com' in u:
        return 'wechat'
    return None


def resolve_short(url):
    """短链（xhslink / b23.tv）先跟一次跳转拿真实地址。"""
    if not re.search(r'(xhslink\.com|b23\.tv)', url):
        return url
    r = subprocess.run(['curl', '-sL', '-o', '/dev/null', '-w', '%{url_effective}',
                        '--max-time', '20', '-A', UA, url],
                       capture_output=True, text=True)
    eff = (r.stdout or '').strip()
    return eff if eff.startswith('http') else url


def run_extractor(platform, url, incoming):
    script = {
        'xiaohongshu': 'extract_xhs.py',
        'bilibili': 'extract_bilibili.py',
        'xiaoyuzhou': 'extract_xiaoyuzhou.py',
        'douyin': 'extract_douyin.py',
    }.get(platform)
    if not script:
        return None, 'unsupported'
    path = os.path.join(MEDIA_SCRIPTS, script)
    if not os.path.exists(path):
        return None, '缺少 media-extract 提取器: %s' % path
    r = subprocess.run([sys.executable, path, url, incoming],
                       capture_output=True, text=True, encoding='utf-8', errors='ignore')
    if r.returncode != 0:
        tail = (r.stdout or r.stderr or '').strip().splitlines()
        return None, (tail[-1] if tail else '提取器返回码 %d' % r.returncode)
    subs = [d for d in os.listdir(incoming)
            if os.path.isdir(os.path.join(incoming, d))]
    if not subs:
        return None, '提取器未产出目录'
    subs.sort(key=lambda d: os.path.getmtime(os.path.join(incoming, d)))
    return os.path.join(incoming, subs[-1]), None


def strip_html(h):
    h = re.sub(r'(?is)<(script|style)[^>]*>.*?</\1>', '', h)
    h = re.sub(r'(?i)<br\s*/?>', '\n', h)
    h = re.sub(r'(?i)</(p|div|section|li|h[1-6])>', '\n', h)
    h = re.sub(r'(?s)<[^>]+>', '', h)
    import html as _html
    h = _html.unescape(h)
    h = re.sub(r'[ \t\u00a0]+', ' ', h)
    h = re.sub(r'\n{3,}', '\n\n', h)
    return h.strip()


def wechat_fetch(url, incoming):
    """公众号：无现成提取器，做 best-effort 正文抓取。解析不到就明确失败。"""
    os.makedirs(incoming, exist_ok=True)
    r = subprocess.run(['curl', '-sL', '--max-time', '40', '-A', UA,
                        '-H', 'Accept: text/html,application/xhtml+xml',
                        '-H', 'Accept-Language: zh-CN,zh;q=0.9', url],
                       capture_output=True, text=True, encoding='utf-8', errors='ignore')
    html = r.stdout or ''
    if len(html) < 2000:
        return None, '公众号页面抓取为空（可能被限制或需在微信内打开）'
    title = ''
    m = re.search(r'(?is)<h1[^>]*class="rich_media_title"[^>]*>(.*?)</h1>', html)
    if not m:
        m = re.search(r'(?is)<meta\s+property="og:title"\s+content="([^"]*)"', html)
    if m:
        title = strip_html(m.group(1))
    body = ''
    m = re.search(r'(?is)<div[^>]*id="js_content"[^>]*>(.*?)</div>\s*(?:<script|<div[^>]*class="rich_media_tool)', html)
    if not m:
        m = re.search(r'(?is)<div[^>]*id="js_content"[^>]*>(.*)', html)
    if m:
        body = strip_html(m.group(1))
    if len(body) < 120:
        return None, '公众号正文解析失败（长度 %d），未生成文章' % len(body)

    author = ''
    m = re.search(r'(?is)var\s+nickname\s*=\s*"([^"]*)"', html)
    if m:
        author = m.group(1)

    d = os.path.join(incoming, re.sub(r'[\\/:*?"<>|\n\r\t]', '', title or 'wechat')[:40] or 'wechat')
    os.makedirs(os.path.join(d, 'media'), exist_ok=True)
    with open(os.path.join(d, 'content.md'), 'w', encoding='utf-8') as f:
        f.write('# %s\n\n**文案**\n\n%s\n' % (title or '(无标题)', body))
    with open(os.path.join(d, 'meta.md'), 'w', encoding='utf-8') as f:
        f.write('# %s\n\n- 平台：公众号\n- 作者：%s\n- 发布时间：\n- 状态：partial\n- 原文链接：%s\n'
                % (title or '(无标题)', author, url))
    return d, None


def parse_meta(md):
    """从 meta.md 里抽 作者/发布时间。"""
    author, pub = '', ''
    if not md or not os.path.exists(md):
        return author, pub
    for line in open(md, encoding='utf-8', errors='ignore'):
        line = line.strip()
        if line.startswith('- 作者'):
            author = line.split('：', 1)[-1].strip()
        elif line.startswith('- 发布时间'):
            pub = line.split('：', 1)[-1].strip()
    return author, pub


def cmd_init(args):
    if not args:
        fail('用法: init <项目目录>')
    project = os.path.abspath(args[0])
    ensure_project(project)
    out({'ok': True, 'project': project, 'created': ['文章', '总结', '素材']})


def cmd_next(args):
    project, kind = None, 'article'
    i = 0
    while i < len(args):
        a = args[i]
        if a == '--type':
            kind = args[i + 1] if i + 1 < len(args) else 'article'
            i += 2
            continue
        if not a.startswith('--') and project is None:
            project = a
        i += 1
    if not project:
        fail('用法: next <项目目录> --type article|summary')
    project = os.path.abspath(project)
    out({'ok': True, 'type': kind, 'next': next_idx(project, kind)})


def cmd_status(args):
    if not args:
        fail('用法: status <项目目录>')
    project = os.path.abspath(args[0])
    arts = numbered(os.path.join(project, '文章'))
    sums = numbered(os.path.join(project, '总结'))
    ledger = []
    lp = os.path.join(project, '.ledger.json')
    if os.path.exists(lp):
        try:
            ledger = json.load(open(lp, encoding='utf-8'))
        except Exception:
            ledger = []
    out({'ok': True, 'project': project, 'articles': arts, 'summaries': sums,
         'sources': ledger})


def canon(url):
    """归一化成判重 key。

    同一条内容会有多种写法：小红书 /explore/<id> 与 /discovery/item/<id> 是同一篇，
    分享参数 xsec_token 每次都不同。所以按「平台 + 内容 ID」判重，不按 URL 字符串。
    """
    u = (url or '').strip()
    low = u.lower()
    if 'xiaohongshu.com' in low or 'xhslink.com' in low:
        m = re.search(r'/(?:explore|discovery/item|item|note)/([0-9a-z]{20,32})', low)
        if m:
            return 'xhs:' + m.group(1)
    if 'bilibili.com' in low or 'b23.tv' in low:
        m = re.search(r'(bv[0-9a-z]{10})', low)
        if m:
            return 'bili:' + m.group(1)
        m = re.search(r'/av(\d+)', low)
        if m:
            return 'bili:av' + m.group(1)
    if 'xiaoyuzhoufm.com' in low:
        m = re.search(r'/episode/([0-9a-z]+)', low)
        if m:
            return 'xyz:' + m.group(1)
    if 'douyin.com' in low or 'iesdouyin.com' in low:
        m = re.search(r'/(?:video|note)/(\d+)', low)
        if m:
            return 'dy:' + m.group(1)
        m = re.search(r'modal_id=(\d+)', low)
        if m:
            return 'dy:' + m.group(1)
    if 'mp.weixin.qq.com' in low:
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(u).query)
        biz = (q.get('__biz') or [''])[0]
        mid = (q.get('mid') or [''])[0]
        idx = (q.get('idx') or [''])[0]
        if biz or mid:
            return 'wx:%s:%s:%s' % (biz, mid, idx)
    return re.sub(r'[?#].*$', '', u).rstrip('/')


def numbered_file(d, idx):
    """在目录里按编号找文件，兼容 1.md 与 1 标题.md。"""
    if not os.path.isdir(d):
        return None
    for f in sorted(os.listdir(d)):
        m = re.match(r'^%s' % NUM_RE, f)
        if m and int(m.group(1)) == idx:
            return os.path.join(d, f)
    return None


def article_file(project, idx):
    return numbered_file(os.path.join(project, '文章'), idx)


def material_dir(project, idx):
    """按编号找素材目录，兼容 1/ 与 1 标题/。"""
    d = os.path.join(project, '素材')
    if not os.path.isdir(d):
        return None
    for name in sorted(os.listdir(d)):
        m = re.match(r'^(\d+)(?:[ .].*)?$', name)
        if m and int(m.group(1)) == idx and os.path.isdir(os.path.join(d, name)):
            return os.path.join(d, name)
    return None


def split_fm(text):
    """拆 frontmatter，返回 (dict, 正文)。只认顶层 key: value 与 "- " 列表项。"""
    m = re.match(r'^---\s*\n(.*?)\n---\s*\n', text, re.S)
    if not m:
        return {}, text
    fm = {}
    for line in m.group(1).splitlines():
        if ':' in line and not line.startswith(' '):
            k, v = line.split(':', 1)
            fm[k.strip()] = v.strip()
        elif line.strip().startswith('- ') and fm:
            k = list(fm.keys())[-1]
            fm[k] = (fm[k] + ' ' if fm[k] else '') + line.strip()[2:]
    return fm, text[m.end():]



def cmd_libraries(args):
    """列出一个父目录下的所有库（含 文章/ 或 .ledger.json 的子目录）。"""
    root = os.path.abspath(args[0]) if args else os.getcwd()
    libs = []
    if os.path.isdir(root):
        for d in sorted(os.listdir(root)):
            p = os.path.join(root, d)
            if not os.path.isdir(p) or d.startswith('.'):
                continue
            if os.path.isdir(os.path.join(p, '文章')) or os.path.exists(os.path.join(p, '.ledger.json')):
                libs.append({'name': d, 'path': p,
                             'articles': len(numbered(os.path.join(p, '文章'))),
                             'summaries': len(numbered(os.path.join(p, '总结')))})
    out({'ok': True, 'root': root, 'libraries': libs})


def cmd_retitle(args):
    """把文章改成新标题，并联动：素材目录名、frontmatter、图片引用、文件名、ledger。

    为什么要这条：collect 时只能用原文标题给素材目录命名，而文章标题是模型读完
    正文后才定的，两边必然分叉。以前靠手写一次性脚本改，漏一处图片就裂。
    """
    if len(args) < 3:
        fail('用法: retitle <项目目录> <编号> <新标题>')
    project = os.path.abspath(args[0])
    try:
        idx = int(args[1])
    except ValueError:
        fail('编号必须是数字')
    title = ' '.join(args[2:]).strip()
    slug = safe_title(title)
    if not slug:
        fail('标题为空，或清洗 Windows 禁用字符后为空')

    art = article_file(project, idx)
    if not art:
        fail('文章 %d 不存在' % idx)

    mdir = material_dir(project, idx)
    old_dir = os.path.basename(mdir) if mdir else ''
    new_dir = '%d %s' % (idx, slug)
    changes = []

    if mdir and old_dir != new_dir:
        new_mdir = os.path.join(os.path.dirname(mdir), new_dir)
        if os.path.exists(new_mdir):
            fail('目标目录已存在，未改动任何文件: %s' % new_mdir)
        os.rename(mdir, new_mdir)
        changes.append('素材/%s → 素材/%s' % (old_dir, new_dir))
        mdir = new_mdir

    raw = open(art, 'rb').read().decode('utf-8')
    crlf = '\r\n' in raw
    text = raw.replace('\r\n', '\n')
    head, body = '', text
    m = re.match(r'^(---\s*\n.*?\n---\s*\n)', text, re.S)
    if m:
        head, body = m.group(1), text[m.end():]
    head = re.sub(r'(?m)^标题:.*$', '标题: ' + title, head, count=1)
    head = re.sub(r'(?m)^素材目录:.*$', '素材目录: 素材/' + new_dir, head, count=1)
    if re.search(r'(?m)^#\s+', body):
        body = re.sub(r'(?m)^#\s+.*$', lambda _: '# ' + title, body, count=1)
    else:
        body = '# %s\n\n' % title + body

    if old_dir and old_dir != new_dir:
        head = head.replace('素材/%s/' % old_dir, '素材/%s/' % new_dir)
        body = body.replace('素材/%s/' % old_dir, '素材/%s/' % new_dir)
        body = body.replace('../素材/%s/' % old_dir, '素材/%s/' % new_dir)

    new_text = head + body
    if crlf:
        new_text = new_text.replace('\n', '\r\n')

    def write_back(path):
        # newline='' 关掉自动转换，保留文件原本的行尾，避免整篇看起来被重写
        with open(path, 'w', encoding='utf-8', newline='') as f:
            f.write(new_text)

    new_art = os.path.join(os.path.dirname(art), '%d %s.md' % (idx, slug))
    if os.path.abspath(new_art) != os.path.abspath(art):
        if os.path.exists(new_art):
            fail('目标文件已存在，未改动: %s' % new_art)
        write_back(new_art)
        os.remove(art)
        changes.append('文章/%s → 文章/%s' % (os.path.basename(art),
                                            os.path.basename(new_art)))
    else:
        write_back(art)

    ledger = load_ledger(project)
    for e in ledger:
        if e.get('idx') == idx:
            e['article_title'] = title
            if mdir:
                e['content'] = os.path.relpath(
                    os.path.join(mdir, '原文.md'), project).replace('\\', '/')
    if ledger:
        json.dump(ledger, open(os.path.join(project, '.ledger.json'), 'w',
                               encoding='utf-8'), ensure_ascii=False, indent=2)

    imgs = 0
    if mdir and os.path.isdir(os.path.join(mdir, 'media')):
        imgs = len([f for f in os.listdir(os.path.join(mdir, 'media'))
                    if os.path.isfile(os.path.join(mdir, 'media', f))])
    broken = [r for r in re.findall(r'!\[\[([^\]|]+)', new_text)
              if not os.path.exists(os.path.join(project, *r.strip().split('/')))]
    out({'ok': True, 'article_idx': idx, 'title': title, 'slug': slug,
         'article': new_art, 'material_dir': mdir or '',
         'changes': changes or ['无变化（标题已一致）'],
         'media_count': imgs, 'broken_images': broken})


def cmd_check(args):
    """体检：把「应该没问题」换成一份可核对的问题清单。"""
    project, vault, test_links = None, None, False
    i = 0
    while i < len(args):
        a = args[i]
        if a == '--vault':
            vault = args[i + 1] if i + 1 < len(args) else None
            i += 2
            continue
        if a == '--links':
            test_links = True
        elif not a.startswith('--') and project is None:
            project = a
        i += 1
    if not project:
        fail('用法: check <项目目录> [--vault <路径>] [--links]')
    project = os.path.abspath(project)

    errors, warns = [], []
    arts = numbered(os.path.join(project, '文章'))
    sums = numbered(os.path.join(project, '总结'))
    ledger = load_ledger(project)

    for label, nums in (('文章', arts), ('总结', sums)):
        if nums:
            miss = [n for n in range(1, max(nums) + 1) if n not in nums]
            if miss:
                errors.append('%s编号跳号：缺 %s' % (label, ','.join(map(str, miss))))

    for n in arts:
        p = article_file(project, n)
        text = open(p, encoding='utf-8').read()
        fm, body = split_fm(text)
        name = os.path.basename(p)
        if not fm.get('标题'):
            errors.append('文章 %d 缺 frontmatter「标题」（%s）' % (n, name))
        if not re.search(r'(?m)^#\s+\S', body):
            warns.append('文章 %d 正文没有首行 H1（Obsidian 里认不出）' % n)
        mdir = material_dir(project, n)
        if not mdir:
            errors.append('文章 %d 找不到对应的素材目录' % n)
        else:
            a_slug = re.sub(r'^%d[ .]?' % n, '', name[:-3])
            d_slug = re.sub(r'^%d[ .]?' % n, '', os.path.basename(mdir))
            if a_slug and d_slug and a_slug != d_slug:
                warns.append('文章与素材标题片段不一致：「%s」vs「%s」（用 retitle 对齐）'
                             % (a_slug, d_slug))
            if not os.path.exists(os.path.join(mdir, '原文.md')):
                warns.append('素材 %d 缺 原文.md' % n)
        refs = re.findall(r'!\[\[([^\]|]+)', text) + \
            re.findall(r'!\[[^\]]*\]\(([^)]+)\)', text)
        for ref in refs:
            t = ref.strip().replace('\\', '/')
            while t.startswith('../'):
                t = t[3:]
            if not os.path.exists(os.path.join(project, *t.split('/'))):
                errors.append('文章 %d 图片找不到: %s' % (n, ref.strip()))

    led_idxs = set()
    for e in ledger:
        k = e.get('idx')
        led_idxs.add(k)
        if k not in arts:
            errors.append('台账第 %s 条在磁盘上没有对应文章' % k)
        c = e.get('content') or ''
        if not c or not os.path.exists(os.path.join(project, *c.split('/'))):
            errors.append('台账第 %s 条原文路径无效: %s' % (k, c))
        if not e.get('url'):
            warns.append('台账第 %s 条缺原文链接' % k)
    for n in arts:
        if n not in led_idxs:
            warns.append('文章 %d 不在台账里（手写的？）' % n)

    # 媒体体检：0 字节 = 下载失败留下的空壳（2026-11-13 实测：小红书图片 CDN
    # 偶发整批 403，提取器没重试，8 张全 0 字节还正常走完了收录流程）。
    for n in arts:
        mdir = material_dir(project, n)
        med = os.path.join(mdir, 'media') if mdir else None
        if not med or not os.path.isdir(med):
            continue
        for f in sorted(os.listdir(med)):
            p = os.path.join(med, f)
            if os.path.isfile(p) and os.path.getsize(p) < 1000:
                errors.append('素材 %d 的 media/%s 只有 %d 字节（下载失败空壳），'
                              '跑 repair-media %s 修复' % (n, f, os.path.getsize(p), n))

    covered = set()
    for m_ in sums:
        sp = numbered_file(os.path.join(project, '总结'), m_)
        fm, _ = split_fm(open(sp, encoding='utf-8').read())
        nums = [int(x) for x in re.findall(r'\d+', fm.get('覆盖文章', ''))]
        if not nums:
            warns.append('总结 %d 缺「覆盖文章」字段，无法判断覆盖范围' % m_)
        for x in nums:
            covered.add(x)
            if x not in arts:
                errors.append('总结 %d 声称覆盖文章 %d，但该文章不存在' % (m_, x))
    uncovered = [n for n in arts if n not in covered]

    cfg = load_cfg(project)
    target = vault or cfg.get('vault')
    if target and cfg.get('folder'):
        target = os.path.join(target, *str(cfg['folder']).split('/'))
    orphans = []
    if target and os.path.isdir(target):
        lib_rel = set(rel for rel, _ in iter_payload(project))
        orphans = sorted(r for r in vault_files(target) if r not in lib_rel)

    dead = []
    if test_links:
        for e in ledger:
            u = e.get('url')
            if not u:
                continue
            r = subprocess.run(['curl', '-sL', '-o', '/dev/null', '-w', '%{http_code}',
                                '--max-time', '12', '-A', UA, u],
                               capture_output=True, text=True)
            code = (r.stdout or '').strip()
            if not code.startswith('2'):
                dead.append({'article_idx': e.get('idx'), 'status': code, 'url': u})
                warns.append('文章 %s 原文链接可能失效（HTTP %s）' % (e.get('idx'), code))

    moc = os.path.join(project, '00-目录.md')
    if not os.path.exists(moc):
        warns.append('缺 00-目录.md（Obsidian 入口），跑一次 render.py moc 生成')
    else:
        newest = 0
        for sub in ('文章', '总结'):
            d = os.path.join(project, sub)
            if os.path.isdir(d):
                for f in os.listdir(d):
                    if f.endswith('.md'):
                        newest = max(newest, os.path.getmtime(os.path.join(d, f)))
        if newest > os.path.getmtime(moc) + 1:
            warns.append('00-目录.md 比文章/总结旧，可能已过期，重跑 render.py moc')

    out({'ok': True, 'passed': not errors, 'project': project,
         'stats': {'articles': len(arts), 'summaries': len(sums),
                   'ledger': len(ledger), 'uncovered_articles': uncovered},
         'errors': errors, 'warnings': warns,
         'vault': target or '', 'vault_orphans': orphans, 'dead_links': dead})



def reverse_platform(alias):
    return {v: k for k, v in PLATFORM_ALIAS.items()}.get(alias, alias)


def cmd_repair_media(args):
    """重抓指定编号的坏媒体：重跑提取器，只覆盖 0 字节/缺失的文件，好的不动。

    背景（2026-11-13 实测）：小红书图片 CDN 的签名直链会偶发整批 403，
    当时下载失败留下一批 0 字节空壳，且 URL 带时间戳签名、过期后无法原地重下，
    必须重新打开笔记页拿新鲜直链。本命令就是「重新打开页面 → 重新下载」的固化。
    """
    project, idx = None, None
    for a in args:
        if a.startswith('--'):
            continue
        if project is None:
            project = a
        elif idx is None:
            try:
                idx = int(a)
            except ValueError:
                fail('编号必须是数字: %s' % a)
    if not project or idx is None:
        fail('用法: repair-media <项目目录> <编号>')

    project = os.path.abspath(project)
    mdir = material_dir(project, idx)
    if not mdir:
        fail('素材 %d 不存在' % idx)
    med = os.path.join(mdir, 'media')
    os.makedirs(med, exist_ok=True)

    entry = next((e for e in load_ledger(project) if e.get('idx') == idx), None)
    if not entry or not entry.get('url'):
        fail('台账里没有编号 %d 的原文链接，无法重抓' % idx)

    bad = sorted(f for f in os.listdir(med)
                 if os.path.isfile(os.path.join(med, f))
                 and os.path.getsize(os.path.join(med, f)) < 1000)
    if not bad:
        out({'ok': True, 'article_idx': idx, 'fixed': [], 'still_broken': [],
             'note': '没有坏文件，无需修复'})

    incoming = os.path.join(project, '.incoming',
                            datetime.datetime.now().strftime('%Y%m%d%H%M%S%f'))
    os.makedirs(incoming, exist_ok=True)
    platform = reverse_platform(entry.get('platform') or '')
    produced, err = run_extractor(platform, entry['url'], incoming)
    if not produced:
        shutil.rmtree(incoming, ignore_errors=True)
        fail('重新提取失败：%s' % err)

    new_med = os.path.join(produced, 'media')
    fixed, still_broken = [], []
    for f in bad:
        src = os.path.join(new_med, f)
        dst = os.path.join(med, f)
        if os.path.isfile(src) and os.path.getsize(src) > 1000:
            shutil.copy2(src, dst)
            fixed.append({'file': f, 'bytes': os.path.getsize(dst)})
        else:
            still_broken.append({'file': f,
                                 'new_http': None, 'note': '重抓后仍未获得有效文件'})

    # 同步 _source/meta.json 的图片记录与 meta.md 的「图片：x/y」行，
    # 以磁盘为准逐张重算，别信提取器自己报的数。
    meta_json = os.path.join(mdir, '_source', 'meta.json')
    ok_cnt, total = 0, 0
    if os.path.exists(meta_json):
        try:
            mj = json.load(open(meta_json, encoding='utf-8'))
        except Exception:
            mj = None
        if mj is not None:
            new_mj_path = os.path.join(produced, '_source', 'meta.json')
            new_recs = {}
            if os.path.exists(new_mj_path):
                try:
                    new_recs = {r.get('file'): r for r in json.load(
                        open(new_mj_path, encoding='utf-8')).get('images', [])}
                except Exception:
                    new_recs = {}
            recs = mj.get('images') or []
            for r in recs:
                f = r.get('file')
                p = os.path.join(med, f) if f else None
                sz = os.path.getsize(p) if p and os.path.isfile(p) else 0
                r['bytes'] = sz
                r['http'] = '200' if sz > 1000 else (new_recs.get(f, {}).get('http') or r.get('http'))
                total += 1
                ok_cnt += 1 if sz > 1000 else 0
            mj['image_downloaded'] = ok_cnt
            with open(meta_json, 'w', encoding='utf-8', newline='') as fh:
                json.dump(mj, fh, ensure_ascii=False, indent=2)
    meta_md = os.path.join(mdir, 'meta.md')
    if os.path.exists(meta_md) and total:
        txt = open(meta_md, encoding='utf-8').read()
        txt = re.sub(r'图片：\d+/\d+', '图片：%d/%d' % (ok_cnt, total), txt)
        with open(meta_md, 'w', encoding='utf-8', newline='') as fh:
            fh.write(txt)

    shutil.rmtree(incoming, ignore_errors=True)
    if still_broken:
        fail('修复了 %d 个，仍有 %d 个失败：%s' % (len(fixed), len(still_broken),
             ', '.join(b['file'] for b in still_broken)),
             fixed=fixed, still_broken=still_broken)
    out({'ok': True, 'article_idx': idx, 'fixed': fixed,
         'still_broken': [], 'images_ok': '%d/%d' % (ok_cnt, total) if total else None})


def node_bin():
    for name in ('node', 'node.exe'):
        p = shutil.which(name)
        if p:
            return p
    return None


def default_profile(platform='xhs'):
    return os.path.join(os.path.expanduser('~'), '.workbuddy',
                        'browser-profile', platform)


def render_comments_md(idx, data, rows):
    """把评论渲染成可读 Markdown：主评论按赞数降序，回复按父级序号精确挂载。"""
    parents = [r for r in rows if not r.get('is_reply')]
    kids = {}
    for r in rows:
        if r.get('is_reply'):
            kids.setdefault(r.get('parent_idx', -1), []).append(r)
    parents.sort(key=lambda r: -(r.get('likes') or 0))

    fetched = (data.get('fetched_at') or '')[:19].replace('T', ' ')
    L = ['---', '类型: 评论区', '原文标题: %s' % (data.get('title') or ''),
         '评论总数: %d' % len(rows),
         '主评论数: %d' % len(parents),
         '页面标注总数: %s' % (data.get('total_label') or '未知'),
         '抓取时间: %s' % fetched, '---', '',
         '# 评论区 · %s' % (data.get('title') or ('文章 %d' % idx)), '',
         '共抓取 %d 条（主评论 %d 条、回复 %d 条），按点赞数从高到低排列。'
         % (len(rows), len(parents), len(rows) - len(parents)), '']
    if data.get('login_wall'):
        L += ['> 抓取时遇到登录墙（%s），数量可能不全。' % data['login_wall'], '']

    for i, p in enumerate(parents, 1):
        like = p.get('like') or ''
        like = '' if like in ('赞', '') else ' · %s 赞' % like
        meta = ' · '.join(x for x in [p.get('author'), p.get('location'), p.get('date')] if x)
        L.append('%d. **%s**%s  ' % (i, meta, like))
        L.append('   %s' % (p.get('text') or '').replace('\n', ' '))
        for k in kids.get(p.get('parent_idx', -1), []):
            kmeta = ' · '.join(x for x in [k.get('author'), k.get('date')] if x)
            L.append('   - ↳ %s：%s' % (kmeta, (k.get('text') or '').replace('\n', ' ')))
        L.append('')
    return '\n'.join(L).rstrip() + '\n'


def cmd_comments(args):
    if len(args) < 2:
        fail('用法: comments <项目目录> <编号> [--force] [--top N]')
    project = os.path.abspath(args[0])
    try:
        idx = int(args[1])
    except ValueError:
        fail('编号必须是数字: %s' % args[1])
    top = None
    if '--top' in args:
        try:
            top = int(args[args.index('--top') + 1])
        except (IndexError, ValueError):
            fail('--top 后面要跟数字')
    force = '--force' in args

    entry = None
    for e in load_ledger(project):
        if e.get('idx') == idx:
            entry = e
            break
    if not entry:
        fail('台账里没有编号 %d' % idx)

    url = entry.get('url') or ''
    plat = entry.get('platform') or ''
    is_xhs = re.search(r'(xiaohongshu\.com|xhslink\.com)', url)
    is_bili = re.search(r'(bilibili\.com|b23\.tv)', url)
    if not (is_xhs or is_bili):
        fail('评论区抓取目前支持小红书、B站，这条来自 %s' % (plat or '未知平台'))
    platform_key = 'xhs' if is_xhs else 'bili'
    script_name = 'xhs_comments.js' if is_xhs else 'bili_comments.js'

    mdir = material_dir(project, idx)
    if not mdir:
        fail('找不到编号 %d 的素材目录' % idx)
    out_json = os.path.join(mdir, 'comments.json')

    cached = ''
    if os.path.exists(out_json) and not force:
        data = json.load(open(out_json, encoding='utf-8'))
        cached = '已有 comments.json，跳过抓取（--force 重抓）'
    else:
        node = node_bin()
        if not node:
            fail('抓评论区需要 Node.js 22+，当前找不到 node')
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), script_name)
        if not os.path.exists(script):
            fail('缺少 %s' % script_name)
        cfg = load_cfg(project)
        profile_key = 'browser_profile_%s' % platform_key
        profile = cfg.get(profile_key) or default_profile(platform_key)
        cmd = [node, script, url, out_json]
        if is_xhs:
            cmd += ['--profile', profile]
        else:
            cmd += ['--profile', profile]
            if top:
                cmd += ['--max', str(top)]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding='utf-8', errors='replace')
        if r.returncode != 0 or not os.path.exists(out_json):
            tail = ((r.stderr or '') + (r.stdout or '')).strip()[-400:]
            fail('抓取失败：%s' % (tail or '未知错误'), need_login='未登录' in tail)
        data = json.load(open(out_json, encoding='utf-8'))
        if not cfg.get(profile_key):
            cfg[profile_key] = profile
            save_cfg(project, cfg)

    rows = data.get('rows') or []
    md = render_comments_md(idx, data, rows)
    md_path = os.path.join(mdir, '评论.md')
    with open(md_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(md)

    total = data.get('total_label') or ''
    partial = False
    if total.isdigit() and rows:
        partial = len(rows) < int(total)
    wall = data.get('login_wall') or ''
    note = ''
    if partial:
        note = '抓到 %d 条，页面标注 %s 条，可能不全' % (len(rows), total)
    if wall:
        note = (note + '；' if note else '') + \
            ('需要登录：运行 login %s 扫码，然后 comments <库> %d --force 重抓'
             % (platform_key, idx))
    out({'ok': True, 'article_idx': idx, 'collected': len(rows),
         'top_level': len([r for r in rows if not r.get('is_reply')]),
         'replies': len([r for r in rows if r.get('is_reply')]),
         'total_label': total, 'partial': partial,
         'login_wall': wall, 'platform': platform_key,
         'json': out_json, 'markdown': md_path,
         'cached': cached,
         'note': note})


def cmd_collect(args):
    if len(args) < 2:
        fail('用法: collect <项目目录> <链接>')
    project = os.path.abspath(args[0])
    url = args[1].strip()
    ensure_project(project)

    platform = detect_platform(url)
    if not platform:
        fail('暂不支持该链接，可处理：小红书 / B站 / 小宇宙 / 公众号')
    url = resolve_short(url)

    ledger = load_ledger(project)
    for e in ledger:
        if canon(e.get('url', '')) == canon(url):
            idx = e.get('idx')
            art = article_file(project, idx)
            mdir = material_dir(project, idx)
            files = []
            if mdir and os.path.isdir(os.path.join(mdir, 'media')):
                files = sorted(os.listdir(os.path.join(mdir, 'media')))
            out({'ok': True, 'duplicate': True, 'article_idx': idx,
                 'platform': e.get('platform'), 'title': e.get('title'),
                 'author': e.get('author'), 'publish_time': e.get('publish_time'),
                 'raw_content': os.path.join(project, *e.get('content', '').split('/')),
                 'article_target': art,
                 'article_exists': bool(art),
                 'media_dir': mdir or '', 'media_count': len(files),
                 'note': '该链接已收录，未重复下载'})

    incoming = os.path.join(project, '.incoming', datetime.datetime.now().strftime('%Y%m%d%H%M%S%f'))
    os.makedirs(incoming, exist_ok=True)

    if platform == 'wechat':
        produced, err = wechat_fetch(url, incoming)
    else:
        produced, err = run_extractor(platform, url, incoming)
    if not produced:
        shutil.rmtree(incoming, ignore_errors=True)
        fail('%s 采集失败：%s' % (PLATFORM_ALIAS.get(platform, platform), err),
             platform=platform, url=url)

    idx = next_idx(project, 'article')
    title = os.path.basename(produced).rstrip()
    if not title or title == 'wechat':
        title = '(无标题)'
    slug = safe_title(title)
    dirname = ('%d %s' % (idx, slug)) if slug else str(idx)
    dest = os.path.join(project, '素材', dirname)
    os.makedirs(dest, exist_ok=True)

    content_src = os.path.join(produced, 'content.md')
    if not os.path.exists(content_src):
        shutil.rmtree(incoming, ignore_errors=True)
        fail('采集产物缺少 content.md，未落盘', platform=platform)
    shutil.copy2(content_src, os.path.join(dest, '原文.md'))

    meta_src = os.path.join(produced, 'meta.md')
    if os.path.exists(meta_src):
        shutil.copy2(meta_src, os.path.join(dest, 'meta.md'))
    for sub in ('media', '_source'):
        src = os.path.join(produced, sub)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(dest, sub), dirs_exist_ok=True)

    author, pub = parse_meta(os.path.join(dest, 'meta.md'))

    media_files = []
    mdir = os.path.join(dest, 'media')
    if os.path.isdir(mdir):
        media_files = sorted(f for f in os.listdir(mdir) if os.path.isfile(os.path.join(mdir, f)))

    entry = {'idx': idx, 'platform': PLATFORM_ALIAS.get(platform, platform),
             'url': url, 'title': title, 'author': author, 'publish_time': pub,
             'captured_at': now(),
             'content': os.path.relpath(os.path.join(dest, '原文.md'), project).replace('\\', '/')}
    ledger.append(entry)
    json.dump(ledger, open(os.path.join(project, '.ledger.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)

    shutil.rmtree(incoming, ignore_errors=True)
    out({'ok': True, 'article_idx': idx, 'platform': entry['platform'],
         'title': title, 'title_slug': slug, 'author': author, 'publish_time': pub,
         'raw_content': os.path.join(dest, '原文.md'),
         'media_dir': mdir, 'media_count': len(media_files),
         'media_files': media_files,
         'article_target': os.path.join(project, '文章',
                                        ('%d %s.md' % (idx, slug)) if slug else '%d.md' % idx)})


def load_cfg(project):
    p = os.path.join(project, '.config.json')
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding='utf-8'))
        except Exception:
            return {}
    return {}


def save_cfg(project, cfg):
    json.dump(cfg, open(os.path.join(project, '.config.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)


def iter_payload(project):
    """遍历要发布到 vault 的文件，产出 (相对路径, 绝对源路径)。

    排除内部文件：.incoming / .ledger.json / .config.json / meta.md / _source(原始 html)
    """
    for sub in ('文章', '总结', '素材'):
        base = os.path.join(project, sub)
        if not os.path.isdir(base):
            continue
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d != '_source']
            for f in files:
                if f.startswith('.') or f == 'meta.md':
                    continue
                src = os.path.join(root, f)
                yield os.path.relpath(src, project).replace('\\', '/'), src
    # 库根目录的笔记（00-目录.md、用户自己的备注）也一并镜像
    for f in sorted(os.listdir(project)):
        p = os.path.join(project, f)
        if os.path.isfile(p) and f.endswith('.md') and not f.startswith('.'):
            yield f, p


def same_content(a, b):
    """两个文件内容是否等价。

    只比字节会把「CRLF vs LF」误判成改动：Windows 上写文件默认 CRLF，
    库和 vault 因此可能字节数不同而内容一致。文本文件按归一化行尾比较。
    """
    try:
        ba = open(a, 'rb').read()
        bb = open(b, 'rb').read()
    except OSError:
        return False
    if ba == bb:
        return True
    if os.path.splitext(a)[1].lower() in ('.md', '.txt', '.html', '.htm', '.json', '.csv'):
        return ba.replace(b'\r\n', b'\n') == bb.replace(b'\r\n', b'\n')
    return False


def vault_files(target):
    """列出 vault 里属于镜像范围的文件（相对路径，斜杠统一）。"""
    res = []
    for sub in ('文章', '总结', '素材'):
        base = os.path.join(target, sub)
        if not os.path.isdir(base):
            continue
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d != '_source']
            for f in files:
                if f.startswith('.') or f == 'meta.md':
                    continue
                res.append(os.path.relpath(os.path.join(root, f), target)
                           .replace('\\', '/'))
    for f in sorted(os.listdir(target)):
        p = os.path.join(target, f)
        if os.path.isfile(p) and f.endswith('.md') and not f.startswith('.'):
            res.append(f)
    return res


def cmd_publish(args):
    """把库内容镜像到 Obsidian vault（复制，不改动源库）。

    冲突保护：vault 里的同名文件若比库里新（你手动改过），跳过并报告，绝不覆盖。
    """
    project, vault, folder, dry, prune = None, None, None, False, False
    i = 0
    while i < len(args):
        a = args[i]
        if a == '--vault':
            vault = args[i + 1] if i + 1 < len(args) else None
            i += 2
            continue
        if a == '--folder':
            folder = args[i + 1] if i + 1 < len(args) else None
            i += 2
            continue
        if a == '--dry-run':
            dry = True
        elif a == '--prune':
            prune = True
        elif not a.startswith('--') and project is None:
            project = a
        i += 1
    if not project:
        fail('用法: publish <项目目录> --vault <Obsidian 目标目录> '
             '[--folder 主题名] [--prune] [--dry-run]')
    project = os.path.abspath(project)
    ensure_project(project)

    cfg = load_cfg(project)
    base = vault or cfg.get('vault') or ''
    if not base:
        fail('未设置 Obsidian 目标目录，首次使用请带 --vault <路径>')
    base = os.path.abspath(base)
    if not vault:
        base = os.path.abspath(cfg['vault'])
    # 主题文件夹：--folder 指定或沿用上次；目标 = vault/<主题>/，
    # 文章、总结、素材全部收进去，不存在就新建。一个 vault 可以放多个主题互不干扰。
    folder = folder or cfg.get('folder') or ''
    if folder:
        folder = folder.strip().strip('\\/').replace('\\', '/')
        if not folder or folder.startswith('.') or ':' in folder:
            fail('非法的主题文件夹名: %r' % folder)
    target = os.path.join(base, *folder.split('/')) if folder else base

    if target == project:
        fail('vault 目录与库目录相同，无需发布')
    if target.startswith(project + os.sep):
        fail('vault 目录不能放在库目录内部')
    if not dry and not os.path.isdir(target):
        os.makedirs(target, exist_ok=True)

    render = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'render.py')
    if os.path.exists(render) and not dry:
        # 目录在「库里」生成，然后作为普通文件镜像过去；
        # 若在 vault 里再生成一次，时间戳会不同，下一轮就变成假冲突
        subprocess.run([sys.executable, render, 'moc', project],
                       capture_output=True, text=True)

    payload = list(iter_payload(project))
    lib_rel = set(rel for rel, _ in payload)

    copied, same, conflicts = 0, 0, []
    for rel, src in payload:
        dst = os.path.join(target, *rel.split('/'))
        if os.path.exists(dst):
            try:
                # 内容一致就算没变，不管 mtime：库被重写过但内容没动的情况很多
                if same_content(dst, src):
                    same += 1
                    continue
                if os.path.getmtime(dst) > os.path.getmtime(src) + 1:
                    conflicts.append(rel)
                    continue
            except OSError:
                pass
        if not dry:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
        copied += 1

    orphans = sorted(r for r in vault_files(target) if r not in lib_rel) \
        if os.path.isdir(target) else []
    pruned = []
    if prune:
        for rel in orphans:
            if dry:
                pruned.append(rel)
                continue
            p = os.path.join(target, *rel.split('/'))
            try:
                os.remove(p)
                pruned.append(rel)
            except OSError:
                pass
        if not dry:
            for sub in ('文章', '总结', '素材'):
                base = os.path.join(target, sub)
                if not os.path.isdir(base):
                    continue
                for root, dirs, files in os.walk(base, topdown=False):
                    if not os.listdir(root):
                        try:
                            os.rmdir(root)
                        except OSError:
                            pass

    if not dry:
        cfg['vault'] = base
        cfg['folder'] = folder
        cfg['last_publish'] = now()
        save_cfg(project, cfg)
        if os.path.exists(render):
            subprocess.run([sys.executable, render, 'index', target],
                           capture_output=True, text=True)

    out({'ok': True, 'dry_run': dry, 'vault': target, 'folder': folder,
         'copied': copied, 'unchanged': same, 'conflicts': conflicts,
         'conflict_note': '这些文件在 Obsidian 里被改过，未覆盖' if conflicts else '',
         'orphans': [r for r in orphans if r not in pruned],
         'pruned': pruned,
         'orphan_note': ('有 %d 个文件在库里已不存在（改名或删除的残留）'
                         '，加 --prune 可清掉' % len(orphans)) if orphans and not prune else ''})


def cmd_login(args):
    node = node_bin()
    if not node:
        fail('需要 Node.js 22+')
    platform = next((a for a in args if not a.startswith('--')), 'xhs')
    if platform in ('xhs', 'xiaohongshu'):
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'xhs_login.js')
        profile = default_profile('xhs')
        cmd = [node, script]
    elif platform in ('bili', 'bilibili', 'douyin'):
        key = 'bili' if platform in ('bili', 'bilibili') else 'douyin'
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'login.js')
        profile = default_profile(key)
        cmd = [node, script, key]
    else:
        fail('未知平台 %r（支持 xhs / bili / douyin）' % platform)
    if not os.path.exists(script):
        fail('缺少 %s' % os.path.basename(script))
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    ok = 'LOGIN_OK' in (r.stdout or '')
    out({'ok': ok, 'platform': platform,
         'profile': profile,
         'log': ((r.stdout or '') + (r.stderr or '')).strip()[-600:],
         'note': '登录态已保存，后续抓取直接复用' if ok else '未完成登录'})


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd, args = sys.argv[1], sys.argv[2:]
    handler = {'init': cmd_init, 'collect': cmd_collect, 'retitle': cmd_retitle,
               'next': cmd_next, 'status': cmd_status, 'check': cmd_check,
               'comments': cmd_comments, 'login': cmd_login,
               'libraries': cmd_libraries, 'publish': cmd_publish,
               'repair-media': cmd_repair_media}.get(cmd)
    if not handler:
        fail('未知子命令: %s（可用 init/collect/retitle/next/status/check/'
             'comments/login/libraries/publish/repair-media）' % cmd)
    handler(args)


if __name__ == '__main__':
    main()

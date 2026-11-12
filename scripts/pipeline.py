# -*- coding: utf-8 -*-
"""
link-digest 工作区管理 + 内容采集。

子命令:
    init    <项目目录>                     建目录骨架（文章/ 总结/ 素材/）
    collect <项目目录> <链接>              采集链接 → 素材/<n>/，并追加 ledger
    next    <项目目录> --type article|summary   输出下一个可用编号
    status  <项目目录>                     输出当前收录情况
    libraries <父目录>                    列出该目录下的所有库
    publish <项目目录> --vault <路径>       把库里内容镜像到 Obsidian（含冲突保护）

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
    """去参数做重复判定：同一条笔记的分享链接 xsec_token 每次都不同。"""
    return re.sub(r'[?#].*$', '', url.strip()).rstrip('/')


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
            out({'ok': True, 'duplicate': True, 'article_idx': e.get('idx'),
                 'platform': e.get('platform'), 'title': e.get('title'),
                 'author': e.get('author'), 'publish_time': e.get('publish_time'),
                 'raw_content': os.path.join(project, *e.get('content', '').split('/')),
                 'article_target': os.path.join(project, '文章', '%s.md' % e.get('idx')),
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


def cmd_publish(args):
    """把库内容镜像到 Obsidian vault（复制，不改动源库）。

    冲突保护：vault 里的同名文件若比库里新（你手动改过），跳过并报告，绝不覆盖。
    """
    project, vault, dry = None, None, False
    i = 0
    while i < len(args):
        a = args[i]
        if a == '--vault':
            vault = args[i + 1] if i + 1 < len(args) else None
            i += 2
            continue
        if a == '--dry-run':
            dry = True
        elif not a.startswith('--') and project is None:
            project = a
        i += 1
    if not project:
        fail('用法: publish <项目目录> --vault <Obsidian 目标目录> [--dry-run]')
    project = os.path.abspath(project)
    ensure_project(project)

    cfg = load_cfg(project)
    target = os.path.abspath(vault or cfg.get('vault') or '')
    if not vault and not cfg.get('vault'):
        fail('未设置 Obsidian 目标目录，首次使用请带 --vault <路径>')
    if not vault:
        target = os.path.abspath(cfg['vault'])

    if target == project:
        fail('vault 目录与库目录相同，无需发布')
    if target.startswith(project + os.sep):
        fail('vault 目录不能放在库目录内部')

    copied, same, conflicts = 0, 0, []
    for rel, src in iter_payload(project):
        dst = os.path.join(target, *rel.split('/'))
        if os.path.exists(dst):
            try:
                if os.path.getmtime(dst) > os.path.getmtime(src) + 1:
                    conflicts.append(rel)
                    continue
                if abs(os.path.getmtime(dst) - os.path.getmtime(src)) <= 1:
                    same += 1
                    continue
            except OSError:
                pass
        if not dry:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
        copied += 1

    if not dry:
        cfg['vault'] = target
        cfg['last_publish'] = now()
        save_cfg(project, cfg)
        render = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'render.py')
        if os.path.exists(render):
            subprocess.run([sys.executable, render, 'index', target],
                           capture_output=True, text=True)

    out({'ok': True, 'dry_run': dry, 'vault': target,
         'copied': copied, 'unchanged': same, 'conflicts': conflicts,
         'conflict_note': '这些文件在 Obsidian 里被改过，未覆盖' if conflicts else ''})


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd, args = sys.argv[1], sys.argv[2:]
    handler = {'init': cmd_init, 'collect': cmd_collect, 'next': cmd_next,
               'status': cmd_status, 'libraries': cmd_libraries,
               'publish': cmd_publish}.get(cmd)
    if not handler:
        fail('未知子命令: %s（可用 init/collect/next/status/libraries/publish）' % cmd)
    handler(args)


if __name__ == '__main__':
    main()

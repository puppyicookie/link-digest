# -*- coding: utf-8 -*-
"""
生成库索引网页：把 文章/*.md 与 总结/*.md 渲染成一个可离线打开的单文件 HTML。

用法:
    python render.py index <库目录>

产物: <库目录>/索引.html
无外部依赖：内置极简 Markdown 渲染（标题/表格/列表/粗体/行内码/图片/引用/分隔线）。
"""
import html
import json
import os
import re
import sys

CSS = """
*{box-sizing:border-box}
body{margin:0;background:#f6f5f2;color:#1f1f1d;
 font:15px/1.75 -apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:860px;margin:0 auto;padding:40px 24px 80px}
header h1{margin:0 0 6px;font-size:26px;font-weight:600;letter-spacing:.5px}
header p{margin:0;color:#6b6a65;font-size:13px}
nav{margin:24px 0 8px;display:flex;gap:8px;flex-wrap:wrap}
nav a{font-size:13px;color:#4a4a45;text-decoration:none;border:1px solid #dcdad3;
 background:#fff;padding:5px 12px;border-radius:999px}
nav a:hover{border-color:#b9b6ac}
h2.sec{font-size:14px;font-weight:600;color:#6b6a65;letter-spacing:2px;
 margin:40px 0 14px;padding-bottom:8px;border-bottom:1px solid #e2e0d8}
details{background:#fff;border:1px solid #e5e3dc;border-radius:10px;margin-bottom:10px;
 padding:0 18px}
details[open]{padding-bottom:18px}
summary{cursor:pointer;list-style:none;padding:15px 0;display:flex;gap:14px;align-items:baseline}
summary::-webkit-details-marker{display:none}
.idx{font:600 13px/1 ui-monospace,Consolas,monospace;color:#b0ada2;min-width:26px}
.s-title{font-size:16px;font-weight:600;line-height:1.4}
.s-meta{font-size:12.5px;color:#8b8a83;margin-top:3px}
article{border-top:1px dashed #eceae3;padding-top:16px;font-size:14.5px}
article h2{font-size:17px;font-weight:600;margin:22px 0 8px}
article h3{font-size:15px;font-weight:600;margin:18px 0 6px}
article p{margin:8px 0}
article ul,article ol{margin:8px 0;padding-left:22px}
article li{margin:3px 0}
article table{border-collapse:collapse;width:100%;margin:12px 0;font-size:13.5px}
article th,article td{border:1px solid #e5e3dc;padding:7px 10px;text-align:left}
article th{background:#faf9f6;font-weight:600}
article img{max-width:100%;border-radius:8px;margin:10px 0;border:1px solid #eceae3}
article code{background:#f2f0ea;padding:1px 5px;border-radius:4px;font-size:13px}
article blockquote{margin:10px 0;padding:8px 14px;border-left:3px solid #dcdad3;color:#6b6a65}
article hr{border:0;border-top:1px dashed #e5e3dc;margin:20px 0}
.empty{color:#8b8a83;font-size:14px;padding:20px 0}
"""


def esc(s):
    return html.escape(s, quote=False)


def inline(s):
    s = esc(s)
    s = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', s)
    s = re.sub(r'`([^`]+)`', r'<code>\1</code>', s)
    # Obsidian wiki 嵌入 ![[素材/.../xx.jpg]]：路径相对库根，index.html 也在库根，直接可用
    s = re.sub(r'!\[\[([^\]]+)\]\]',
               lambda m: '<img alt="" src="%s">' % fix_src(m.group(1)), s)
    s = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', lambda m: '<img alt="%s" src="%s">'
               % (m.group(1), fix_src(m.group(2))), s)
    return s


def fix_src(p):
    p = p.replace('\\', '/')
    while p.startswith('../'):
        p = p[3:]
    return p


def strip_fm(text):
    fm, body = {}, text
    m = re.match(r'^---\s*\n(.*?)\n---\s*\n', text, re.S)
    if m:
        body = text[m.end():]
        for line in m.group(1).splitlines():
            if ':' in line and not line.startswith(' '):
                k, v = line.split(':', 1)
                fm[k.strip()] = v.strip()
            elif line.strip().startswith('- ') and fm:
                last = list(fm.keys())[-1]
                if not fm[last]:
                    fm[last] = line.strip()[2:]
                else:
                    fm[last] = fm[last] + ' / ' + line.strip()[2:]
    return fm, body


def md2html(md):
    lines = md.split('\n')
    out, i = [], 0
    while i < len(lines):
        ln = lines[i]
        s = ln.strip()

        if not s:
            i += 1
            continue
        if re.fullmatch(r'-{3,}|\*{3,}', s):
            out.append('<hr>')
            i += 1
            continue
        m = re.match(r'^(#{1,4})\s+(.*)$', s)
        if m:
            lv = 2 if len(m.group(1)) <= 2 else len(m.group(1))
            out.append('<h%d>%s</h%d>' % (lv, inline(m.group(2)), lv))
            i += 1
            continue
        if s.startswith('|') and i + 1 < len(lines) and re.match(r'^\|[\s:|-]+\|$', lines[i + 1].strip()):
            rows = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                rows.append(lines[i].strip())
                i += 1
            cells = [[c.strip() for c in r.strip('|').split('|')] for r in rows]
            head = cells[0]
            body = [c for c in cells[1:] if not re.fullmatch(r'[\s:|-]+', ''.join(c))]
            t = ['<table><thead><tr>']
            t += ['<th>%s</th>' % inline(c) for c in head]
            t.append('</tr></thead><tbody>')
            for r in body:
                t.append('<tr>' + ''.join('<td>%s</td>' % inline(c) for c in r) + '</tr>')
            t.append('</tbody></table>')
            out.append(''.join(t))
            continue
        if s.startswith('>'):
            buf = []
            while i < len(lines) and lines[i].strip().startswith('>'):
                buf.append(lines[i].strip().lstrip('>').strip())
                i += 1
            out.append('<blockquote>%s</blockquote>' % inline(' '.join(buf)))
            continue
        m = re.match(r'^[-*]\s+(.*)$', s)
        if m:
            buf = []
            while i < len(lines) and re.match(r'^[-*]\s+', lines[i].strip()):
                buf.append(re.sub(r'^[-*]\s+', '', lines[i].strip()))
                i += 1
            out.append('<ul>' + ''.join('<li>%s</li>' % inline(x) for x in buf) + '</ul>')
            continue
        m = re.match(r'^\d+[.)]\s+(.*)$', s)
        if m:
            buf = []
            while i < len(lines) and re.match(r'^\d+[.)]\s+', lines[i].strip()):
                buf.append(re.sub(r'^\d+[.)]\s+', '', lines[i].strip()))
                i += 1
            out.append('<ol>' + ''.join('<li>%s</li>' % inline(x) for x in buf) + '</ol>')
            continue
        buf = []
        while i < len(lines) and lines[i].strip() and not re.match(
                r'^(#{1,4}\s|[-*]\s|\d+[.)]\s|>|\|)', lines[i].strip()):
            buf.append(lines[i].strip())
            i += 1
        if buf:
            out.append('<p>%s</p>' % inline(' '.join(buf)))
    return '\n'.join(out)


def collect(path):
    items = []
    if not os.path.isdir(path):
        return items
    for f in os.listdir(path):
        m = re.match(r'^(\d+)(?:[ .].*)?\.md$', f)
        if not m:
            continue
        p = os.path.join(path, f)
        text = open(p, encoding='utf-8').read()
        fm, body = strip_fm(text)
        h1 = ''
        m2 = re.match(r'^\s*#\s+(.+?)\s*\n', body)
        if m2:
            h1 = m2.group(1)
            body = body[m2.end():]
        items.append({'idx': int(m.group(1)), 'file': p, 'fm': fm,
                      'h1': h1, 'body': body})
    items.sort(key=lambda x: x['idx'])
    return items


def card(it, kind):
    fm = it['fm']
    if kind == 'article':
        title = fm.get('标题') or it.get('h1') or '未命名'
        meta = ' · '.join(x for x in [fm.get('来源平台'), fm.get('原作者'),
                                      fm.get('发布时间')] if x)
    else:
        title = it.get('h1') or ('总结 %s' % it['idx'])
        cover = fm.get('覆盖文章', '')
        meta = ('覆盖文章 ' + cover) if cover else ''
    return ('<details><summary><span class="idx">%02d</span><div>'
            '<div class="s-title">%s</div><div class="s-meta">%s</div>'
            '</div></summary><article>%s</article></details>'
            % (it['idx'], esc(title), esc(meta), md2html(it['body'])))


def read_doc(p):
    """返回 (frontmatter, 去掉 H1 的正文, H1)。"""
    text = open(p, encoding='utf-8').read()
    fm, body = strip_fm(text)
    h1 = ''
    m = re.match(r'^\s*#\s+(.+?)\s*\n', body)
    if m:
        h1 = m.group(1)
        body = body[m.end():]
    return fm, body, h1


def one_line(body, limit=70):
    """一句话摘要：优先取 `## 一句话摘要` 下首段，否则取正文首段。"""
    m = re.search(r'(?ms)^##\s*一句话摘要\s*\n(.*?)(?=\n##\s|\Z)', body)
    seg = m.group(1) if m else body
    for para in re.split(r'\n\s*\n', seg.strip()):
        s = ' '.join(x.strip() for x in para.splitlines() if x.strip())
        s = re.sub(r'^[-*>\s]+', '', s).strip()
        if not s:
            continue
        if len(s) <= limit:
            return s
        head = s[:limit]
        cut = max(head.rfind(x) for x in '。！？；')
        if cut >= limit * 0.6:
            return head[:cut + 1]          # 停在句末，读起来是完整的
        if head[-1].isascii() and head[-1].isalnum():
            sp = head.rfind(' ')           # 别把英文单词切一半
            if sp >= limit * 0.5:
                head = head[:sp]
        return head.rstrip('，、, ') + '…'
    return ''


def wiki(root, p, label):
    """Obsidian 双链；文件名含 # | [ ] 时降级为纯文本，避免链接语法被破坏。"""
    rel = os.path.relpath(p, root).replace('\\', '/')
    if rel.lower().endswith('.md'):
        rel = rel[:-3]
    if re.search(r'[#|\[\]]', rel):
        return label
    return '[[%s|%s]]' % (rel, label) if label != rel else '[[%s]]' % rel


def build_moc(root):
    """生成 Obsidian 入口笔记 00-目录.md：双链 + 按篇一句话 + 待总结提示。"""
    arts, sums = collect(os.path.join(root, '文章')), collect(os.path.join(root, '总结'))
    covered = set()
    sum_items = []
    for s in sums:
        fm, body, h1 = read_doc(s['file'])
        title = h1 or ('总结 %s' % s['idx'])
        # H1 里常带「（覆盖文章 1—2）」，覆盖范围单独一列展示，标题里去掉避免重复
        title = re.sub(r'\s*[（(]覆盖文章[^）)]*[）)]\s*$', '', title).strip() or title
        nums = [int(x) for x in re.findall(r'\d+', fm.get('覆盖文章', ''))]
        covered.update(nums)
        rng = '覆盖文章 %s' % ('、'.join(map(str, nums)) if nums else '未标注')
        sum_items.append((s['idx'], title, rng, s['file']))

    art_items = []
    for a in arts:
        fm, body, h1 = read_doc(a['file'])
        title = fm.get('标题') or h1 or ('文章 %s' % a['idx'])
        meta = ' · '.join(x for x in [fm.get('来源平台'), fm.get('原作者'),
                                      fm.get('发布时间')] if x)
        tags = [t for t in re.split(r'[,，\s]+', fm.get('标签', '')) if t]
        art_items.append((a['idx'], title, meta, one_line(body),
                          ['#' + t.lstrip('#') for t in tags], a['file']))

    pending = [a[0] for a in art_items if a[0] not in covered]
    ts = __import__('datetime').datetime.now().astimezone().isoformat(timespec='seconds')
    day = ts[:10]

    # 只写日期不写时刻：同一天内重复生成应得到完全相同的内容，
    # 否则每次 publish 都会因为时间戳不同而多复制一个文件、也是假改动
    L = ['---', '类型: 目录', '文章数: %d' % len(arts), '总结数: %d' % len(sums),
         '更新时间: %s' % day, '---', '', '# %s' % os.path.basename(root.rstrip('\\/')), '']
    L.append('%d 篇文章 · %d 份总结 · 最后更新 %s' % (len(arts), len(sums), day))

    L += ['', '## 总结', '']
    if sum_items:
        for idx, title, rng, f in sum_items:
            L.append('- %s —— %s' % (wiki(root, f, '%d · %s' % (idx, title)), rng))
    else:
        L.append('还没有总结。')

    L += ['', '## 文章', '']
    if art_items:
        for idx, title, meta, summary, tags, f in art_items:
            L.append('%d. %s' % (idx, wiki(root, f, title)))
            line = '   ' + (meta or '')
            if summary:
                line += (' ｜ ' if meta else '   ') + summary
            L.append(line.rstrip())
            if tags:
                L.append('   ' + ' '.join(tags))
    else:
        L.append('还没有文章。')

    if pending:
        L += ['', '## 待总结', '',
              '这些文章还没进任何一份总结：%s' % '、'.join(map(str, pending))]

    out = os.path.join(root, '00-目录.md')
    with open(out, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('\n'.join(L).rstrip() + '\n')
    return out, len(arts), len(sums), pending


def main():
    if len(sys.argv) < 3 or sys.argv[1] not in ('index', 'moc', 'all'):
        print('用法: python render.py index|moc|all <库目录>')
        sys.exit(1)
    mode, root = sys.argv[1], os.path.abspath(sys.argv[2])

    if mode in ('moc', 'all'):
        moc, na, ns, pending = build_moc(root)
        print(json.dumps({'ok': True, 'moc': moc, 'articles': na, 'summaries': ns,
                          'pending_articles': pending}, ensure_ascii=False))
        if mode == 'moc':
            return

    arts = collect(os.path.join(root, '文章'))
    sums = collect(os.path.join(root, '总结'))

    name = os.path.basename(root.rstrip('\\/'))
    parts = ['<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width,initial-scale=1">',
             '<title>%s</title><style>%s</style></head><body><div class="wrap">' % (esc(name), CSS),
             '<header><h1>%s</h1><p>%d 篇文章 · %d 份总结</p></header>'
             % (esc(name), len(arts), len(sums)),
             '<nav><a href="#articles">文章</a><a href="#summaries">总结</a></nav>',
             '<h2 class="sec" id="articles">文章</h2>']
    parts += [card(a, 'article') for a in arts] or ['<p class="empty">还没有文章</p>']
    parts.append('<h2 class="sec" id="summaries">总结</h2>')
    parts += [card(s, 'summary') for s in sums] or ['<p class="empty">还没有总结</p>']
    parts.append('</div></body></html>')

    out = os.path.join(root, 'index.html')
    open(out, 'w', encoding='utf-8').write('\n'.join(parts))
    print(json.dumps({'ok': True, 'index': out, 'articles': len(arts),
                      'summaries': len(sums)}, ensure_ascii=False))


if __name__ == '__main__':
    main()

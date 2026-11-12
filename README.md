# link-digest

把小红书 / B站 / 小宇宙 / 公众号的链接，收成一个持续生长的个人内容库：链接变成文章，文章累积成总结，确认后一键同步进 Obsidian。

A WorkBuddy skill that turns shared links into numbered Markdown articles, then re-summarizes them into themed digests, with a one-command publish step into an Obsidian vault.

## 它解决什么

刷到一篇好东西，收藏夹里塞了三个月，再也没打开过。这个技能把「收藏」变成「读过的笔记」：

- 一条链接 → 一篇 1500 字左右的可读文章（不是复制粘贴原文，是重新整理结构）
- 攒够几篇 → 一份按主题重组的总结，把几篇的说法合在一起讲
- 觉得写得好 → 一键进 Obsidian，图片和双链都是原生的

## 安装

把这个仓库放进 WorkBuddy 的技能目录：

```bash
git clone https://github.com/<你的账号>/link-digest.git ~/.workbuddy/skills/link-digest
```

Windows 下路径是 `C:\Users\<用户名>\.workbuddy\skills\link-digest`。

装完后**新开一个会话**（技能列表在会话启动时加载），然后发链接说「收录」即可。

### 依赖

| 依赖 | 说明 |
|---|---|
| Python 3.9+ | 只用标准库，无需 pip 安装 |
| curl | 抓页面与下载图片 |
| [media-extract](https://www.workbuddy.cn) 技能 | 可选。小红书/B站/小宇宙的提取器复用它的脚本；缺失时这三类链接会报错，公众号有内置兜底 |

## 目录结构

一个库就是一个普通文件夹：

```
文章库/
├── 文章/        1 用 NotebookLM 搞定期末复习.md
├── 总结/        1 NotebookLM 怎么用才有效.md
├── 素材/        1 用 NotebookLM 搞定期末复习/
│   └── <n> 标题/   原文.md  meta.md  media/  _source/
├── index.html    可离线打开的单文件阅读页
└── .ledger.json  收录台账（编号、链接、作者、时间）
```

编号在库内独立递增，`文章/` 和 `总结/` 各一套，从不覆盖已有文件。可以按主题建多个库。

## 用法

```bash
# 建库
python scripts/pipeline.py init ~/文章库

# 收录一条链接（返回 JSON，含编号、标题、素材路径）
python scripts/pipeline.py collect ~/文章库 "<链接>"

# 改文章标题：联动素材目录名、文件名、frontmatter、图片引用、台账
python scripts/pipeline.py retitle ~/文章库 3 "新的文章标题"

# 取下一个可用编号
python scripts/pipeline.py next ~/文章库 --type article    # 或 --type summary

# 看现在收了多少
python scripts/pipeline.py status ~/文章库

# 体检：图片、编号、台账、总结覆盖范围、vault 孤儿文件
python scripts/pipeline.py check ~/文章库
python scripts/pipeline.py check ~/文章库 --links          # 额外测原文链接是否还活着

# 生成可离线阅读的索引页
python scripts/render.py index ~/文章库

# 发布到 Obsidian（带冲突保护；--prune 清掉改名留下的残留）
python scripts/pipeline.py publish ~/文章库 --vault ~/Obsidian/文章库 --prune
```

脚本只负责采集、落盘、编号。正文文章和总结由模型按 `references/` 里的规范撰写——这部分是判断和写作，硬编码不出来。

## 判重按内容 ID，不按链接字符串

同一条内容有好几种写法：小红书的 `/explore/<id>` 和 `/discovery/item/<id>` 是同一篇，分享参数 `xsec_token` 每次都不同。所以重复判定用「平台 + 内容 ID」：小红书认 24 位笔记 ID，B站认 BV 号，小宇宙认 episode id，公众号认 `__biz + mid + idx`。同一条链接收两次会被识别出来并指向原编号，不会重复下载图片。

## 改名为什么要专门一条命令

`collect` 时只能用**原文标题**给素材目录命名，但文章标题是模型读完正文之后才定的——「期末考高速通过 纯用ai搞定复习大纲＋刷题」会变成「用 NotebookLM + Gemini 两天搞定微观经济学期末复习」。两边必然分叉。

`retitle` 一条命令做完全部联动：素材目录改名、文章文件改名、frontmatter 的 `标题` 与 `素材目录`、正文里全部 `![[素材/…]]` 图片嵌入、台账记录。返回的 `broken_images` 必须为空。

## 三条硬规则

1. **命名必须「编号 + 空格 + 标题」**。`1.md` 在编辑器标签里只显示「1」，十几篇之后谁也认不出。同号的文章文件与素材目录必须用同一个标题片段，否则引用会断。
2. **图片只用 wiki 嵌入** `![[素材/1 标题/media/01.jpg]]`，不用 `![](../素材/...)`。Obsidian 解析不了 `../` 相对路径，会裂图。
3. **先审后发布**。收录和总结都先落库，用户确认后才 `publish`。`publish` 也绝不覆盖 Obsidian 里比库更新的文件，冲突会列出来交给用户判断。

## 图片是内容主体时

教程长图、步骤截图这类笔记，正文的干货全在图里。技能规范要求逐张识读图片文字写进正文，不能只写「详见图片」；同时文末保留「原图」附录，方便回看。

视频笔记只有文案，不做语音转写。

## 已知边界

- 只处理手动提供的链接，不做批量爬取、不遍历话题页或用户主页
- 小红书链接必须带 `xsec_token`（App 里「分享 → 复制链接」），纯 ID 链接大概率抓空
- **评论区抓不到**。评论走独立接口且需要 `x-s` / `x-s-common` 签名，SSR 里是空数组，纯请求链路无解，需要真实浏览器控制通道
- 公众号没有专用提取器，是 best-effort 解析，失败会明确报错而不是生成空文章
- 内容版权归原作者，仅作个人归档，不用于二次发布
- **全量总结有上下文上限**。当前规则是每次总结都读全部文章，2~10 篇没问题，30 篇左右模型上下文会装不下。这一步还没解决，届时要么改成「每 10 篇出一份底稿再汇总」，要么按主题分库

## License

MIT

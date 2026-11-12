---
name: link-digest
description: 把小红书、B站、小宇宙、公众号链接收录成编号文章，并在需要时把全部文章按主题重组、重新总结成新的编号文档，同时生成可离线打开的索引网页。当用户发来内容链接并要求「收录/整理成文章/存下来」，或说「总结一下」「总结成文档」「再加一篇」「现在收了几篇」时使用。产出为 文章/1.md、2.md… 与 总结/1.md、2.md… 两套独立编号的 Markdown 文档，外加 index.html。
agent_created: true
---

# link-digest · 链接收录与累积总结

## 用途

维护一个持续生长的内容库：把用户发来的链接变成可读文章，再按需把全部文章按主题重新组织成一份总结。文章与总结各自独立编号，可建多个库分开存。

## 库的组织

一个库就是一个目录，内部结构固定：

```
<库目录>/
├── 文章/        1 标题.md, 2 标题.md…        模型重写后的可读文章
├── 总结/        1 标题.md, 2 标题.md…        全量总结，每次一份新文档
├── 素材/        1 标题/, 2 标题/             原文留底 + 图片（与文章同号）
│   └── <n> 标题/   原文.md  meta.md  media/  _source/
├── index.html                                文章与总结的可翻阅网页
└── .ledger.json                              收录台账
```

**命名规则（必须遵守，用户在 Obsidian 里靠文件名辨认内容）**：所有文件和目录都是「编号 + 空格 + 标题」，不只写数字——`文章/1 用 NotebookLM 搞定期末复习.md`、`总结/1 NotebookLM 怎么用才有效.md`、`素材/1 用 NotebookLM 搞定期末复习/`。标题取自文章 frontmatter 的 `标题`，脚本会清理 Windows 禁用字符并截到 40 字（`pipeline.py safe_title`）。纯数字文件名（`1.md`）是旧格式，遇到就顺手重命名并同步引用。同号的文章、素材目录必须**用同一个标题片段**，否则引用会断。

**多库**：不同主题各建一个库，例如 `文章库/AI工具`、`文章库/考研`。编号在各自库内独立从 1 开始，互不影响。建库时目录名直接用主题名。

用户没指定往哪个库存时：只有 1 个库就直接存；有多个库且不明显匹配，问一句「放哪个库」。

## 默认工作流：先审后入库

用户当前配置：**库在工作区里生成，确认满意后才发布到 Obsidian**。每次照这个顺序走，不要跳步。

1. 收录链接 → `文章/<n>.md`
2. 总结 → `总结/<m>.md`，跑索引，`present_files` 把这份总结交出去
3. **停下来等用户表态**。可以说「不合适我改」「要不要调整某节」，但不能自己接着发布
4. 用户说 OK（“可以”“就这样”“搬吧”“存进 obsidian”）→ 执行一次 `publish`，回一句「已同步到 Obsidian，D:\Obsidian\文章库」

用户明确说了「不用审，直接入库」时，才允许收录完直接发布。

## 五个动作

### 一、收录链接

```bash
python ~/.workbuddy/skills/link-digest/scripts/pipeline.py collect "<库目录>" "<链接>"
```

脚本返回单行 JSON，含 `article_idx` / `title` / `author` / `publish_time` / `raw_content` / `media_dir` / `media_files` / `article_target`。同一链接重复收录会返回 `duplicate: true` 并指向原编号，不重复下载。

拿到结果后：

1. 读 `raw_content`（`素材/<n> <标题>/原文.md`）
2. `media_files` 非空且图片承载正文（教程长图、步骤截图）时，逐张识读图片文字
3. 按 `references/article-spec.md` 撰写文章，写到 `article_target`（`文章/<n> <短标题>.md`）
4. 失败（`ok: false`）如实报告并跳过这一条，不编造文章，不中断同批其他链接

多条链接逐条串行处理，最后汇总「已收录 X 篇，编号 a~b」，失败的单独列出。

### 二、总结

1. 读 `文章/` 下全部 Markdown（**必须全量**，不是只看新增）
2. 取号：`python ~/.workbuddy/skills/link-digest/scripts/pipeline.py next "<库目录>" --type summary`
3. 按 `references/summary-spec.md` 写：按主题重组、不设字数上限但不啰嗦、偏正式的口语、保留「我的看法」和「一个没人讲清的坑」两段收尾
4. 写入 `总结/<m> <短标题>.md`（短标题概括这期总结的主题，如「NotebookLM 怎么用才有效」）

`文章/` 为空时直接回「还没有收录任何文章」，不生成空总结。

### 三、重建索引

```bash
python ~/.workbuddy/skills/link-digest/scripts/render.py index "<库目录>"
```

生成 `index.html`：单文件、离线可看，点卡片展开全文。**每次写完文章或总结都跑一次**，保持索引是最新的。

### 四、查看状态

```bash
python ~/.workbuddy/skills/link-digest/scripts/pipeline.py status "<库目录>"
python ~/.workbuddy/skills/link-digest/scripts/pipeline.py libraries "<父目录>"
```

用表格回：编号 / 标题 / 平台 / 作者 / 收录时间，并说明已有几份总结、各自覆盖了哪些文章。

### 五、发布到 Obsidian

**只在用户确认之后执行。**

```bash
python ~/.workbuddy/skills/link-digest/scripts/pipeline.py publish "<库目录>" --vault "D:/Obsidian/文章库"
```

- 复制 `文章/`、`总结/`、`素材/`（含图片）到 vault，跳过内部文件（`.ledger.json`、`meta.md`、`_source/`），并重建 vault 里的 `index.html`
- `--vault` 只需第一次带，之后记在库的 `.config.json` 里；目标目录变了才需要重新指定
- **publish 只增改不删除**：库里重命名或删除文件后，vault 里会留下旧名字的文件，发布前要手动把 vault 里的旧文件清掉，避免 Obsidian 里出现两份
- **冲突保护**：vault 里的同名文件若比库里新（用户在 Obsidian 里改过），跳过并在 `conflicts` 里列出。这种情况**必须口头告诉用户哪几个文件没覆盖**，并问是保留 Obsidian 版本还是以库为准
- 想先看会改动什么，加 `--dry-run`
- 发布后用一句话回报：复制了几个文件、有没有冲突。不复述全部路径

库仍是唯一事实来源，vault 是阅读端。用户如果在 Obsidian 里改错了，从库里重新 copy 一份覆盖即可。

## 初始化

```bash
python ~/.workbuddy/skills/link-digest/scripts/pipeline.py init "<库目录>"
```

默认库路径 `<工作区>/文章库`，用户指定过就沿用。Obsidian 目标目录默认 `D:/Obsidian/文章库`。

## 交付

- 每完成一次收录或总结，跑一次索引重建，然后用 `present_files` 展示**刚生成的那份文档**；新建库时额外把 `index.html` 一起展示
- 回复里给：编号 + 标题 + 一句话说明，以及下一步能做什么（「再说一次总结就出 2 号」）
- 总结交付时把「等你确认后我入库」说出口，让用户知道现在还差一步
- 路径用绝对路径

## 边界

- 只处理用户手动提供的链接，不做批量爬取、不遍历话题页或用户主页
- 小红书链接必须带 `xsec_token`（App「分享→复制链接」）；纯 ID 链接大概率抓空，遇到就请用户重新复制
- 小红书视频笔记只有文案，不做语音转写
- 评论抓取不在本技能范围；需要评论区内容须另开浏览器控制通道
- 版权归原作者，原创内容仅作个人归档，不用于二次发布
- 不覆盖已有文件：目标编号已存在时重新取号，绝不覆盖
- 未经用户确认不执行 `publish`；`publish` 也绝不覆盖 Obsidian 里比库更新的文件

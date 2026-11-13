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
├── 00-目录.md                                Obsidian 入口：双链 + 一句话 + 待总结提示（自动生成）
├── index.html                                文章与总结的可翻阅网页（自动生成）
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

## 八个动作

### 一、收录链接

```bash
python ~/.workbuddy/skills/link-digest/scripts/pipeline.py collect "<库目录>" "<链接>"
```

脚本返回单行 JSON，含 `article_idx` / `title` / `author` / `publish_time` / `raw_content` / `media_dir` / `media_files` / `article_target`。同一链接重复收录会返回 `duplicate: true` 并指向原编号，不重复下载。

判重**按内容 ID**，不按 URL 字符串：小红书的 `/explore/<id>` 与 `/discovery/item/<id>` 视为同一篇，`xsec_token` 不同也算重复；B站认 BV 号、小宇宙认 episode id、公众号认 `__biz+mid+idx`。

拿到结果后：

1. 读 `raw_content`（`素材/<n> <标题>/原文.md`）
2. `media_files` 非空且图片承载正文（教程长图、步骤截图）时，逐张识读图片文字
3. 按 `references/article-spec.md` 撰写文章，写到 `article_target`（`文章/<n> <短标题>.md`）
4. **文章标题定了之后跑一次 `retitle`**（见「三、改名」），把素材目录、文件名、引用、台账全部对齐。`collect` 时只能用**原文标题**给素材目录命名，而文章标题是你读完正文后才定的，两边必然分叉——不 retitle 就会留下「文章叫 A、目录叫 B」的脏数据
5. 失败（`ok: false`）如实报告并跳过这一条，不编造文章，不中断同批其他链接

多条链接逐条串行处理，最后汇总「已收录 X 篇，编号 a~b」，失败的单独列出。

### 二、总结

1. 读 `文章/` 下全部 Markdown（**必须全量**，不是只看新增）
2. 取号：`python ~/.workbuddy/skills/link-digest/scripts/pipeline.py next "<库目录>" --type summary`
3. 按 `references/summary-spec.md` 写：按主题重组、不设字数上限但不啰嗦、偏正式的口语、保留「我的看法」和「一个没人讲清的坑」两段收尾
4. 写入 `总结/<m> <短标题>.md`（短标题概括这期总结的主题，如「NotebookLM 怎么用才有效」）

`文章/` 为空时直接回「还没有收录任何文章」，不生成空总结。

### 三、改名（retitle）

```bash
python ~/.workbuddy/skills/link-digest/scripts/pipeline.py retitle "<库目录>" <编号> "<新标题>"
```

一条命令做完五件事：改素材目录名、改文章文件名、改 frontmatter 的 `标题` 和 `素材目录`、重写正文里全部 `![[素材/…]]` 图片嵌入、同步台账的 `article_title` 与原文路径。

**用在什么时候**：文章标题与原文标题不一致时（几乎每次都是）。返回的 `broken_images` 必须为空，非空说明有引用没跟上，要查。

不要手写脚本改文件名——以前这么干过两次，每次都要手动盯 17 张图的路径。

### 四、体检（check）

```bash
python ~/.workbuddy/skills/link-digest/scripts/pipeline.py check "<库目录>"
python ~/.workbuddy/skills/link-digest/scripts/pipeline.py check "<库目录>" --links   # 额外测原文链接是否还活着（慢，联网）
```

输出 `passed` / `errors` / `warnings` / `vault_orphans` / `dead_links` / `stats.uncovered_articles`。检查项：

- 编号是否跳号
- frontmatter 必填字段、正文首行 H1
- 图片引用是否都指向真实文件
- **media/ 下有没有 0 字节的坏图**（下载失败留下的空壳，Obsidian 里显示裂图）
- 文章文件名与素材目录名的标题片段是否一致
- 台账条目与磁盘是否对得上
- 总结声称覆盖的文章是否存在、哪些文章还没进任何总结
- vault 里有没有库里已不存在的孤儿文件

**每次收录完、每次发布前跑一次。** `errors` 非空就不要交付。`uncovered_articles` 非空时顺口告诉用户「3、4 号还没进总结」。

check 报「0 字节空壳」时用这条修（重开笔记页拿新鲜签名直链重下，只覆盖坏文件，好的不动）：

```bash
python ~/.workbuddy/skills/link-digest/scripts/pipeline.py repair-media "<库目录>" <编号>
```

背景：小红书图片 CDN 的直链带时间戳签名，会偶发整批 403，且过期后无法原地重下。`repair-media` 就是「重新打开页面 → 重新下载」的固化，修完自动同步 `meta.md` 的图片计数。0 字节文件必须当 errors 处理，不能只给 warning——用户在 Obsidian 里看到的是裂图。

### 五、生成索引与目录

```bash
python ~/.workbuddy/skills/link-digest/scripts/render.py all "<库目录>"    # 两个都生成
python ~/.workbuddy/skills/link-digest/scripts/render.py index "<库目录>"  # 只要网页
python ~/.workbuddy/skills/link-digest/scripts/render.py moc "<库目录>"    # 只要 00-目录.md
```

两个产物，用途不同：

- **`index.html`** —— 单文件网页，点卡片展开全文。发链接给别人、或手机上打开用。**在 Obsidian 里没用**（Obsidian 不渲染 html）。
- **`00-目录.md`** —— Obsidian 里的入口。`[[文章/…]]`、`[[总结/…]]` 双链，每篇带平台/作者/时间 + 一句话摘要，末尾有 `## 待总结` 列出还没进任何总结的文章。**每次写完文章或总结都要重跑**，否则目录会旧。

`00-目录.md` 只写日期不写时刻，同一天重复生成结果完全一致——这样 publish 不会因为时间戳变化产生假改动。

### 六、查看状态

```bash
python ~/.workbuddy/skills/link-digest/scripts/pipeline.py status "<库目录>"
python ~/.workbuddy/skills/link-digest/scripts/pipeline.py libraries "<父目录>"
```

用表格回：编号 / 标题 / 平台 / 作者 / 收录时间，并说明已有几份总结、各自覆盖了哪些文章。

### 七、抓评论区（可选，仅小红书）

用户说「带评论」「抓一下评论」「评论区也看看」时才走这一步。不说就不抓，正文链路保持不变。

```bash
python ~/.workbuddy/skills/link-digest/scripts/pipeline.py comments "<库目录>" <编号>
python ~/.workbuddy/skills/link-digest/scripts/pipeline.py comments "<库目录>" <编号> --force   # 重抓
```

产物两份，都在 `素材/<n 标题>/` 下：

- `comments.json` —— 原始结构化数据（作者、正文、日期、地点、点赞数、是否回复、父级序号）
- `评论.md` —— 按点赞降序、回复挂在主评论下的可读版，Obsidian 里可直接看

返回字段里重点看两个：`partial` 和 `login_wall`。`partial: true` 表示抓到的条数少于页面标注总数，**必须把两个数字都告诉用户**（「页面写 212 条，抓到 180 条」），不许说成抓全了。

**前置条件**：需要 Node.js 22+ 和一个已登录的小红书登录态。没登录或登录过期时脚本会返回 `login_wall`，这时让用户跑一次：

```bash
python ~/.workbuddy/skills/link-digest/scripts/pipeline.py login
```

会弹出一个 Chrome 窗口（独立配置目录 `~/.workbuddy/browser-profile/xhs`，不动用户日常浏览器），用户扫码后自动关闭并保存登录态，有效期通常几周。

**实现要点（改脚本前先看）**：不用签名接口、不用第三方库，走 Chrome DevTools Protocol 在真实页面里读 DOM。两个坑都必须避：

1. **只滚内层容器不够**，`.note-scroller` 和 `window` 都要滚，否则主评论永远停在前 10 条
2. **每条「展开 N 条回复」都要点**，否则回复拿不到

不要改成「直接调 `/api/sns/web/v2/comment/page`」——那个接口要 `x-s` 签名，硬算等于和平台对抗，页面自己会签，借它的手最稳。

### 八、发布到 Obsidian

**只在用户确认之后执行。**

```bash
python ~/.workbuddy/skills/link-digest/scripts/pipeline.py publish "<库目录>" --vault "D:/Obsidian/文章库"
```

- 复制 `文章/`、`总结/`、`素材/`（含图片）和库根目录的 `.md`（含 `00-目录.md`）到 vault，跳过内部文件（`.ledger.json`、`meta.md`、`_source/`），并重建 vault 里的 `index.html`
- 发布前会先在**库里**重新生成 `00-目录.md` 再镜像过去。不要在 vault 里生成：两边时间戳会不同，下一轮就变成假冲突
- `--vault` 只需第一次带，之后记在库的 `.config.json` 里；目标目录变了才需要重新指定
- **孤儿文件**：库里改名或删掉的文件，vault 里不会自动消失，会在返回的 `orphans` 里列出。加 `--prune` 才会删除（删集合永远需要显式开关）。改名之后发布，务必带 `--prune`，否则 Obsidian 里会同时出现新旧两份
- **冲突保护**：vault 里的同名文件若比库里新（用户在 Obsidian 里改过），跳过并在 `conflicts` 里列出。这种情况**必须口头告诉用户哪几个文件没覆盖**，并问是保留 Obsidian 版本还是以库为准
- 想先看会改动什么，加 `--dry-run`
- 发布后用一句话回报：复制了几个文件、有没有冲突、清掉几个残留。不复述全部路径

库仍是唯一事实来源，vault 是阅读端。用户如果在 Obsidian 里改错了，从库里重新 copy 一份覆盖即可。

## 初始化

```bash
python ~/.workbuddy/skills/link-digest/scripts/pipeline.py init "<库目录>"
```

默认库路径 `<工作区>/文章库`，用户指定过就沿用。Obsidian 目标目录默认 `D:/Obsidian/文章库`。

## 交付

- 每完成一次收录或总结，先跑 `check`，再跑 `render.py all`（网页 + 目录），然后用 `present_files` 展示**刚生成的那份文档**；新建库时额外把 `index.html` 一起展示
- 和用户交代入口：Obsidian 里看 `00-目录.md`，手机上或发给别人看 `index.html`
- `check` 有 `errors` 就不要交付，先修
- 回复里给：编号 + 标题 + 一句话说明，以及下一步能做什么（「再说一次总结就出 2 号」）
- 总结交付时把「等你确认后我入库」说出口，让用户知道现在还差一步
- 路径用绝对路径

## 边界

- 只处理用户手动提供的链接，不做批量爬取、不遍历话题页或用户主页
- 小红书链接必须带 `xsec_token`（App「分享→复制链接」）；纯 ID 链接大概率抓空，遇到就请用户重新复制
- 小红书视频笔记只有文案，不做语音转写
- 评论抓取**仅小红书笔记**，且需要用户自己的登录态；其他平台暂不支持
- 评论区只读：不点赞、不回复、不关注、不转发
- 评论只抓页面上公开可见的内容，抓不全时如实报 `partial`，不补、不猜
- 版权归原作者，原创内容仅作个人归档，不用于二次发布
- 不覆盖已有文件：目标编号已存在时重新取号，绝不覆盖
- 未经用户确认不执行 `publish`；`publish` 也绝不覆盖 Obsidian 里比库更新的文件
- 只有带 `--prune` 才会删除 vault 里的文件，默认只报告

## 已知待定

**全量总结的上下文上限**。当前规则是「每次总结读全部文章」，2~10 篇没问题，到 30 篇左右模型上下文会装不下，那时要么报错、要么悄悄只读前几篇（后者更糟）。三个候选方案：分层出底稿、文章生成时写 150 字摘要供总结只读摘要、库超过 20 篇就建议分库。**等库真正到 20 篇再定**，现在改等于猜。

# Skill: generate_skill_from_url

## Name
generate_skill_from_url

## Description
Generates a multimodal Skill markdown file from a source URL. Use this Skill when a user wants to turn a web tutorial, product support article, or software guide into a reusable agent Skill with concise steps and embedded screenshots.

---

## 这个 Skill 做什么

给定一个网页 URL，读取页面中的任务说明、操作步骤、图片和视频内容，整理成一个标准 Skill `.md` 文件。生成结果应包含 `# Skill`、`## Name`、`## Description`、`## Steps`，并把相关截图放在它所说明的步骤后面。

适用请求包括：

- “从这个链接生成一个 Skill”
- “把这个页面做成 Skill 文件”
- “把这篇教程整理成可复用的 agent skill”
- “帮我从这个网址提取操作步骤和截图”

---

## 输入

- 一个可访问的网页 URL
- 可选：用户给出的目标标题、ID、保存目录或是否覆盖旧文件

---

## 输出

将生成的 Skill 保存到：

```text
skills/<slug>/SKILL.md
```

相关图片保存到：

```text
skills/<slug>/references/img_NN.ext
```

最终 Skill markdown 必须符合：

```markdown
# Skill: <Skill Name>

## Name
<snake_case_skill_name>

## Description
<1-3 English sentences describing what this Skill does and when to use it>

## Steps
1. <action-oriented instruction>

![alt text](references/img_NN.ext)
2. <next instruction>
```

---

## Steps

1. 接收用户提供的 URL，先确定页面要表达的主要任务；如果用户没有提供标题，就从页面标题或文章主标题推断一个简短任务名。

2. 用 Playwright 打开并完整渲染该 URL，等待动态内容加载完成，必要时关闭 cookie banner 或同意弹窗。读取页面正文、标题层级、列表项、图片、iframe 和嵌入视频链接。

3. 从渲染后的页面中整理 `section_tree`：保留主内容区的标题、段落和列表项，去掉导航、页脚、广告、登录入口、隐私条款、站点菜单等与任务无关的文本。

4. 为页面中的每张内容图片建立记录。记录图片 URL、alt 文本、上方最近标题、图片前后的正文片段，以及 figcaption、aria-describedby、表格同行文字或“见图 N”这类远程引用。

5. 跳过明显不是内容图片的资源，例如 SVG 图标、data URI、小 logo、广告像素、追踪图片和站点装饰图。不要在这一步因为截图看起来“不够重要”就丢弃它；内容相关性留到视觉过滤时判断。

6. 并发下载候选图片。只保留可读取的 `jpeg`、`png`、`gif`、`webp` 图片；丢弃尺寸过小、超过大小限制、无法打开或下载失败的图片；用图片原始字节的 SHA-256 哈希去重。

7. 如果页面包含 YouTube、Bilibili、Vimeo 或其他可下载的嵌入视频，尝试用 `yt-dlp` 下载视频，再用 `ffmpeg` 从视频中提取代表性帧。视频失败时跳过该视频并继续处理页面图片。

8. 将下载成功的 DOM 图片和视频帧合并成同一组候选视觉材料。每个候选项都必须带上可用的文本上下文；视频帧至少带上视频标题或描述。

9. 调用支持视觉输入的模型过滤候选图片。给模型同时提供图片和图片附近的文字，让它保留软件界面、菜单、设置页、按钮、对话框、操作结果截图或能直接说明任务的画面；跳过广告、纯装饰图、独立 logo 和无关宣传图。模型调用失败时，保守地保留该批图片。

10. 把保留下来的图片保存到 `skills/<slug>/references/`，按出现顺序命名为 `img_00.ext`、`img_01.ext`、`img_02.ext`。保存后记录每张图片在最终 markdown 中可使用的相对路径。

11. 判断每张保存后的图片应该说明哪段文字。优先使用 figcaption、aria-describedby、表格同行文字或正文反向引用；如果没有明确引用，再结合图片内容和 `text_before` / `text_after` 判断它属于前文、后文，还是独立说明。

12. 根据保存路径和对齐结果构建 image manifest。manifest 中必须包含图片相对路径、alt 文本、所在标题和它对应的文本上下文，供最终生成 Skill 时逐字引用。

13. 调用文本模型生成最终 Skill markdown。输入应包含来源 URL、`section_tree` 和 image manifest；只使用已提供的内容，不得自行补充未出现在输入中的步骤或 UI 元素。生成内容只保留直接完成任务所需的正向步骤。

14. 写 `## Name` 时使用简短准确的 snake_case；写 `## Description` 时用英文 1-3 句说明这个 Skill 的用途和触发场景。

15. 写 `## Steps` 时使用编号步骤，每一步都应该是可执行的动作。把图片紧贴在它说明的步骤后方，不要让图片集中堆在文末，也不要引用 manifest 之外的路径。

16. 删除或避免加入 FAQ、故障排查、营销介绍、背景知识、系统要求长表格、替代方法、撤销步骤和边缘说明，除非它们是完成主任务的必要动作。

17. 生成后检查 markdown 图片语法。若出现 `![references/img_00.png]` 这种把路径写进 alt 文本里的形式，将它修复成 `![img_00](references/img_00.png)`。

18. 最后确认输出文件存在，并检查 markdown 中的每个图片引用都能在 `skills/<slug>/` 下找到对应文件。若有断链，修正路径后再结束。

---

## 质量要求

- 生成的 Skill 应该短而可执行，优先保留主任务路径。
- 图片必须帮助理解具体操作，不能只是装饰。
- 图片路径只能来自保存后的真实文件，不能编造。
- 即使页面有很多内容，也要把最终 Skill 压缩成 agent 能快速执行的步骤。
- 如果页面完全无法读取，也没有可用的 web fallback 内容，不要生成空 Skill；应说明失败原因。

---

## 可用实现

**执行本 Skill 时，优先使用下方脚本，`## Steps` 描述的是背后的逻辑，用于理解或自定义实现。**

所有命令均在项目根目录（`generate_skills/`）下运行。每个脚本各负责一个阶段，通过 JSON 文件传递中间结果；工作目录自动隔离为 `work/<slug>/`，多个 URL 顺序或并行跑互不干扰。

执行步骤（在 `scripts/` 目录作为 cwd 执行，`<skills_dir>` 为 skill 输出根目录的绝对路径）：

```bash
# 第 1 步：爬取页面，--slug 指定 work 子目录名和最终输出目录名
# 运行后输出：[stage 1] wrote work/<slug>/stage_01_scrape.json: N image(s) ...
python stage_01_scrape.py "<URL>" --slug <slug>

# 第 2 步：下载并去重图片
python stage_02_download.py work/<slug>/stage_01_scrape.json

# 第 3 步：提取视频帧（无视频时快速跳过）
python stage_02b_video.py work/<slug>/stage_02_download.json

# 第 4 步：VLM 过滤
python stage_03_filter.py work/<slug>/stage_02b_video.json

# 第 5 步：保存图片并对齐文本上下文，--skills-dir 必须传绝对路径
python stage_04_save_align.py work/<slug>/stage_03_filter.json --skills-dir <skills_dir>

# 第 6 步：将对齐后的 records 聚类成 feature groups（单一流程页面会自动跳过分组）
python stage_04b_group_features.py work/<slug>/stage_04_save_align.json

# 第 7 步：生成 Skill markdown，--clean 表示成功后自动删除 work/<slug>/
python stage_05_generate.py work/<slug>/stage_04b_group_features.json --clean
```

示例（URL 为 Figma 文章，slug 为 `067_advanced_prototyping_examples`，skills_dir 为 `/data/generated_skills`）：

```bash
python stage_01_scrape.py "https://help.figma.com/hc/en-us/articles/17146044893591" --slug 067_advanced_prototyping_examples
python stage_02_download.py      work/067_advanced_prototyping_examples/stage_01_scrape.json
python stage_02b_video.py        work/067_advanced_prototyping_examples/stage_02_download.json
python stage_03_filter.py        work/067_advanced_prototyping_examples/stage_02b_video.json
python stage_04_save_align.py    work/067_advanced_prototyping_examples/stage_03_filter.json --skills-dir /data/generated_skills
python stage_04b_group_features.py work/067_advanced_prototyping_examples/stage_04_save_align.json
python stage_05_generate.py      work/067_advanced_prototyping_examples/stage_04b_group_features.json --clean
```

---

## 运行环境

- Python 3.11+
- 依赖包：`playwright`、`beautifulsoup4`、`Pillow`、`requests`、`openai`、`python-dotenv`
- 外部工具：`ffmpeg`、`ffprobe`、`yt-dlp`
- 环境变量：`API_BASE`、`API_KEY`、`MODEL_NAME`
- Playwright 需要安装 Chromium：`playwright install chromium`

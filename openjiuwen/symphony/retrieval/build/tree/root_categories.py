from __future__ import annotations

from pathlib import Path
from typing import Sequence

from openjiuwen.symphony.retrieval.build.io.config_loader import parse_json_or_yaml, read_config_text

RootCategory = str | dict[str, object]
RootCategoryInput = Sequence[RootCategory] | str | Path | None

FIXED_ROOT_CATEGORIES = {
    "office-docs": {
        "name": "办公文档",
        "description": "办公文件的生成、编辑、转换、提取、排版和结构化处理。",
        "select_when": "用户要处理 Word、PDF、PPT、Excel、Markdown、邮件、会议纪要或办公流程产物。",
        "dont_select_when": "用户只是写普通文章/营销文案、管理个人知识库/笔记/任务、联网查资料、开发网页、生成图片视频或查询金融行情。",
        "children": {
            "documents": {
                "name": "文档处理",
                "description": "Word、PDF、TXT、Markdown、格式转换、OCR 后文档整理、文档提取和文档清洗。",
                "select_when": "请求涉及具体文件的 Word、PDF、TXT、Markdown、格式转换、文档抽取、阅读、合并、拆分或排版。",
                "dont_select_when": "请求主要是个人知识库、笔记、备忘、收藏、知识图谱、表格计算、PPT 生成、写原创文章或联网搜索。",
            },
            "spreadsheets": {
                "name": "表格数据",
                "description": "Excel、XLSX、CSV、数据分析、图表、公式、透视表和表格生成。",
                "select_when": "请求涉及表格、数据清洗、统计分析、图表、公式、仪表盘、预算或 Excel 输出。",
                "dont_select_when": "请求只是写报告正文、做 PPT、查网页资料或开发数据库。",
            },
            "slides": {
                "name": "PPT演示",
                "description": "PPT、幻灯片、演示文稿、学术海报、演讲材料和课件生成美化。",
                "select_when": "用户明确要 PPT、slides、演示文稿、路演材料、答辩材料、课程幻灯片或学术海报。",
                "dont_select_when": "用户只是写文章、生成图片、处理 PDF 或开发网页。",
            },
            "mail-meeting": {
                "name": "邮件会议",
                "description": "邮件收发、会议管理、会议纪要、日程协同和办公自动化流程。",
                "select_when": "请求涉及真实邮件收发、会议工具、会议纪要、日程协同、办公事务流转或办公自动化。",
                "dont_select_when": "请求只是管理待办/项目/主动跟进/智能体任务、创建 skill、处理单个文档或网页搜索。",
            },
        },
    },
    "writing-content": {
        "name": "写作内容",
        "description": "文章、故事、新闻、营销文案、内容策划、文本润色和写作风格处理。",
        "select_when": "用户要写、改写、润色、策划或生成面向读者的文字内容，而不是操作具体办公文件。",
        "dont_select_when": "用户明确要输出 Word/PDF/PPT/Excel 文件、生成图片视频、联网调研、管理知识库或构建智能体/skill。",
        "children": {
            "general-writing": {
                "name": "通用写作",
                "description": "文章、博客、新闻稿、报告正文、长文、口播稿和普通内容撰写。",
                "select_when": "请求写文章、写新闻、写报告正文、写视频脚本、整理成可读内容或生成长篇文字。",
                "dont_select_when": "请求重点是营销转化、文学故事、AI 痕迹消除、知识库管理、智能体人格/skill 构建或输出办公文件。",
            },
            "marketing-copy": {
                "name": "营销文案",
                "description": "产品文案、广告文案、落地页 copy、增长创意、CTA、社媒传播和品牌表达。",
                "select_when": "请求涉及营销、增长、转化、产品介绍、广告、品牌、官网文案或社交媒体传播。",
                "dont_select_when": "请求只是文学创作、新闻写作、普通润色或产品 PRD。",
            },
            "story-creative": {
                "name": "故事创作",
                "description": "儿童故事、寓言、小说、脑洞、人物故事、绘本故事和文学风格创作。",
                "select_when": "请求创作故事、小说、儿童读物、儿童书、寓言、脑洞设定、人物叙事或指定文学风格文本。",
                "dont_select_when": "请求是视觉绘本图片生成、视频制作、营销文案或新闻稿。",
            },
            "text-polish": {
                "name": "文本润色",
                "description": "文本润色、改写、降 AI 味、风格迭代、语气调整和中英文表达优化。",
                "select_when": "请求润色、改写、去 AI 痕迹、人类化、调整语气、优化表达或学习用户写作风格。",
                "dont_select_when": "请求是从零写 PRD、生成 PPT、处理 PDF 或进行事实调研。",
            },
        },
    },
    "search-research": {
        "name": "搜索研究",
        "description": "网页搜索、内容抓取、新闻、论文、深度调研和知识库检索。",
        "select_when": "用户要查找已有信息、联网搜索、读取网页、研究主题、检索论文、查询新闻或使用知识库/笔记/记忆。",
        "dont_select_when": "用户已经给定资料且只要求生成具体文件、图片、视频、表格或创作文案。",
        "children": {
            "web-search": {
                "name": "网页搜索",
                "description": "联网搜索、网页读取、浏览器访问、多平台内容访问、网页抓取和网页转 Markdown。",
                "select_when": "请求查网页、找资料、查产品参数、读取链接、访问平台内容或需要浏览器/爬取能力。",
                "dont_select_when": "请求是深度报告、论文阅读、个人知识库或本地文档处理。",
            },
            "deep-research": {
                "name": "深度调研",
                "description": "多源调研、竞品分析、行业研究、技术研究、综合报告和洞察分析。",
                "select_when": "用户要系统调研、深度分析、竞品/行业/技术研究、多源验证或带引用报告。",
                "dont_select_when": "用户只要简单搜索结果、新闻列表、论文检索、课程/课堂/研学方案或纯文字创作。",
            },
            "news-papers": {
                "name": "新闻论文",
                "description": "新闻热点、新闻抽取、科技日报、arXiv、论文搜索、论文阅读和学术速递。",
                "select_when": "请求涉及新闻、热点、公众号/新闻提取、论文检索、论文阅读、学术论文或每日资讯。",
                "dont_select_when": "请求是通用网页搜索、深度行业报告、办公文件生成或文学写作。",
            },
            "knowledge-notes": {
                "name": "知识笔记",
                "description": "个人知识库、笔记、收藏、记忆、备忘录、IMA、second brain、ontology 和结构化知识管理。",
                "select_when": "用户要记录、保存、检索个人笔记/收藏/知识库/记忆/备忘录，或管理结构化知识关系。",
                "dont_select_when": "用户要搜索公开互联网、处理本地文件、生成 Word/PDF 或操作手机文件。",
            },
        },
    },
    "media-creative": {
        "name": "图片音视频",
        "description": "图片理解、图像生成、视觉设计、视频动画、语音音乐、漫画和绘本视觉产物。",
        "select_when": "用户输入或输出涉及图片、截图、照片、OCR、音频、语音、音乐、视频、动画、漫画或视觉设计。",
        "dont_select_when": "用户主要是在写文本、做办公文件、网页开发、旅行查询或金融分析。",
        "children": {
            "image-understand": {
                "name": "图片理解",
                "description": "图片识别、OCR、图片翻译、图库搜索、图片内容理解和已有图片素材查找。",
                "select_when": "用户提供或引用图片、截图、手写、图中文字/表格，或要查找已有图片素材。",
                "dont_select_when": "用户要生成、绘制、编辑、美化或设计一张新图片。",
            },
            "image-design": {
                "name": "图片设计",
                "description": "图像生成、海报、Logo、表情包、视觉设计、流程图、漫画绘本插图和设计提示词。",
                "select_when": "用户要画图、生成图片、设计海报/Logo/视觉素材、制作表情包/漫画/绘本插图或图像编辑。",
                "dont_select_when": "用户只是识别、翻译、OCR、搜索已有图片或写纯文本故事。",
            },
            "video-animation": {
                "name": "视频动画",
                "description": "视频生成、动画、Remotion、短片、Vlog、科普视频、电影场景和短剧制作。",
                "select_when": "请求生成、剪辑、策划或制作视频、动画、短剧、Vlog、科普片或电影场景。",
                "dont_select_when": "请求只是写短视频脚本、生成静态图片、转写音频或做 PPT。",
            },
            "audio-music": {
                "name": "语音音乐",
                "description": "语音转写、文字转语音、声音合成、配音、播客和音乐生成。",
                "select_when": "请求涉及语音转文字、配音、TTS、声音克隆、播客、有声书或音乐创作。",
                "dont_select_when": "请求是视频画面生成、文本润色、会议管理本身或普通写作。",
            },
        },
    },
    "life-services": {
        "name": "生活服务",
        "description": "出行旅行、地图天气、健康教育、本地优惠、餐饮和日常生活服务。",
        "select_when": "用户请求与真实生活行动、位置、旅行、天气、健康、学习、优惠券、餐饮或本地服务有关。",
        "dont_select_when": "请求主要是内容创作、办公文件、网页开发、系统配置或金融投资。",
        "children": {
            "travel": {
                "name": "出行旅行",
                "description": "机票、火车票、酒店、景点、旅游规划、航空信息和旅行产品查询预订。",
                "select_when": "请求订票、查航班/火车/酒店、规划旅行、查景点、做旅游攻略或查询旅行产品。",
                "dont_select_when": "请求只是市内路线、附近地点、天气、打车或优惠券。",
            },
            "maps-weather": {
                "name": "地图天气",
                "description": "定位、地图、路线、打车、周边搜索、天气、交通出行和本地 POI。",
                "select_when": "请求当前位置、怎么走、打车、路线规划、附近地点、天气或通勤出发时间。",
                "dont_select_when": "请求是跨城旅行订票、酒店预订、金融行情或办公地图可视化开发。",
            },
            "health-edu": {
                "name": "健康教育",
                "description": "运动健康、饮食营养、健身、禁食、K12、公考、数学、课程和研学教育。",
                "select_when": "请求涉及健康数据、心率血压、饮食、健身、学习辅导、考试备考、课堂生成、课程设计或研学方案。",
                "dont_select_when": "请求是儿童故事/儿童书创作、旅行路线、天气查询、金融市场分析或普通内容写作。",
            },
            "local-deals": {
                "name": "本地优惠",
                "description": "美团优惠、餐饮团购、麦当劳、电影、本地生活、食谱和购物清单。",
                "select_when": "请求找餐厅、领券、外卖/团购优惠、麦当劳、电影信息、菜谱或本地消费。",
                "dont_select_when": "请求只是路线导航、旅行订票、健康营养分析或金融优惠。",
            },
        },
    },
    "product-dev": {
        "name": "开发产品",
        "description": "网页应用、产品需求、UX/UI、部署数据库、代码测试和工程质量。",
        "select_when": "用户要设计产品、写 PRD、做网页/应用、部署网站、接数据库、优化前端或测试代码。",
        "dont_select_when": "用户只是写营销文案、生成办公文件、做图片视频、查询资料或配置智能体。",
        "children": {
            "web-apps": {
                "name": "网页应用",
                "description": "HTML、前端、React、Web 应用、交互网页、地图 JSAPI 和网页端功能开发。",
                "select_when": "请求开发网站、网页应用、HTML 页面、前端交互、React/Next 优化或 Web 地图可视化。",
                "dont_select_when": "请求只是网页搜索、生成海报、写 PRD 或部署已有应用。",
            },
            "product-ux": {
                "name": "产品UX",
                "description": "PRD、产品规划、用户研究、A/B 实验、UX 设计、方案评审和需求梳理。",
                "select_when": "请求产品需求文档、用户故事、产品策略、实验设计、用户研究、UX 方案或产品评审。",
                "dont_select_when": "请求直接写代码、部署应用、写普通文章或营销文案。",
            },
            "deploy-data": {
                "name": "部署数据",
                "description": "网站部署、云数据库、云端后端、上线发布和应用数据存储。",
                "select_when": "请求部署网站、发布应用、接入云数据库、云端存储或让网页支持共享数据。",
                "dont_select_when": "请求只是做静态网页设计、前端代码优化、产品规划或本地文件上传。",
            },
            "code-test": {
                "name": "代码测试",
                "description": "代码审查、Web 自动化测试、测试用例、技能测试和工程质量验证。",
                "select_when": "请求 review 代码、测试网页、生成测试用例、验证功能、审计工程质量或测试 skill。",
                "dont_select_when": "请求是产品需求、网页视觉设计、部署上线或普通安全合规检查。",
            },
        },
    },
    "finance-business": {
        "name": "金融商业",
        "description": "股票行情、投资分析、金融新闻、量化交易、模拟交易和企业信息查询。",
        "select_when": "用户询问股票、基金、指数、黄金、行情、投资、金融新闻、企业工商或商业数据。",
        "dont_select_when": "用户只是做普通市场调研、表格分析、营销文案或产品竞品分析。",
        "children": {
            "market-data": {
                "name": "行情数据",
                "description": "股票、基金、指数、贵金属、宏观和金融实时/历史行情查询。",
                "select_when": "请求查价格、走势、K线、涨跌幅、资金流、宏观数据或金融基础数据。",
                "dont_select_when": "请求是投资建议、情绪分析、企业工商资料或模拟下单。",
            },
            "investment": {
                "name": "投资分析",
                "description": "金融新闻、情绪分析、预测、信号追踪、选股、研报和投资研判。",
                "select_when": "请求分析走势、拐点、投资价值、市场事件影响、选股、研报或金融情绪。",
                "dont_select_when": "请求只是实时行情数字、企业工商资料、普通新闻搜索或模拟交易。",
            },
            "business-data": {
                "name": "企业查询",
                "description": "企查查、企业工商、商业主体、公司风险、知识产权和企业经营信息查询。",
                "select_when": "请求查询公司、企业工商、风险、知识产权、经营信息或商业主体资料。",
                "dont_select_when": "请求是股票交易数据、投资预测、行业深度研究或营销文案。",
            },
            "trading-sim": {
                "name": "模拟交易",
                "description": "模拟炒股、投资练习、虚拟持仓、交易组合管理和交易训练。",
                "select_when": "用户明确要模拟交易、虚拟持仓、买卖练习、组合管理或炒股大师模拟。",
                "dont_select_when": "用户要真实行情、投资分析、金融新闻或企业信息查询。",
            },
        },
    },
    "system-tools": {
        "name": "系统工具",
        "description": "手机设备、文件云盘、智能体/技能管理、任务自动化、安全合规和连接配置。",
        "select_when": "用户请求操作设备系统、管理文件云盘、配置渠道、创建技能/智能体、切换人格、管理任务或做安全校验。",
        "dont_select_when": "用户主要要完成一个具体业务任务，如写文档、查资料、做图、出行或金融分析。",
        "children": {
            "device-home": {
                "name": "设备家庭",
                "description": "手机 APP 操控、联系人、电话、短信、闹钟、日历、收藏、图库和智能家居设备。",
                "select_when": "请求操作手机应用、系统功能、联系人短信电话、闹钟日历、收藏图库或智能家居。",
                "dont_select_when": "请求是云盘文件、办公文档、网页搜索或旅行地图查询。",
            },
            "files-cloud": {
                "name": "文件云盘",
                "description": "本地文件、云空间、百度网盘、文件上传、下载、分享和文件资源管理。",
                "select_when": "请求找文件、上传下载、云盘管理、网盘分享、本地文件资源读取或获取文件链接。",
                "dont_select_when": "请求是解析文件内容、生成 Word/PDF/PPT、图片理解或搜索网页。",
            },
            "agents-tasks": {
                "name": "智能体任务",
                "description": "技能创建安装查找、智能体构建、人设人格、SOUL、主动任务、待办项目、计划管理和自我进化。",
                "select_when": "请求创建/安装/查找 skill，构建 agent，切换 persona，管理待办/项目/目标，或让智能体主动执行/跟进。",
                "dont_select_when": "请求只是调用已有业务 skill 完成普通任务，或写 PRD/产品方案。",
            },
            "safety-channels": {
                "name": "安全连接",
                "description": "安全合规、隐私保护、执行校验、渠道机器人和消息平台连接配置。",
                "select_when": "请求安全扫描、合规检测、密钥保护、命令验证，或配置飞书/微信/QQ/企微/微博等通道。",
                "dont_select_when": "请求是普通邮件短信发送、办公文件处理、业务查询或手机联系人操作。",
            },
        },
    },
}


def resolve_tree_root_categories(value: RootCategoryInput) -> list[RootCategory] | None:
    if value in (None, [], ()):
        return None
    if isinstance(value, (str, Path)):
        return load_tree_root_categories(value)
    return list(value)


def load_tree_root_categories(source: str | Path) -> list[RootCategory]:
    raw_source = str(source or "").strip()
    if not raw_source:
        raise ValueError("tree_root_categories file path is empty")
    payload = parse_json_or_yaml(
        read_config_text(raw_source, description="tree_root_categories"),
        source=raw_source,
    )
    return list(_extract_categories(payload, source=raw_source))


def _extract_categories(payload: object, *, source: str) -> list[RootCategory]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("tree_root_categories", "root_categories"):
            value = payload.get(key)
            if value is None:
                continue
            if not isinstance(value, list):
                raise ValueError(f"{source}: field '{key}' must be a list")
            return value
    raise ValueError(f"{source}: expected a root category list or root category object")


__all__ = [
    "FIXED_ROOT_CATEGORIES",
    "RootCategory",
    "RootCategoryInput",
    "load_tree_root_categories",
    "resolve_tree_root_categories",
]

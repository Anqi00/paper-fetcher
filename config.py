"""
Configuration for daily paper fetcher.
Sources: arXiv  |  Semantic Scholar (venue-filtered)  |  ScienceDirect RSS
"""

import os
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE lines); real env vars take precedence."""
    if not path.exists():
        return
    for line in path.read_text("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


# cron 是非交互 shell，不会 source .env，因此在这里自动读取
_load_dotenv(Path(__file__).parent / ".env")

# ── arXiv ──────────────────────────────────────────────────────────────────
ARXIV_CATEGORIES = ["cs.CV", "cs.RO", "cs.GR", "eess.IV"]

# 研究方向画像：写进 LLM 提示词，用于判断“与我的研究有什么关系”
RESEARCH_PROFILE = (
    "苹果树冬季（休眠期）剪枝点识别：枝干分割、骨架/拓扑重建、剪枝决策；"
    "图像特征提取与匹配；2D-3D 特征匹配（图像-点云配准、视觉定位）；"
    "3D Gaussian Splatting（3DGS）；4D/动态 Gaussian Splatting（4DGS）"
)

# 每个主题：
#   arxiv_queries : arXiv 检索式（支持 abs:"短语"、OR、AND；不带语法的普通词会被 AND 连接）
#   s2_queries    : 发给 Semantic Scholar 的关键词
#   keywords                   : 本地相关性打分 + RSS 主题匹配（小写子串）
TOPICS = [
    {
        "label": "剪枝点识别/果树枝干",
        "arxiv_queries": [
            'abs:pruning AND (abs:tree OR abs:orchard OR abs:apple OR abs:vine OR abs:branch)',
            'abs:"fruit tree" OR abs:"apple tree" OR abs:"tree skeleton" '
            'OR abs:"branch segmentation" OR abs:orchard OR abs:dormant',
        ],
        "s2_queries": [
            "apple tree dormant pruning",
            "fruit tree branch segmentation",
        ],
        "keywords": [
            "tree prun", "pruning point", "branch prun", "vine prun", "dormant",
            "apple tree", "fruit tree", "tree branch",
            "branch segment", "branch detect", "trunk", "tree skeleton",
            "tree architecture", "orchard", "cutting point",
        ],
    },
    {
        "label": "特征匹配",
        "arxiv_queries": [
            'abs:"feature matching" OR abs:"image matching" OR abs:"local features" '
            'OR abs:keypoint OR abs:"dense matching"',
        ],
        "s2_queries": [
            "local feature matching deep learning",
            "keypoint detection description",
        ],
        "keywords": [
            "feature match", "local feature", "keypoint", "descriptor",
            "correspondence", "matcher", "image matching",
        ],
    },
    {
        "label": "2D-3D匹配/视觉定位",
        "arxiv_queries": [
            'abs:"2D-3D" OR abs:"image-to-point cloud" OR abs:"2D-to-3D" '
            'OR abs:"cross-modal registration"',
            'abs:"visual localization" OR abs:"camera relocalization" '
            'OR abs:"scene coordinate regression"',
        ],
        "s2_queries": [
            "2D-3D matching visual localization",
            "image to point cloud registration",
        ],
        "keywords": [
            "2d-3d", "2d–3d", "2d/3d", "image-to-point", "image-point cloud",
            "point cloud registration", "pnp", "relocali", "visual localiz",
            "scene coordinate", "camera pose",
        ],
    },
    {
        "label": "3DGS",
        "arxiv_queries": [
            'abs:"Gaussian splatting" OR abs:3DGS',
        ],
        "s2_queries": [
            "3D Gaussian splatting",
        ],
        "keywords": [
            "gaussian splat", "3dgs", "3d gaussian", "radiance field",
        ],
    },
    {
        "label": "4DGS/动态场景",
        "arxiv_queries": [
            'abs:"4D Gaussian" OR abs:"dynamic Gaussian" OR abs:4DGS '
            'OR abs:"deformable Gaussian" OR (abs:"Gaussian splatting" AND abs:dynamic)',
        ],
        "s2_queries": [
            "4D Gaussian splatting dynamic scene",
        ],
        "keywords": [
            "4d gaussian", "4dgs", "dynamic gaussian", "deformable gaussian",
            "dynamic scene", "4d reconstruction", "spatio-temporal gaussian",
        ],
    },
]

# 命中这些词的论文直接排除（小写子串匹配），按需增删
EXCLUDE_KEYWORDS = [
    "surgical", "surgery", "laparoscop", "endoscop", "medical image",
]

# ── Semantic Scholar ────────────────────────────────────────────────────────
# Papers are kept only if their venue substring-matches any entry below
S2_TARGET_VENUES = [
    # Top CV conferences
    "CVPR", "ICCV", "ECCV", "WACV", "BMVC",
    # ML conferences (relevant for methods)
    "NeurIPS", "ICLR", "ICML", "AAAI", "IJCAI",
    # Robotics
    "ICRA", "IROS",
    "Robotics and Automation Letters",     # RA-L
    "International Journal of Robotics Research",
    "Robotics: Science and Systems",
    "Journal of Field Robotics",
    # Agricultural / precision ag
    "Computers and Electronics in Agriculture",
    "Artificial Intelligence in Agriculture",
    "Biosystems Engineering",
    "Precision Agriculture",
    "Smart Agricultural Technology",
    # Vision / IP journals
    "IEEE Transactions on Pattern Analysis",  # TPAMI
    "International Journal of Computer Vision",
    "IEEE Transactions on Image Processing",
    "IEEE Transactions on Geoscience and Remote Sensing",
    "Computer Vision and Image Understanding",
]

# Papers per S2 query (max 100)
MAX_S2_RESULTS_PER_QUERY = 50

# S2 always uses a 30-day window to account for publication indexing lag
S2_LOOKBACK_DAYS = 30

# Optional: free API key at semanticscholar.org/product/api
# Never commit keys: export S2_API_KEY or put it in a local .env loader.
S2_API_KEY = os.environ.get("S2_API_KEY", "")

# ── RSS Journal Feeds ────────────────────────────────────────────────────────
# Note: ScienceDirect RSS items may carry future "issue dates"; the fetcher
# therefore accepts ALL items and uses the seen-paper cache for incrementality.
# Wiley / Springer feeds carry proper publication timestamps and are date-filtered.
RSS_JOURNALS = [
    # ── ScienceDirect (Elsevier / KeAi) ──────────────────────────────────────
    {
        "name": "Computers and Electronics in Agriculture",
        "abbr": "COMPAG",
        "url": "https://rss.sciencedirect.com/publication/science/01681699",
        "date_in_html": True,   # date is embedded in summary HTML, not in feed header
    },
    {
        "name": "Biosystems Engineering",
        "abbr": "Biosyst.Eng.",
        "url": "https://rss.sciencedirect.com/publication/science/15375110",
        "date_in_html": True,
    },
    {
        "name": "Smart Agricultural Technology",
        "abbr": "SmartAg",
        "url": "https://rss.sciencedirect.com/publication/science/27723755",
        "date_in_html": True,
    },
    # ── Springer ─────────────────────────────────────────────────────────────
    {
        "name": "Precision Agriculture",
        "abbr": "PrecAg",
        "url": "https://link.springer.com/search.rss?facet-journal-id=11119",
        "date_in_html": False,
    },
    # ── Wiley ─────────────────────────────────────────────────────────────────
    {
        "name": "Journal of Field Robotics",
        "abbr": "JFR",
        "url": "https://onlinelibrary.wiley.com/action/showFeed?jc=15564967&type=etoc&feed=rss",
        "date_in_html": False,
    },
]

# ── General ──────────────────────────────────────────────────────────────────
MAX_RESULTS_PER_QUERY = 100  # max arXiv results per sub-query (newest first)
MAX_PAPERS_TOTAL = 3        # total papers in the daily digest

# “不想看”反馈在最终排序中的降权强度。0 表示关闭反馈影响。
FEEDBACK_WEIGHT = 2.0

# 相关性打分：标题命中一个关键词 +2，摘要命中 +1，上限 RELEVANCE_CAP。
# 低于 MIN_RELEVANCE 的候选不会进入日报。
RELEVANCE_CAP = 6
MIN_RELEVANCE = 2

# 逐步扩大搜索窗口，直到凑满 MAX_PAPERS_TOTAL 篇
# 每次运行只做一次 API 抓取（取最大值），然后在内存里按窗口筛选
# 从 3 天起步：arXiv API 有约 2 天延迟，1 天窗口里几乎只剩未来日期的期刊 RSS
LOOKBACK_STEPS = [3, 7, 30]
OUTPUT_DIR = "output"

# ── 论文总结后端 ─────────────────────────────────────────────────────────────
# 选项:
#   "gemini"  — Google Gemini API（免费，需 key，aistudio.google.com）
#   "ollama"  — 本地 LLM，完全离线免费（需先安装 ollama，见下）
#   "none"    — 不生成总结，只显示原文摘要
SUMMARY_BACKEND = os.environ.get("SUMMARY_BACKEND", "none")

# Gemini key（仅 SUMMARY_BACKEND="gemini" 时需要）
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# Ollama 设置（仅 SUMMARY_BACKEND="ollama" 时需要）
# 安装: curl -fsSL https://ollama.com/install.sh | sh
# 拉模型: ollama pull qwen2.5:7b   (支持中文，约 4GB)
OLLAMA_HOST  = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
# 上下文窗口：全文模式需要较大窗口（8GB 显存跑 7B 模型用 12k 足够）
OLLAMA_NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "12288"))

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

# 总结时是否下载论文全文（arXiv HTML 优先，失败再尝试 PDF）。
# 全文能提取到实验设置和具体数字；关闭则只用摘要。
SUMMARY_USE_FULLTEXT = os.environ.get("SUMMARY_USE_FULLTEXT", "1") != "0"
# 长论文分段精读：每段字符数（约 4 字符 ≈ 1 token，需小于 OLLAMA_NUM_CTX），
# 最多读几段（参考文献已去掉，超出部分通常是附录）
FULLTEXT_CHUNK_CHARS = int(os.environ.get("FULLTEXT_CHUNK_CHARS", "20000"))
FULLTEXT_MAX_CHUNKS = int(os.environ.get("FULLTEXT_MAX_CHUNKS", "6"))
# Gemini 上下文很长，整篇一次读完
GEMINI_MAX_CHARS = int(os.environ.get("GEMINI_MAX_CHARS", "400000"))

# 可选：Elsevier API key（dev.elsevier.com 免费申请），用于 COMPAG /
# Biosystems Engineering / Smart Agricultural Technology 等 ScienceDirect 期刊：
# 开放获取论文可取全文；在订阅了该期刊的校园网内，订阅论文也能取全文。
ELSEVIER_API_KEY = os.environ.get("ELSEVIER_API_KEY", "")

# ── Email (optional) ─────────────────────────────────────────────────────────
EMAIL_TO = os.environ.get("EMAIL_TO", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))

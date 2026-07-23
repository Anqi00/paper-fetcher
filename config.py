"""
Configuration for daily paper fetcher.
Sources: arXiv  |  Semantic Scholar (venue-filtered)  |  ScienceDirect RSS
"""

import os

# ── arXiv ──────────────────────────────────────────────────────────────────
ARXIV_CATEGORIES = ["cs.CV", "cs.RO", "cs.GR", "eess.IV"]

TOPICS = [
    {
        "label": "3D重建",
        "arxiv_queries": [
            "3D reconstruction",
            "neural radiance field NeRF",
            "3D Gaussian splatting",
            "multi-view stereo reconstruction",
            "depth estimation reconstruction",
            "implicit neural representation",
        ],
        # Focused queries for Semantic Scholar (1-2 phrases work best)
        "s2_queries": [
            "3D reconstruction deep learning",
            "neural radiance field",
            "3D Gaussian splatting",
        ],
        # Keywords used to assign RSS journal papers to this topic
        "rss_keywords": [
            "3d reconstruction", "point cloud reconstruct", "depth estimation",
            "gaussian splat", "neural radiance", "multi-view stereo",
            "structure from motion", "sfm", "slam",
        ],
    },
    {
        "label": "点云分析",
        "arxiv_queries": [
            "point cloud segmentation",
            "point cloud registration",
            "point cloud completion",
            "3D point cloud deep learning",
            "LiDAR perception",
            "point cloud object detection",
        ],
        "s2_queries": [
            "point cloud deep learning",
            "LiDAR point cloud segmentation",
        ],
        "rss_keywords": [
            "point cloud", "lidar", "3d scan", "voxel", "range image",
            "velodyne", "depth sensor",
        ],
    },
    {
        "label": "自动化剪枝/果园机器人",
        "arxiv_queries": [
            "robotic pruning",
            "autonomous pruning",
            "orchard robot",
            "agricultural robot arm manipulation",
            "fruit picking robot",
            "crop harvesting robot",
            "vineyard robot",
        ],
        "s2_queries": [
            "robotic pruning autonomous",
            "orchard robot arm harvesting",
            "agricultural manipulation robot",
        ],
        "rss_keywords": [
            "prun", "orchard", "harvest", "pick", "fruit detect",
            "crop robot", "vineyard", "canopy", "branch detect",
            "agricultural robot", "agri robot",
        ],
    },
    {
        "label": "视觉定位/视觉伺服",
        "arxiv_queries": [
            "visual localization",
            "visual place recognition",
            "visual servoing",
            "image-based visual servo",
            "visual SLAM localization",
            "camera relocalization",
            "scene coordinate regression",
        ],
        "s2_queries": [
            "visual localization place recognition",
            "visual servoing robot control",
        ],
        "rss_keywords": [
            "visual localiz", "visual servo", "place recogni",
            "camera pose", "relocali", "visual navigation",
        ],
    },
    {
        "label": "特征提取/匹配",
        "arxiv_queries": [
            "local feature extraction matching",
            "feature matching deep learning",
            "keypoint detection descriptor",
            "image feature matching",
            "sparse feature matching",
            "dense feature matching",
        ],
        "s2_queries": [
            "local feature matching deep learning",
            "keypoint detection description neural",
        ],
        "rss_keywords": [
            "feature extract", "feature match", "keypoint", "descriptor",
            "image retrieval", "feature detect",
        ],
    },
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
MAX_RESULTS_PER_QUERY = 20  # max arXiv results per sub-query
MAX_PAPERS_TOTAL = 3        # total papers in the daily digest

# 逐步扩大搜索窗口，直到凑满 MAX_PAPERS_TOTAL 篇
# 每次运行只做一次 API 抓取（取最大值），然后在内存里按窗口筛选
LOOKBACK_STEPS = [1, 3, 7, 30]
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

# ── Email (optional) ─────────────────────────────────────────────────────────
EMAIL_TO = os.environ.get("EMAIL_TO", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))

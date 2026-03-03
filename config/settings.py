import os
from dotenv import load_dotenv

# 加载 .env 环境变量
load_dotenv()

# ================= 基础路径配置 =================
# 获取项目根目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 数据库路径
DB_PATH = os.path.join(BASE_DIR, 'data', 'baostock.db')  # 🟢 V8.13 新库

# 字体路径
FONT_PATH = os.path.join(BASE_DIR, 'config', 'fonts', 'SimHei.ttf')

# 报告/日志输出目录 (这就是报错缺失的变量)
REPORT_DIR = os.path.join(BASE_DIR, 'reports')

# ================= API 与通知配置 =================
# DeepSeek 配置
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-reasoner")
API_URL = "https://api.deepseek.com/chat/completions"

# 微信推送配置
WECHAT_WEBHOOK_KEY = os.getenv("WECHAT_WEBHOOK_KEY")

# Discord 推送配置
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_SERVER_ID = os.getenv("DISCORD_SERVER_ID")
DISCORD_CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID")

# ================= 数据源配置 =================
# 🟢 V8.13: Baostock Only (不再使用 Tushare/AkShare)
# TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")  # Reverted (User Request: Stabilize)
DATA_SOURCE_PRIORITY = ["baostock"]  # Single Source

# ================= 系统运行参数 =================
# 扫描并发进程数
# 扫描并发进程数
# 🟢 Multiprocessing Config: Baostock allows multi-process (separate logins).
# 4 workers successfully reduces sync time (approx 4x speedup vs serial).
MAX_WORKERS = int(os.getenv("MAX_WORKERS", 4))

# 长图拼接数量
MAX_IMAGES_PER_BATCH = int(os.getenv("MAX_IMAGES_PER_BATCH", 10))

# ================= 策略参数配置 =================
# 通用
STRATEGY_MIN_DATA_LENGTH = 125  # Increased for MTR V35 safety
STRATEGY_DATA_FETCH_LIMIT = 300 # 🟢 Upgraded for MTR V35 (Needs 120 lookback)

# V7 Strategy
V7_ADX_STR_THRESHOLD = 15.0
V7_WICK_RATIO = 0.40
V7_CLOSE_POS_RATIO = 0.80
V7_EMA_SLOPE_THRESHOLD = -0.01
V7_EMA_DIST_THRESHOLD = 0.02
V7_SL_ATR_MULTIPLIER = 3.0

# 3K Strategy
K3_BODY_PCT_A = 0.50
K3_WICK_LONG_PCT = 0.33
K3_WICK_SHORT_PCT = 0.10
K3_GAP_CONFIRM_WINDOW = 5  # 突破缺口确认窗口（3K后N根K线确认缺口是否保持开放）
K3_GAP_TEST_MAX_WINDOW = 20  # 缺口测试确认最大等待窗口（3K后最多等N根K线确认买入）
K3_SWING_LOOKBACK = 40  # 波段高低点回溯窗口（向前看N根K线找前期波段高/低点）

# MTR Strategy
MTR_WICK_RATIO = 0.25 # Relaxed from 0.40 (Digital Abu Consensus)
MTR_CLOSE_POS_RATIO = 0.50 # Relaxed from 0.80 (Top Half)

# ================= 数据库与数据配置 =================
DB_POOL_SIZE = 10
DB_TIMEOUT = 60
DATA_FETCH_MINUTE_LIMIT = 500
DEFAULT_MINUTE_LIMIT = 200
ENABLE_SNAPSHOT_CACHE = True

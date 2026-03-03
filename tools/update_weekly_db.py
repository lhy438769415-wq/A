import sys
import os
import io
import logging

# 配置日志输出到控制台
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 添加根目录到包路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 强制禁用代理，防止 VPN 导致 Baostock 请求超时卡死
os.environ['HTTP_PROXY'] = ''
os.environ['HTTPS_PROXY'] = ''
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
os.environ['ALL_PROXY'] = ''
os.environ['all_proxy'] = ''

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from core.data_provider import update_weekly_data_batch

if __name__ == '__main__':
    print("==================================================")
    print("🚀 开始增量同步周线数据库 (15年历史 / 集成架构版)")
    print("==================================================")
    try:
        update_weekly_data_batch()
    except Exception as e:
        print(f"\n❌ 执行失败: {e}")

# -*- coding: utf-8 -*-
# 文件名: for_hold.py (位于 tools/ 目录)
"""
持仓分析模块
用于对已持有的股票进行双周期诊断分析
"""

import sys
import os
import pandas as pd
import logging
from typing import List, Dict, Optional

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 1. 路径处理：当前在 tools/ 目录下，需要把项目根目录加入 sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
sys.path.insert(0, project_root)

try:
    # 直接从子文件夹导入，路径更清晰
    from core.api_client import query_deepseek
    from core.formatter import format_for_ai, parse_response
    from core.calculator import add_indicators
    # Use new data provider, aliasing fetch_minute_bars to get_intraday_data for compatibility
    import core.data_provider as data_provider
    from tools.notifier import fetch_stock_name, send_discord_message
    from tools.journal import init_journal_db, log_guardian_decision
except ImportError as e:
    logger.error(f"❌ 导入模块失败: {e}")
    logger.error("请确保您在项目根目录下运行此脚本: python for_hold.py")
    sys.exit(1)

# 持仓文件放在项目根目录，方便编辑
HOLD_LIST_PATH = os.path.join(project_root, "hold_list.txt")


def load_holdings_with_cost() -> List[Dict[str, any]]:
    """从持仓文件中读取持仓信息

    Returns:
        List[Dict[str, any]]: 持仓列表，每个元素包含股票代码和成本
    """
    holdings = []
    if not os.path.exists(HOLD_LIST_PATH):
        logger.warning(f"⚠️ 未找到持仓文件: {HOLD_LIST_PATH}")
        return []
    try:
        with open(HOLD_LIST_PATH, "r", encoding="utf-8") as f:
            for line in f:
                # 过滤注释和空行
                line = line.strip()
                if not line or line.startswith("#"):
                    continue

                parts = line.split(',')
                if len(parts) >= 1:
                    code = parts[0].strip()
                    # 如果没写成本，默认为 0
                    cost = float(parts[1].strip()) if len(parts) > 1 else 0.0
                    holdings.append({'code': code, 'cost': cost})
        return holdings
    except Exception as e:
        logger.error(f"读取持仓文件出错: {e}")
        return []


def analyze_single_stock_micro(holding_item: Dict[str, any]) -> Optional[bool]:
    """分析单只持仓股票 (纯日线模式)

    Args:
        holding_item: 包含股票代码和成本的字典

    Returns:
        bool: 分析是否成功
    """
    code = holding_item['code']
    cost = holding_item['cost']
    name = fetch_stock_name(code)
    logger.info(f"\n🔬 [{name}|{code}] 正在进行持仓诊断 (日线)...")

    # -------------------------------------------------
    # 1. 准备数据 (仅日线)
    # -------------------------------------------------
    # 获取最近 150 天日线 (确保 EMA60/ATR 等指标计算有效)
    df_daily = data_provider.get_stock_data(code, limit=150)

    if df_daily is None or df_daily.empty:
        logger.warning(f"❌ {name}: 日线数据不足")
        return False

    try:
        df_daily = add_indicators(df_daily)
    except Exception as e:
        logger.error(f"指标计算失败: {e}")
        return False

    # -------------------------------------------------
    # 2. 调用 Formatter 生成 Guardian Prompt
    # -------------------------------------------------
    # 构造持仓上下文
    holding_ctx = {
        'code': code, 
        'cost': cost,
        'df': df_daily,
        'type': 'HOLDING_CHECK' 
    }
    
    try:
        # 使用 Guardian 专用 Prompt
        from core.formatter import format_guardian_prompt
        prompt = format_guardian_prompt(holding_ctx)
    except Exception as e:
        logger.error(f"生成Guardian Prompt失败: {e}")
        return False

    # 3. DeepSeek 推理
    logger.info(f"🧠 AI正在分析持仓风险 (Guardian Mode)...")
    response_text = query_deepseek(prompt)
    
    # 解析响应
    parsed = parse_response(response_text)
    
    status = parsed.get('status', 'HOLD')
    warning = parsed.get('warning', 'None')
    new_stop = parsed.get('new_stop', 'N/A')
    advice = parsed.get('position_adjust', 'Keep Position')
    reason = parsed.get('reason', '无具体理由')

    # 4. 组装最终消息 (Guardian 风格)
    current_price = df_daily.iloc[-1]['close']
    pnl_val = current_price - cost
    pnl_pct = (pnl_val / cost * 100) if cost > 0 else 0.0
    pnl_emoji = "🔴" if pnl_pct >= 0 else "🟢"  # A股：红涨绿跌
    
    # 状态图标
    status_icon = "🛡️"
    if "EXIT" in status.upper(): status_icon = "🛑"
    elif "TRIM" in status.upper(): status_icon = "✂️"
    
    warning_str = ""
    if warning and warning.upper() != "NONE":
         warning_str = f"\n⚠️ **预警**: {warning}"
    
    final_content = (
        f"{status_icon} **Guardian 持仓日报**\n"
        f"🔍 **{name}（{code}）**\n"
        f"💰 **现价**: {current_price:.2f} (成本: {cost:.2f})\n"
        f"{pnl_emoji} **浮盈**: {pnl_pct:+.2f}%\n"
        f"---------------\n"
        f"📅 **状态**: {status}\n"
        f"🛑 **止损建议**: {new_stop}{warning_str}\n"
        f"📝 **策略**: {advice}\n"
        f"💡 **理由**: {reason}\n"
    )

    # 发送
    try:
        send_discord_message(final_content)
        # 记录日志 (SOP Step 16)
        log_guardian_decision(code, {'verdict': status, 'reason': reason, 'raw': response_text})
        logger.info(f"✅ {name} 诊断完成 ({status})")
        return True
    except Exception as e:
        logger.error(f"❌ 发送消息/记录日志失败: {e}")
        return False


if __name__ == "__main__":
    logger.info("🚀 === 持仓双周期管家 (For Hold) ===\\n")
    init_journal_db()  # 初始化日志库

    holdings = load_holdings_with_cost()
    logger.info(f"📋 读取到 {len(holdings)} 只持仓股票")

    # 🟢 增强：添加数据完整性检查提示
    if len(holdings) == 0:
        logger.warning("⚠️ 警告：持仓文件为空")
        logger.info("💡 请确认 hold_list.txt 文件是否存在且格式正确")
        logger.info("格式示例：sh.600889,16.34")
        exit(1)

    # 🟢 优化：增加处理统计
    success_count = 0
    error_count = 0

    for item in holdings:
        try:
            result = analyze_single_stock_micro(item)
            if result:
                success_count += 1
            else:
                error_count += 1
        except Exception as e:
            logger.error(f"❌ 处理 {item['code']} 时发生未知错误: {e}")
            error_count += 1

    logger.info(f"\\n📊 处理完成: 成功 {success_count} 只, 失败 {error_count} 只")
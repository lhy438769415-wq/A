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


def _get_trade_thesis(code: str) -> dict:
    """从 signal_archive 查询该持仓的交易论据 (Trade Thesis)
    
    查找该代码最近一条 ACTIVE/PENDING 状态的信号记录,
    提取入场价、止损、目标等关键参数作为 AI 分析的上下文。
    """
    try:
        from core.database import get_db_connection
        import core.data_provider as dp_check
        
        with get_db_connection() as conn:
            row = conn.execute("""
                SELECT strategy, signal_date, entry_price, sl_price, tp_price, 
                       ev_rating, status, timeframe
                FROM signal_archive 
                WHERE code = ? AND status IN ('PENDING', 'ACTIVE')
                ORDER BY created_at DESC LIMIT 1
            """, (code,)).fetchone()
            
            if not row:
                return {}
            
            thesis = {
                'strategy': row[0] or '未知',
                'signal_date': row[1] or '未知',
                'entry_price': row[2] or 0,
                'sl_price': row[3] or 0,
                'tp_price': row[4] or 0,
                'ev_rating': row[5] or 'N/A',
                'status': row[6] or 'UNKNOWN',
                'timeframe': row[7] or 'daily',
            }
            
            # 检查缺口是否仍然开放 (仅对 structural_gap 策略)
            if 'STRUCTURAL_GAP' in thesis['strategy'].upper():
                try:
                    tf = thesis['timeframe']
                    if tf == 'weekly':
                        df_check = dp_check.get_stock_data_weekly(code, limit=10)
                    else:
                        df_check = dp_check.get_stock_data(code, limit=10)
                    
                    if df_check is not None and not df_check.empty:
                        latest_low = df_check['low'].iloc[-1]
                        gap_floor = thesis['sl_price']
                        thesis['gap_status'] = '开放 ✅' if latest_low > gap_floor else '已击穿 ❌'
                    else:
                        thesis['gap_status'] = '数据不足'
                except:
                    thesis['gap_status'] = '检查失败'
            else:
                thesis['gap_status'] = 'N/A'
            
            return thesis
    except Exception as e:
        logger.warning(f"查询交易论据失败 ({code}): {e}")
        return {}


def analyze_single_stock_micro(holding_item: Dict[str, any]) -> Optional[bool]:
    """分析单只持仓股票 (PA 日线诊断 + 交易论据上下文)

    Args:
        holding_item: 包含股票代码和成本的字典

    Returns:
        bool: 分析是否成功
    """
    code = holding_item['code']
    cost = holding_item['cost']
    name = fetch_stock_name(code)
    logger.info(f"\n🔬 [{name}|{code}] 正在进行持仓诊断...")

    # -------------------------------------------------
    # 1. 准备数据 (日线 PA)
    # -------------------------------------------------
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
    # 2. 查询交易论据 (Trade Thesis)
    # -------------------------------------------------
    trade_thesis = _get_trade_thesis(code)
    if trade_thesis:
        logger.info(f"  📋 交易论据: {trade_thesis['strategy']} | SL={trade_thesis['sl_price']:.2f} | TP={trade_thesis['tp_price']:.2f} | 缺口={trade_thesis.get('gap_status', 'N/A')}")
    else:
        logger.info(f"  ⚠️ 无归档信号记录, 使用基础分析模式")

    # -------------------------------------------------
    # 3. 调用 Formatter 生成 Guardian Prompt (带交易论据)
    # -------------------------------------------------
    holding_ctx = {
        'code': code, 
        'cost': cost,
        'df': df_daily,
        'type': 'HOLDING_CHECK',
        'trade_thesis': trade_thesis,
    }
    
    try:
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

    # 4. 组装最终消息 (用户体验优先)
    current_price = df_daily.iloc[-1]['close']
    pnl_val = current_price - cost
    pnl_pct = (pnl_val / cost * 100) if cost > 0 else 0.0
    pnl_emoji = "🔴" if pnl_pct >= 0 else "🟢"  # A股：红涨绿跌
    
    # 状态图标 + 颜色
    status_upper = status.upper()
    if "EXIT" in status_upper:
        status_icon, status_cn = "🛑", "建议离场"
    elif "TRIM" in status_upper:
        status_icon, status_cn = "✂️", "建议减仓"
    else:
        status_icon, status_cn = "🛡️", "继续持有"
    
    # AI 一句话摘要 (优先用 discord_msg, 比完整 reason 更简洁)
    one_liner = parsed.get('discord_msg') or reason
    if len(one_liner) > 80:
        one_liner = one_liner[:77] + "..."
    
    # 预警信息
    warning_line = ""
    if warning and warning.upper() != "NONE":
        warning_line = f"⚠️ 预警: {warning}\n"
    
    # 构建消息: 用户最关心的信息在最前面
    final_content = (
        f"{status_icon} **{name}** ({code}) — **{status_cn}**\n"
        f"{pnl_emoji} 现价 {current_price:.2f} | 成本 {cost:.2f} | 浮盈 **{pnl_pct:+.1f}%**\n"
        f"{warning_line}"
        f"🎯 止损 → {new_stop} | 目标 {trade_thesis.get('tp_price', 0):.2f}\n"
        f"💡 {one_liner}\n"
    )

    # 发送文字 + 图表
    try:
        send_discord_message(final_content)
        
        # 推送 K 线图 (带 SL/TP 标线, 让用户可视化评估)
        try:
            from tools.notifier import generate_chart_bytes, send_discord_image
            sl_for_chart = trade_thesis.get('sl_price', 0)
            tp_for_chart = trade_thesis.get('tp_price', 0)
            strategy_type = trade_thesis.get('strategy', 'STRUCTURAL_GAP')
            
            chart_buf = generate_chart_bytes(
                code=code, stock_name=name,
                strategy_type=strategy_type,
                sl_price=sl_for_chart, tp1=tp_for_chart,
                ev_rating=trade_thesis.get('ev_rating', ''),
            )
            if chart_buf:
                send_discord_image(chart_buf, filename=f"guardian_{code}.png")
        except Exception as e:
            logger.warning(f"图表生成/推送失败: {e}")
        
        # 记录日志
        log_guardian_decision(code, {'verdict': status, 'reason': reason, 'raw': response_text})
        logger.info(f"✅ {name} 诊断完成 ({status_cn})")
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
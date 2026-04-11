import os
import sys
import pandas as pd
import numpy as np
import logging

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.data_provider import get_stock_list, get_stock_data_weekly

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def backtest_pattern(limit_stocks=None, max_hold_weeks=12):
    """
    回测: 周线牛旗三推 (Weekly Bull Flag 3 Pushes)
    寻找达到 "测量缺口止盈位置" 的盈亏情况
    """
    stock_list = get_stock_list()
    if not stock_list:
        logger.error("No stocks found.")
        return
        
    target_stocks = stock_list[:limit_stocks] if limit_stocks is not None else stock_list
    logger.info(f"🚀 开始回测 {len(target_stocks)} 只股票的历史数据...")
    
    # 统计数据
    stats = {
        'total_signals': 0,
        'wins': 0,
        'losses': 0,
        'timeouts': 0,
        'avg_win_weeks': [],
        'avg_loss_weeks': [],
        'total_profit_r': 0.0,
        'total_loss_r': 0.0
    }
    
    trade_log = []

    for symbol in target_stocks:
        try:
            # 至少需要充足的历史数据
            df_weekly = get_stock_data_weekly(symbol, limit=200)
            if df_weekly is None or len(df_weekly) < 50:
                continue
                
            df_weekly = df_weekly.copy().reset_index(drop=True)
            
            # 兼容 talib 确保平滑
            try:
                import talib
                df_weekly['ema20'] = talib.EMA(df_weekly['close'], timeperiod=20)
            except ImportError:
                df_weekly['ema20'] = df_weekly['close'].ewm(span=20, adjust=False).mean()
            
            df_weekly['body'] = (df_weekly['close'] - df_weekly['open']).abs()
            df_weekly['body_pct'] = df_weekly['body'] / df_weekly['open']
            
            # --- 向量化预计算特征 ---
            is_bull_breakout = (df_weekly['close'] > df_weekly['open']) & \
                               (df_weekly['body_pct'] > 0.05) & \
                               (df_weekly['close'] > df_weekly['ema20'])
                               
            highs_decreasing = (df_weekly['high'].shift(1) >= df_weekly['high'] * 0.98) & \
                               (df_weekly['high'].shift(2) >= df_weekly['high'].shift(1) * 0.98)
                               
            lows_increasing = (df_weekly['low'] >= df_weekly['low'].shift(1) * 0.97) & \
                              (df_weekly['low'].shift(1) >= df_weekly['low'].shift(2) * 0.97)
            
            weekly_signal = is_bull_breakout.shift(3).fillna(False).astype(bool) & \
                            highs_decreasing & \
                            lows_increasing
                            
            signal_indices = df_weekly.index[weekly_signal].tolist()
            
            for t in signal_indices:
                # 剔除过于靠后的信号，无法完成回测
                if t + 1 >= len(df_weekly):
                    continue
                
                # 提取锚点特征
                t_breakout = t - 3
                leg_low = df_weekly.loc[t_breakout, 'low']
                leg_high = df_weekly.loc[t_breakout, 'high']
                
                # 回调区低点 = 这 3 周的最低点
                pullback_low = df_weekly.loc[t-2:t, 'low'].min()
                
                # 如果回调低点已经击穿了起涨点，这个信号是失效的
                if pullback_low < leg_low:
                    continue
                
                # 交易计划 (Trade Plan)
                entry_price = df_weekly.loc[t+1, 'open']
                
                # 止损: 放在测量缺口的底部 (Gap Floor)，即爆发根的最低点下方
                stop_loss = leg_low * 0.985
                
                if entry_price <= stop_loss:
                    continue # 开盘即止损，跳过
                
                # 止盈 (测量缺口测算 Target = Pullback Low + Initial Leg Height)
                # Al Brooks 测算: 假设这是一段 AB=CD 运动，C点(回调低点)起涨，高度等同于AB(突破段)
                leg_height = leg_high - leg_low
                take_profit_1 = pullback_low + leg_height
                # 或者激进目标 (Breakout High + Height)
                take_profit_2 = leg_high + leg_height
                
                # 取保守的 AB=CD 缺口目标
                take_profit = take_profit_1
                
                # 计算初始风险 (1R) 和 潜在收益
                risk = entry_price - stop_loss
                reward = take_profit - entry_price
                if risk <= 0:
                    continue # 必须要有风险空间，否则没法计算R
                
                potential_r_multiple = reward / risk
                
                stats['total_signals'] += 1
                
                # 开始跨期推进
                status = 'TIMEOUT'
                hold_weeks = 0
                for fwd in range(t+1, min(t+1+max_hold_weeks, len(df_weekly))):
                    curr_bar = df_weekly.loc[fwd]
                    hold_weeks += 1
                    
                    # 悲观原则: 如果同周既触及止盈又触及止损，当做止损处理
                    if curr_bar['low'] <= stop_loss:
                        status = 'LOSS'
                        stats['losses'] += 1
                        stats['avg_loss_weeks'].append(hold_weeks)
                        stats['total_loss_r'] -= 1.0  # 亏损固定为 -1R
                        break
                    elif curr_bar['high'] >= take_profit:
                        status = 'WIN'
                        stats['wins'] += 1
                        stats['avg_win_weeks'].append(hold_weeks)
                        stats['total_profit_r'] += potential_r_multiple # 盈利获得目标的 R 倍数
                        break
                
                if status == 'TIMEOUT':
                    stats['timeouts'] += 1
                    
                trade_log.append({
                    'symbol': symbol,
                    'signal_date': df_weekly.loc[t, 'trade_date'],
                    'entry': round(entry_price, 2),
                    'sl': round(stop_loss, 2),
                    'tp': round(take_profit, 2),
                    'status': status,
                    'weeks': hold_weeks
                })

        except Exception as e:
            logger.error(f"Error processing {symbol}: {e}")
            continue

    # =================输出报告=================
    report_lines = []
    report_lines.append("\n" + "="*50)
    report_lines.append("🦅 Brooks-AI MTR/Gap: 周线牛旗三推 历史回测报告")
    report_lines.append("="*50)
    report_lines.append(f"📊 扫描标的数: {len(target_stocks)} (全库扫描)")
    report_lines.append(f"信号总数: {stats['total_signals']}")
    
    if stats['total_signals'] > 0:
        win_rate = stats['wins'] / stats['total_signals'] * 100
        loss_rate = stats['losses'] / stats['total_signals'] * 100
        timeout_rate = stats['timeouts'] / stats['total_signals'] * 100
        
        avg_w_weeks = np.mean(stats['avg_win_weeks']) if stats['avg_win_weeks'] else 0
        avg_l_weeks = np.mean(stats['avg_loss_weeks']) if stats['avg_loss_weeks'] else 0
        
        # 计算数学期望 (Expectancy)
        total_r = stats['total_profit_r'] + stats['total_loss_r']
        expectancy_per_trade = total_r / stats['total_signals']
        
        report_lines.append(f"✅ 止盈 (Wins): {stats['wins']} ({win_rate:.2f}%) | 平均持有 {avg_w_weeks:.1f} 周")
        report_lines.append(f"❌ 止损 (Losses): {stats['losses']} ({loss_rate:.2f}%) | 平均持有 {avg_l_weeks:.1f} 周")
        report_lines.append(f"⏳ 超时退场 (Timeouts): {stats['timeouts']} ({timeout_rate:.2f}%) (超过 {max_hold_weeks} 周未能达到目标)")
        report_lines.append("-" * 30)
        report_lines.append(f"📈 累计净 R 值: {total_r:.2f} R")
        report_lines.append(f"🎯 单笔系统数学期望 (EV): {expectancy_per_trade:.4f} R")
        
        # 打印部分经典胜利案例
        report_lines.append("\n🏆 经典胜利案例 (Top 5):")
        win_trades = [t for t in trade_log if t['status'] == 'WIN']
        
        report_lines.append(f"{'Symbol':<10} | {'Signal Date':<12} | {'Entry':<8} | {'SL':<8} | {'TP':<8} | {'Hold Weeks'}")
        report_lines.append("-" * 65)
        for t in win_trades[:5]:
            report_lines.append(f"{t['symbol']:<10} | {str(t['signal_date'])[:10]:<12} | {t['entry']:<8.2f} | {t['sl']:<8.2f} | {t['tp']:<8.2f} | {t['weeks']}")
        
    else:
        report_lines.append("未发现有效符合交易计划的信号。")
        
    report_text = "\n".join(report_lines)
    print(report_text) # Still print to console
    
    with open("backtest_report.txt", "w", encoding="utf-8") as f:
        f.write(report_text)


if __name__ == "__main__":
    import warnings
    warnings.simplefilter(action='ignore', category=FutureWarning)
    
    # 将 max_hold_weeks 设置为极大值 (如 9999)，几乎取消超时限制，让所有单子走到最终止盈/止损或走到数据尽头
    backtest_pattern(limit_stocks=None, max_hold_weeks=9999)

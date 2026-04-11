import os
import sys
import pandas as pd
import numpy as np
import logging

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.data_provider import get_stock_list, get_stock_data_weekly

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def backtest_pattern(limit_stocks=None, max_hold_weeks=9999):
    """
    回测: 周线突破缺口 + IOI (Inside-Outside-Inside)
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
            df_weekly = get_stock_data_weekly(symbol, limit=400)
            if df_weekly is None or len(df_weekly) < 50:
                continue
                
            df_weekly = df_weekly.copy().reset_index(drop=True)
            
            try:
                import talib
                df_weekly['ema20'] = talib.EMA(df_weekly['close'], timeperiod=20)
            except ImportError:
                df_weekly['ema20'] = df_weekly['close'].ewm(span=20, adjust=False).mean()
            
            df_weekly['body'] = (df_weekly['close'] - df_weekly['open']).abs()
            df_weekly['body_pct'] = df_weekly['body'] / df_weekly['open']
            
            # 1. 突破缺口 (t-3)
            is_bull_breakout = (df_weekly['close'] > df_weekly['open']) & \
                               (df_weekly['body_pct'] > 0.05) & \
                               (df_weekly['close'] > df_weekly['ema20'])
            
            # 2. I 线 (t-2): Inside Bar
            # 高点低于(或等于)前高，低点高于(或等于)前低
            is_inside_1 = (df_weekly['high'] <= df_weekly['high'].shift(1)) & \
                          (df_weekly['low'] >= df_weekly['low'].shift(1))
                          
            # 3. O 线 (t-1): Outside Bar
            # 高点高于前高，低点低于前低
            is_outside = (df_weekly['high'] > df_weekly['high'].shift(1)) & \
                         (df_weekly['low'] < df_weekly['low'].shift(1))
                         
            # 4. I 线 (t): Inside Bar
            is_inside_2 = (df_weekly['high'] <= df_weekly['high'].shift(1)) & \
                          (df_weekly['low'] >= df_weekly['low'].shift(1))
            
            # 信号拼接
            weekly_signal = is_bull_breakout.shift(3).fillna(False).astype(bool) & \
                            is_inside_1.shift(2).fillna(False).astype(bool) & \
                            is_outside.shift(1).fillna(False).astype(bool) & \
                            is_inside_2.fillna(False).astype(bool)
                            
            signal_indices = df_weekly.index[weekly_signal].tolist()
            
            for t in signal_indices:
                if t + 1 >= len(df_weekly):
                    continue
                
                # 提取特征
                t_breakout = t - 3
                leg_low = df_weekly.loc[t_breakout, 'low']
                leg_high = df_weekly.loc[t_breakout, 'high']
                
                # 最新的 I 线的高点作为入场突破位
                i2_high = df_weekly.loc[t, 'high']
                
                # 回调区低点 = IOI过程中的最低点 (也就是 O 线的低点)
                pullback_low = df_weekly.loc[t-1, 'low']
                
                # 失效判定：如果 O 线直接跌破了突破根的最低点 (Gap Floor)，说明缺口已经完全封闭
                if pullback_low < leg_low:
                    continue
                
                # 交易计划
                # 触发价 (Buy Stop): 在第二个 I 线的高点往上一点点
                buy_stop_price = i2_high + 0.01
                
                # 检查下一周是否能触发
                next_bar = df_weekly.loc[t+1]
                if next_bar['high'] < buy_stop_price:
                    continue # 没触发，放弃
                
                # 滑点处理：如果开盘直接高开越过buy_stop，以开盘价成交
                entry_price = max(buy_stop_price, next_bar['open'])
                
                # 止损: 从测量缺口底部上移至 O 线的最低点 (即 pullback_low) 下方
                # 理论支撑：一旦跌破 O 线低点，基于 IOI 终极洗盘的看涨前提即告破裂 (Premise Invalidated)
                stop_loss = pullback_low * 0.985
                
                if entry_price <= stop_loss:
                    continue 
                
                # 止盈 (测量缺口测算 Target = Pullback Low + Initial Leg Height)
                leg_height = leg_high - leg_low
                take_profit = pullback_low + leg_height
                
                risk = entry_price - stop_loss
                if risk <= 0:
                    continue
                
                reward = take_profit - entry_price
                potential_r_multiple = reward / risk
                
                stats['total_signals'] += 1
                
                # 为了简化回测，假设如果在触发当周就同时满足了止盈和止损（极端波动），悲观记为亏损
                status = 'TIMEOUT'
                hold_weeks = 0
                for fwd in range(t+1, min(t+1+max_hold_weeks, len(df_weekly))):
                    curr_bar = df_weekly.loc[fwd]
                    hold_weeks += 1
                    
                    if curr_bar['low'] <= stop_loss:
                        status = 'LOSS'
                        stats['losses'] += 1
                        stats['avg_loss_weeks'].append(hold_weeks)
                        stats['total_loss_r'] -= 1.0  
                        break
                    elif curr_bar['high'] >= take_profit:
                        status = 'WIN'
                        stats['wins'] += 1
                        stats['avg_win_weeks'].append(hold_weeks)
                        stats['total_profit_r'] += potential_r_multiple 
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
    report_lines.append("🦅 Brooks-AI MTR/Gap: 周线缺口 IOI 形态 历史回测报告")
    report_lines.append("="*50)
    report_lines.append(f"📊 扫描标的数: {len(target_stocks)} (全库扫描)")
    report_lines.append(f"信号总数: {stats['total_signals']}")
    
    if stats['total_signals'] > 0:
        win_rate = stats['wins'] / stats['total_signals'] * 100
        loss_rate = stats['losses'] / stats['total_signals'] * 100
        timeout_rate = stats['timeouts'] / stats['total_signals'] * 100
        
        avg_w_weeks = np.mean(stats['avg_win_weeks']) if stats['avg_win_weeks'] else 0
        avg_l_weeks = np.mean(stats['avg_loss_weeks']) if stats['avg_loss_weeks'] else 0
        
        total_r = stats['total_profit_r'] + stats['total_loss_r']
        expectancy_per_trade = total_r / stats['total_signals']
        
        report_lines.append(f"✅ 止盈 (Wins): {stats['wins']} ({win_rate:.2f}%) | 平均持有 {avg_w_weeks:.1f} 周")
        report_lines.append(f"❌ 止损 (Losses): {stats['losses']} ({loss_rate:.2f}%) | 平均持有 {avg_l_weeks:.1f} 周")
        report_lines.append(f"⏳ 未决退场: {stats['timeouts']} ({timeout_rate:.2f}%)")
        report_lines.append("-" * 30)
        report_lines.append(f"📈 累计净 R 值: {total_r:.2f} R")
        report_lines.append(f"🎯 单笔系统数学期望 (EV): {expectancy_per_trade:.4f} R")
        
        report_lines.append("\n🏆 经典胜利案例 (Top 5):")
        win_trades = [t for t in trade_log if t['status'] == 'WIN']
        
        report_lines.append(f"{'Symbol':<10} | {'Signal Date':<12} | {'Entry':<8} | {'SL':<8} | {'TP':<8} | {'Hold Weeks'}")
        report_lines.append("-" * 65)
        for t in win_trades[:5]:
            report_lines.append(f"{t['symbol']:<10} | {str(t['signal_date'])[:10]:<12} | {t['entry']:<8.2f} | {t['sl']:<8.2f} | {t['tp']:<8.2f} | {t['weeks']}")
        
    else:
        report_lines.append("未发现有效符合交易计划的信号。")
        
    report_text = "\n".join(report_lines)
    print(report_text) 
    
    with open("backtest_ioi_report.txt", "w", encoding="utf-8") as f:
        f.write(report_text)


if __name__ == "__main__":
    import warnings
    warnings.simplefilter(action='ignore', category=FutureWarning)
    backtest_pattern(limit_stocks=None, max_hold_weeks=9999)

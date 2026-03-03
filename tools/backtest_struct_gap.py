import os
import sys
import pandas as pd
import numpy as np
from datetime import datetime
import logging
from concurrent.futures import ProcessPoolExecutor, as_completed

# Ensure project root is in path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from core.data_provider import get_stock_data, get_stock_list
from core.calculator import add_indicators
from core.strategies.structural_gap_strategy import StructuralGapStrategy
from config import settings

logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def evaluate_trade(df: pd.DataFrame, signal_idx: int) -> dict:
    """
    模拟由突破单触发后的后续走势，并提取高维 PA 回撤特征。
    判定是先打止损还是先打止盈。
    """
    try:
        # 提取当天的信号参数
        sig_row = df.iloc[signal_idx]
        entry_price = sig_row['entry_struct_gap']
        sl_price = sig_row['sl_struct_gap']
        tp_price = sig_row['tp_struct_gap']
        
        outcome = "UNKNOWN"
        reason = "EOF"
        hold_bars = 0
        
        # --- [新增] 提取触发日之前的 Pullback PA 特征 ---
        # Find the index of the breakout day (where bars_since_breakout is 0)
        breakout_indices = df.index[df['bars_since_breakout'] == 0]
        pb_start_idx = breakout_indices[breakout_indices <= df.index[signal_idx]].max()
        
        # 防止找不到突破日
        if pd.isna(pb_start_idx):
             # 仍继续交易，只是缺乏回撤特征
             pb_df = pd.DataFrame()
        else:
             # Pullback period is from breakout day up to the day before the signal
             pb_df = df.loc[pb_start_idx:df.index[signal_idx]-1]
        
        # If there's no actual pullback (e.g., signal on breakout day), pb_df might be empty
        if not pb_df.empty:
            # 计算连续阴线
            is_bear = pb_df['close'] < pb_df['open']
            # Group consecutive True values (bearish bars)
            consec_bear = is_bear.groupby((~is_bear).cumsum()).sum()
            max_consec_bear = consec_bear.max() if not consec_bear.empty else 0
            
            # 重叠度
            overlaps = []
            for i in range(1, len(pb_df)):
                prev_close = pb_df['close'].iloc[i-1]
                curr_high = pb_df['high'].iloc[i]
                curr_low = pb_df['low'].iloc[i]
                bar_range = curr_high - curr_low
                if bar_range > 0 and curr_high > prev_close:
                    overlap = min(curr_high - prev_close, bar_range) / bar_range
                    overlaps.append(overlap)
                else:
                    overlaps.append(0)
            avg_overlap = np.mean(overlaps) if len(overlaps) > 0 else 0
    
            bear_pct = is_bear.mean()
             
        # --- 回测主体 ---
        # 信号当天，无法成交（要等第二天开盘后挂 Buy Stop）
        # 从信号日的下一个交易日开始往后看
        post_signal = df.iloc[signal_idx + 1:]
        
        if post_signal.empty:
            return {'status': 'PENDING', 'reason': '数据结尾，尚未触发入场'}
            
        trade_status = 'WAITING_TRIGGER'
        entry_date = None
        exit_date = None
        exit_price = None
        exit_reason = ""
        
        for i, row in post_signal.iterrows():
            if trade_status == 'WAITING_TRIGGER':
                # 检查是否撤单：如果在触发入场前，直接跌破了防守底线，则整个形态破位，信号作废（取消挂单）
                if row['low'] < sl_price:
                    return {'status': 'INVALIDATED', 'reason': '未入场即跌穿防守线，信号作废'}
                
                # 检查是否触发入场 (Buy Stop 订单：股价一旦上摸入场价即刻市价成交)
                if row['high'] >= entry_price:
                    # 考虑到可能跳空高开直接越过 entry_price
                    actual_entry = max(entry_price, row['open'])
                    entry_date = row['date'] if 'date' in row else i
                    trade_status = 'IN_TRADE'
                    
                    # 极限情况：同一天既触发了入场，又触发了止损/止盈（日内宽幅震荡）
                    # 保守起见（最恶劣假设），我们假设先扫了止损
                    if row['low'] <= sl_price:
                        return {
                            'status': 'LOSS', 
                            'entry_date': entry_date, 'exit_date': entry_date,
                            'entry_price': actual_entry, 'exit_price': sl_price,
                            'reason': '入场当日巨震扫损',
                            'sig_quality': sig_row.get('sig_bar_quality', 0),
                            'pb_bear_pct': bear_pct if not pb_df.empty else 0,
                            'pb_overlap': avg_overlap if not pb_df.empty else 0,
                            'pb_consec_bear': max_consec_bear if not pb_df.empty else 0
                        }
                    
                    if row['high'] >= tp_price:
                        return {
                            'status': 'WIN',
                            'entry_date': entry_date, 'exit_date': entry_date,
                            'entry_price': actual_entry, 'exit_price': tp_price,
                            'reason': '入场当日即秒打止盈',
                            'sig_quality': sig_row.get('sig_bar_quality', 0),
                            'pb_bear_pct': bear_pct if not pb_df.empty else 0,
                            'pb_overlap': avg_overlap if not pb_df.empty else 0,
                            'pb_consec_bear': max_consec_bear if not pb_df.empty else 0
                        }
            
            elif trade_status == 'IN_TRADE':
                # 持仓状态下的退出判定
                
                # 1. 检查止损（防守底线被击穿）
                # 同样，如果是大幅跳空低开，真正的止损价是开盘价
                if row['low'] <= sl_price:
                    actual_exit = min(sl_price, row['open'])
                    return {
                        'status': 'LOSS',
                        'entry_date': entry_date, 'exit_date': row['date'] if 'date' in row else i,
                        'entry_price': actual_entry, 'exit_price': actual_exit,
                        'reason': '正常扫损退场',
                        'sig_quality': sig_row.get('sig_bar_quality', 0),
                        'pb_bear_pct': bear_pct if not pb_df.empty else 0,
                        'pb_overlap': avg_overlap if not pb_df.empty else 0,
                        'pb_consec_bear': max_consec_bear if not pb_df.empty else 0
                    }
                    
                # 2. 检查止盈（触顶）
                if row['high'] >= tp_price:
                    actual_exit = max(tp_price, row['open'])
                    return {
                        'status': 'WIN',
                        'entry_date': entry_date, 'exit_date': row['date'] if 'date' in row else i,
                        'entry_price': actual_entry, 'exit_price': tp_price,  # 限价止盈单必定是以指定价成交，除非跳空高开
                        'reason': '正常打止盈点',
                        'sig_quality': sig_row.get('sig_bar_quality', 0),
                        'pb_bear_pct': bear_pct if not pb_df.empty else 0,
                        'pb_overlap': avg_overlap if not pb_df.empty else 0,
                        'pb_consec_bear': max_consec_bear if not pb_df.empty else 0
                    }
                    
        # 如果走完了数据还没打到止盈/止损
        if trade_status == 'IN_TRADE':
             return {
                 'status': 'HOLDING', 
                 'entry_date': entry_date,
                 'entry_price': actual_entry,
                 'reason': '持仓中至今未达目标',
                 'sig_quality': sig_row.get('sig_bar_quality', 0),
                 'pb_bear_pct': bear_pct if not pb_df.empty else 0,
                 'pb_overlap': avg_overlap if not pb_df.empty else 0,
                 'pb_consec_bear': max_consec_bear if not pb_df.empty else 0
             }
             
        return {
            'status': 'PENDING', 
            'reason': '挂单中至今未触发',
            'sig_quality': sig_row.get('sig_bar_quality', 0),
            'pb_bear_pct': bear_pct if not pb_df.empty else 0,
            'pb_overlap': avg_overlap if not pb_df.empty else 0,
            'pb_consec_bear': max_consec_bear if not pb_df.empty else 0
        }
        
    except Exception as e:
        return {'status': 'ERROR', 'reason': str(e)}

def backtest_single_stock(code: str, bars_limit=1500) -> list:
    """ 回测单支股票并返回所有交易结果 """
    try:
        df = get_stock_data(code, limit=bars_limit)
        if df is None or len(df) < 100:
            return []
            
        df = add_indicators(df)
        strategy = StructuralGapStrategy()
        df = strategy.calculate_signals(df)
        
        # 提取信号
        signal_indices = [i for i, val in enumerate(df['signal_struct_gap_confirm']) if val]
        
        results = []
        for idx in signal_indices:
            trade_res = evaluate_trade(df, idx)
            if trade_res['status'] in ['SKIP', 'FILTERED']:
                continue
                
            # 添加上下文元数据
            sig_date = df.iloc[idx]['date'] if 'date' in df.columns else df.index[idx]
            if hasattr(sig_date, 'strftime'):
                sig_date = sig_date.strftime('%Y-%m-%d')
            else:
                sig_date = str(sig_date)
                
            trade_res['code'] = code
            trade_res['signal_date'] = sig_date
            
            # 计算盈亏金额与比例
            if trade_res['status'] in ['WIN', 'LOSS']:
                profit = trade_res['exit_price'] - trade_res['entry_price']
                risk = trade_res['entry_price'] - df.iloc[idx]['sl_struct_gap'] 
                trade_res['profit'] = profit
                trade_res['rr'] = profit / risk if risk > 0 else 0
            
            results.append(trade_res)
            
        return results
        
    except Exception as e:
        logger.debug(f"Error testing {code}: {e}")
        return []

def run_batch_backtest(limit=300, bars_limit=1500):
    """
    运行全景回测 (多进程)
    """
    logger.info(f"🚀 初始化 Structural Gap (V3.0) 宏观战略回测引擎...")
    all_codes = get_stock_list()
    if not all_codes:
        logger.error("无法获取股票列表")
        return
        
    if limit > 0:
        all_codes = all_codes[:limit]
        
    logger.info(f"📊 准备扫描 {len(all_codes)} 只标的 (回溯约 {(bars_limit/250):.1f} 年)...")
    
    all_trades = []
    
    with ProcessPoolExecutor(max_workers=settings.MAX_WORKERS) as executor:
        futures = {executor.submit(backtest_single_stock, code, bars_limit): code for code in all_codes}
        
        completed = 0
        for future in as_completed(futures):
            completed += 1
            if completed % 100 == 0:
                print(f"⌛ 进度: {completed} / {len(all_codes)}", end='\r')
                
            trades = future.result()
            if trades:
                all_trades.extend(trades)
                
    print(f"\n✅ 扫描完毕。全市场累计剥离出形态信号: {len(all_trades)} 笔。")
    
    # ====== 统计局 ======
    if not all_trades:
        logger.info("未产生任何有效信号。")
        return
        
    total_signals = len(all_trades)
    
    # 分类
    wins = [t for t in all_trades if t['status'] == 'WIN']
    losses = [t for t in all_trades if t['status'] == 'LOSS']
    invalidated = [t for t in all_trades if t['status'] == 'INVALIDATED']
    holding = [t for t in all_trades if t['status'] == 'HOLDING']
    pending = [t for t in all_trades if t['status'] == 'PENDING']
    
    completed_trades = len(wins) + len(losses)
    
    print("\n" + "="*50)
    print("📈 Structural Gap V3.0 (日线级别) 全局真实回测报告")
    print("="*50)
    print(f"• 总捕获高潮突破信号: {total_signals} 次")
    print(f"• 最终未入场破位(撤单): {len(invalidated)} 次 (无损躲过)")
    print(f"• 仍未触发及持仓中: {len(pending) + len(holding)} 次")
    print(f"• 真正已结案的实战交易: {completed_trades} 笔")
    print("-"*50)
    
    if completed_trades > 0:
        win_rate = len(wins) / completed_trades * 100
        print(f"🏆 【全局胜率表现】: {win_rate:.2f}% ({len(wins)} 赢 / {len(losses)} 亏)")
        
        avg_rr_win = np.mean([t['rr'] for t in wins]) if wins else 0
        avg_rr_loss = np.mean([t['rr'] for t in losses]) if losses else 0
        
        print(f"💰 【净盈亏比(R:R)】:")
        print(f"     ✅ 获利单平均 R = +{avg_rr_win:.2f}R")
        print(f"     ❌ 亏损单平均 R = {avg_rr_loss:.2f}R")
        print(f"     预期收益(EV) = ({win_rate:.1f}% x {avg_rr_win:.2f}) + ({(100-win_rate):.1f}% x {avg_rr_loss:.2f}) = {(win_rate/100*avg_rr_win + (1-win_rate/100)*avg_rr_loss):.2f} R/单")
        
        print("\n" + "="*50)
        print("🎯 概率切割矩阵 (EV Probability Matrix)")
        print("="*50)
        
        # 提取结案交易进行特征分析
        completed_list = [t for t in all_trades if t['status'] in ['WIN', 'LOSS']]
        df_res = pd.DataFrame(completed_list)
        df_res['is_win'] = df_res['status'] == 'WIN'
        
        # 1. Signal Quality 质量分级
        df_res['sig_tier'] = pd.cut(df_res['sig_quality'], bins=[-np.inf, 0.5, 0.8, 0.95, np.inf], labels=['差(<0.5)', '中(0.5-0.8)', '良(0.8-0.95)', '优(>0.95)'])
        print("【维度 1】: 基于信号 K 线大阳线质量 (Signal Bar Quality)")
        sig_stats = df_res.groupby('sig_tier')['is_win'].agg(['count', 'mean'])
        for idx, row in sig_stats.iterrows():
            if row['count'] > 0:
                print(f"  - {idx}: 样本数 {int(row['count']):>4d} -> 历史打靶概率: {row['mean']*100:.2f}%")
                
        # 2. 连续阴线分级
        df_res['bear_tier'] = pd.cut(df_res['pb_consec_bear'], bins=[-np.inf, 0, 1, 2, np.inf], labels=['0连阴(极强)', '1连阴(正常)', '2连阴(弱)', '>=3连阴(极差)'])
        print("\n【维度 2】: 基于回调期连续阴线数量 (Consecutive Bear Bars)")
        bear_stats = df_res.groupby('bear_tier')['is_win'].agg(['count', 'mean'])
        for idx, row in bear_stats.iterrows():
            if row['count'] > 0:
                print(f"  - {idx}: 样本数 {int(row['count']):>4d} -> 历史打靶概率: {row['mean']*100:.2f}%")

        # 3. 极品组合打分 (Quality > 0.8 & 连阴 < 2)
        best_mask = (df_res['sig_quality'] > 0.8) & (df_res['pb_consec_bear'] < 2)
        worst_mask = (df_res['sig_quality'] <= 0.5) & (df_res['pb_consec_bear'] >= 2)
        
        best_win = df_res[best_mask]['is_win'].mean() * 100 if len(df_res[best_mask]) > 0 else 0
        worst_win = df_res[worst_mask]['is_win'].mean() * 100 if len(df_res[worst_mask]) > 0 else 0
        
        print(f"\n💡 【实战动态 EV 建议】:")
        print(f"  🌟 极品组合 (质量>0.8 且 连阴<2): 样本 {len(df_res[best_mask])} -> 胜率 {best_win:.2f}% (可博弈1~1.5倍MM)")
        print(f"  ⚠️ 毒性组合 (质量<=0.5 且 连阴>=2): 样本 {len(df_res[worst_mask])} -> 胜率 {worst_win:.2f}% (建议打到0.5倍MM或前高就跑)")

    else:
        print("无足够结案交易统计。")
    
    print("="*50)
    return all_trades

if __name__ == "__main__":
    # Test setting: check all stocks back 6 years
    run_batch_backtest(limit=0, bars_limit=1500)

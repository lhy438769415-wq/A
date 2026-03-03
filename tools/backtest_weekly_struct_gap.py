import os
import sys
import pandas as pd
import numpy as np
import logging
from concurrent.futures import ProcessPoolExecutor, as_completed

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from core.data_provider import get_stock_data_weekly, get_stock_list
from core.calculator import add_indicators
from core.strategies.structural_gap_strategy import StructuralGapStrategy
from config import settings
from tools.backtest_struct_gap import evaluate_trade

logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def backtest_single_stock_weekly(code: str, bars_limit=1500) -> list:
    """ 回测单支股票并返回所有交易结果 """
    try:
        df = get_stock_data_weekly(code, limit=bars_limit)
        if df is None or len(df) < 100:
            return []
            
        df = add_indicators(df)
        strategy = StructuralGapStrategy()
        # Weekly bars often have longer pullbacks and wider gaps proportionately. 
        # But we'll test the raw daily engine logic against weekly blocks.
        df = strategy.calculate_signals(df)
        
        # 提取信号
        signal_indices = [i for i, val in enumerate(df['signal_struct_gap_confirm']) if val]
        
        results = []
        for idx in signal_indices:
            trade_res = evaluate_trade(df, idx)
            if trade_res['status'] in ['SKIP', 'FILTERED']:
                continue
                
            # 添加上下文元数据
            sig_date = df.iloc[idx]['trade_date'] if 'trade_date' in df.columns else df.index[idx]
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

def run_weekly_backtest(limit=500, bars_limit=1500):
    logger.info(f"🚀 初始化 Structural Gap (V3.0) 宏观战略回测引擎 [✨周线级别 Weekly✨] ...")
    all_codes = get_stock_list()
    if not all_codes:
        logger.error("无法获取股票列表")
        return []
        
    if limit > 0:
        all_codes = all_codes[:limit]
        
    logger.info(f"📊 准备扫描 {len(all_codes)} 只标的周线...")
    
    all_trades = []
    
    with ProcessPoolExecutor(max_workers=settings.MAX_WORKERS) as executor:
        futures = {executor.submit(backtest_single_stock_weekly, code, bars_limit): code for code in all_codes}
        
        completed = 0
        for future in as_completed(futures):
            completed += 1
            if completed % 100 == 0:
                print(f"⌛ 进度: {completed} / {len(all_codes)}", end='\r')
                
            trades = future.result()
            if trades:
                all_trades.extend(trades)
                
    print(f"\n✅ 扫描完毕。全市场(周线)累计剥离出形态信号: {len(all_trades)} 笔。")
    return all_trades

if __name__ == '__main__':
    trades = run_weekly_backtest(limit=0, bars_limit=1000)
    
    completed = [t for t in trades if t['status'] in ['WIN', 'LOSS']]

    if not completed:
        print("No completed weekly trades found!")
        exit()

    df = pd.DataFrame(completed)
    df['is_win'] = df['status'] == 'WIN'
    
    print("\n" + "="*50)
    print("🌍 跨维打击：周线级 (Weekly) EV Probability Matrix")
    print("="*50)

    win_rate = df['is_win'].mean() * 100
    avg_rr_win = np.mean([t['rr'] for t in completed if t['status']=='WIN']) if any(t['status']=='WIN' for t in completed) else 0
    avg_rr_loss = np.mean([t['rr'] for t in completed if t['status']=='LOSS']) if any(t['status']=='LOSS' for t in completed) else 0
    
    print(f"🏆 【全局周线胜率】: {win_rate:.2f}% ({len(df[df['is_win']])} 赢 / {len(df[~df['is_win']])} 亏)")
    print(f"     ✅ 获利单平均 R = +{avg_rr_win:.2f}R")
    print(f"     ❌ 亏损单平均 R = {avg_rr_loss:.2f}R")
    print(f"     期望收益(EV) = {(win_rate/100*avg_rr_win + (1-win_rate/100)*avg_rr_loss):.2f} R/单")

    df['sig_tier'] = pd.cut(df['sig_quality'], bins=[-99, 0.5, 0.8, 0.95, 99], labels=['Poor(<0.5)', 'Mid(0.5-0.8)', 'Good(0.8-0.95)', 'Excellent(>0.95)'])
    print("\n【周线维度 1】: 基于信号 K 线大阳线质量 (Signal Bar Quality)")
    print(df.groupby('sig_tier')['is_win'].agg(['count', 'mean']))

    df['bear_tier'] = pd.cut(df['pb_consec_bear'], bins=[-99, 0, 1, 2, 99], labels=['0_Bears', '1_Bears', '2_Bears', '3+_Bears'])
    print("\n【周线维度 2】: 基于回调期连续阴线数量 (Consecutive Bear Bars)")
    print(df.groupby('bear_tier')['is_win'].agg(['count', 'mean']))

    best_mask = (df['sig_quality'] > 0.8) & (df['pb_consec_bear'] < 2)
    worst_mask = (df['sig_quality'] <= 0.5) & (df['pb_consec_bear'] >= 2)
    print(f"\n💡 【实战动态 EV 建议 (周线)】:")
    print(f"  🌟 极品组合 (质量>0.8 且 连阴<2): 样本 {len(df[best_mask])} -> 胜率 {df[best_mask]['is_win'].mean()*100:.2f}%")
    print(f"  ⚠️ 毒性组合 (质量<=0.5 且 连阴>=2): 样本 {len(df[worst_mask])} -> 胜率 {df[worst_mask]['is_win'].mean()*100:.2f}%")

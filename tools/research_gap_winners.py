# -*- coding: utf-8 -*-
"""
[Research] 周线 Structural Gap 逆向工程：成功达标的 MM 到底长什么样？

纯离线分析，只读本地 baostock.db，零网络请求。
逻辑：
  1. 遍历全市场所有股票的周线数据
  2. 使用 StructuralGapStrategy.calculate_signals() 找出所有历史信号
  3. 对每个信号，向前追踪：是先触 TP (MM Target) 还是先触 SL (Gap Floor)
  4. 提取胜负案例的多维度特征并进行统计分析
"""
import sys, os, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
import logging
from collections import defaultdict

from core.calculator import add_indicators
from core.strategies.structural_gap_strategy import StructuralGapStrategy
import core.data_provider as dp

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

def run_reverse_analysis():
    """主分析逻辑"""
    strategy = StructuralGapStrategy()
    all_codes = dp.get_stock_list()
    if not all_codes:
        print("❌ 获取股票列表失败"); return
    
    print(f"\n🔬 逆向工程启动: 分析 {len(all_codes)} 只股票的周线历史 Structural Gap...")
    print("=" * 80)
    
    # 收集所有信号的特征和结果
    records = []
    total_signals = 0
    errors = 0
    
    for i, code in enumerate(all_codes):
        if (i + 1) % 100 == 0:
            sys.stdout.write(f"\r  ⏳ 进度: {i+1}/{len(all_codes)} | 已发现信号: {total_signals}")
            sys.stdout.flush()
        
        try:
            df = dp.get_stock_data_weekly(code, limit=None)  # 全量历史
            if df is None or len(df) < 120:
                continue
            
            df = add_indicators(df)
            df = strategy.calculate_signals(df)
            
            # 找到所有信号点
            signals = df[df['signal_struct_gap_confirm'] == True]
            if signals.empty:
                continue
            
            for sig_date, sig_row in signals.iterrows():
                entry = sig_row.get('entry_struct_gap', np.nan)
                sl = sig_row.get('sl_struct_gap', np.nan)
                tp = sig_row.get('tp_struct_gap', np.nan)
                
                if np.isnan(entry) or np.isnan(sl) or np.isnan(tp):
                    continue
                if entry <= sl or tp <= entry:
                    continue
                
                # === 向前追踪 ===
                sig_idx = df.index.get_loc(sig_date)
                future = df.iloc[sig_idx + 1:]  # 信号后的所有K线
                
                if len(future) < 2:
                    continue  # 数据不足以判断
                
                outcome = 'TIMEOUT'
                bars_to_outcome = len(future)
                exit_price = future.iloc[-1]['close']
                
                for j, (fdate, frow) in enumerate(future.iterrows()):
                    if frow['high'] >= tp:
                        outcome = 'WIN'
                        bars_to_outcome = j + 1
                        exit_price = tp
                        break
                    if frow['low'] <= sl:
                        outcome = 'LOSE'
                        bars_to_outcome = j + 1
                        exit_price = sl
                        break
                
                # === 特征提取 ===
                # 1. 信号K线质量
                sig_quality = sig_row.get('sig_bar_quality', 0)
                if pd.isna(sig_quality): sig_quality = 0
                
                # 2. 回调连阴数 (从突破到信号)
                pb_bars_count = sig_row.get('bars_since_breakout', 0)
                if pd.isna(pb_bars_count): pb_bars_count = 0
                pb_bars_count = int(pb_bars_count)
                
                pb_consec_bear = 0
                if pb_bars_count > 0 and sig_idx >= pb_bars_count:
                    pb_df = df.iloc[sig_idx - pb_bars_count : sig_idx]
                    if len(pb_df) > 0:
                        is_bear = pb_df['close'] < pb_df['open']
                        shifts = is_bear != is_bear.shift()
                        groups = shifts.cumsum()
                        bear_groups = is_bear.groupby(groups).sum()
                        pb_consec_bear = int(bear_groups.max()) if not bear_groups.empty else 0
                
                # 3. 缺口宽度 / ATR
                gap_floor = sl
                gap_top = sig_row.get('struct_gap_top_exact', np.nan)
                if pd.isna(gap_top): gap_top = entry
                gap_size_pct = (gap_top - gap_floor) / gap_floor * 100 if gap_floor > 0 else 0
                
                atr = sig_row.get('atr', np.nan)
                gap_atr_ratio = (gap_top - gap_floor) / atr if not pd.isna(atr) and atr > 0 else 0
                
                # 4. 回调深度 (从突破低点到信号期间最低低点)
                prior_low = sig_row.get('struct_gap_prior_low', np.nan)
                retracement_depth = 0
                if not pd.isna(prior_low) and gap_top > gap_floor:
                    # 回调百分比: 从 gap_top 下来了多少
                    min_low_in_pb = df.iloc[max(0, sig_idx - pb_bars_count):sig_idx + 1]['low'].min()
                    retracement_depth = (gap_top - min_low_in_pb) / (gap_top - gap_floor) if (gap_top - gap_floor) > 0 else 0
                
                # 5. 回调K线重叠度
                overlap_pct = 0
                if pb_bars_count > 1 and sig_idx >= pb_bars_count:
                    pb_df = df.iloc[sig_idx - pb_bars_count : sig_idx]
                    if len(pb_df) > 1:
                        overlaps = (pb_df['high'].iloc[1:].values > pb_df['close'].iloc[:-1].values).sum()
                        overlap_pct = overlaps / (len(pb_df) - 1)
                
                # 6. 价格距EMA20
                ema20 = sig_row.get('ema20', np.nan)
                price_vs_ema = (sig_row['close'] - ema20) / ema20 * 100 if not pd.isna(ema20) and ema20 > 0 else 0
                
                # 7. R:R 比率
                risk = entry - sl
                reward = tp - entry
                rr_ratio = reward / risk if risk > 0 else 0
                
                # 8. 突破前趋势深度 (从prior_low到gap_floor的涨幅)
                trend_depth = (gap_floor - prior_low) / prior_low * 100 if not pd.isna(prior_low) and prior_low > 0 else 0
                
                # 9. 阳线实体占比 (回调期内, 代表多头在回调中的抵抗力)
                bull_ratio = 0
                if pb_bars_count > 0 and sig_idx >= pb_bars_count:
                    pb_df = df.iloc[sig_idx - pb_bars_count : sig_idx]
                    if len(pb_df) > 0:
                        bull_ratio = (pb_df['close'] > pb_df['open']).sum() / len(pb_df)
                
                # 10. 当前 EV 评级 (重新计算，验证一致性)
                if sig_quality > 0.8 and pb_consec_bear < 2:
                    ev_rating = 'HIGH'
                elif sig_quality <= 0.5 and pb_consec_bear >= 2:
                    ev_rating = 'LOW'
                else:
                    ev_rating = 'NORMAL'
                
                records.append({
                    'code': code,
                    'sig_date': str(sig_date),
                    'outcome': outcome,
                    'bars_to_outcome': bars_to_outcome,
                    'entry': entry,
                    'sl': sl,
                    'tp': tp,
                    'exit_price': exit_price,
                    'rr_ratio': round(rr_ratio, 2),
                    'sig_quality': round(sig_quality, 3),
                    'pb_consec_bear': pb_consec_bear,
                    'pb_bars_count': pb_bars_count,
                    'gap_size_pct': round(gap_size_pct, 2),
                    'gap_atr_ratio': round(gap_atr_ratio, 2),
                    'retracement_depth': round(retracement_depth, 3),
                    'overlap_pct': round(overlap_pct, 3),
                    'price_vs_ema': round(price_vs_ema, 2),
                    'trend_depth': round(trend_depth, 2),
                    'bull_ratio': round(bull_ratio, 3),
                    'ev_rating': ev_rating,
                })
                total_signals += 1
                
        except Exception as e:
            errors += 1
            continue
    
    print(f"\n\n✅ 扫描完成! 总计发现 {total_signals} 个历史信号 (跳过 {errors} 个异常)")
    print("=" * 80)
    
    if not records:
        print("❌ 没有发现任何可分析的信号。"); return
    
    rdf = pd.DataFrame(records)
    
    # =====================================================================
    # 统计分析
    # =====================================================================
    print("\n" + "=" * 80)
    print("  📊 第一部分：总体胜率基线")
    print("=" * 80)
    
    total = len(rdf)
    wins = (rdf['outcome'] == 'WIN').sum()
    losses = (rdf['outcome'] == 'LOSE').sum()
    timeouts = (rdf['outcome'] == 'TIMEOUT').sum()
    
    # 只看闭合的交易 (排除 TIMEOUT)
    closed = rdf[rdf['outcome'] != 'TIMEOUT']
    win_rate_closed = wins / len(closed) * 100 if len(closed) > 0 else 0
    
    print(f"  总信号数: {total}")
    print(f"  赢 (触TP): {wins} ({wins/total*100:.1f}%)")
    print(f"  亏 (触SL): {losses} ({losses/total*100:.1f}%)")
    print(f"  未决 (TIMEOUT): {timeouts} ({timeouts/total*100:.1f}%)")
    print(f"  闭合胜率 (WIN/CLOSED): {win_rate_closed:.1f}%")
    print(f"  平均达标周数 (WIN): {rdf[rdf['outcome']=='WIN']['bars_to_outcome'].mean():.1f}")
    print(f"  平均止损周数 (LOSE): {rdf[rdf['outcome']=='LOSE']['bars_to_outcome'].mean():.1f}")
    
    # =====================================================================
    print("\n" + "=" * 80)
    print("  📊 第二部分：现有 EV 评级系统验证")
    print("=" * 80)
    
    for rating in ['HIGH', 'NORMAL', 'LOW']:
        subset = closed[closed['ev_rating'] == rating]
        if len(subset) > 0:
            wr = (subset['outcome'] == 'WIN').sum() / len(subset) * 100
            avg_bars = subset[subset['outcome'] == 'WIN']['bars_to_outcome'].mean() if (subset['outcome'] == 'WIN').sum() > 0 else 0
            print(f"  [{rating:>6s}] 样本: {len(subset):>4d} | 胜率: {wr:>5.1f}% | 赢平均周数: {avg_bars:.1f}")
    
    # =====================================================================
    print("\n" + "=" * 80)
    print("  📊 第三部分：单因子分析 (等频分箱)")
    print("=" * 80)
    
    def analyze_factor(df, col, name, n_bins=4):
        """对连续变量进行等频分箱分析"""
        working = df[df['outcome'] != 'TIMEOUT'].copy()
        if len(working) < 20: return
        
        try:
            working[f'{col}_bin'] = pd.qcut(working[col], q=n_bins, duplicates='drop')
        except:
            return
        
        print(f"\n  --- {name} ({col}) ---")
        for bin_label, group in working.groupby(f'{col}_bin', observed=True):
            w = (group['outcome'] == 'WIN').sum()
            t = len(group)
            wr = w / t * 100 if t > 0 else 0
            print(f"    {str(bin_label):>25s}: 胜率 {wr:5.1f}% | 样本 {t:>4d}")
    
    analyze_factor(rdf, 'sig_quality', '信号K线质量')
    analyze_factor(rdf, 'pb_consec_bear', '回调最大连阴数')
    analyze_factor(rdf, 'gap_size_pct', '缺口宽度 (%)')
    analyze_factor(rdf, 'retracement_depth', '回调深度')
    analyze_factor(rdf, 'overlap_pct', 'K线重叠度')
    analyze_factor(rdf, 'rr_ratio', '盈亏比 (R:R)')
    analyze_factor(rdf, 'trend_depth', '突破前趋势深度 (%)')
    analyze_factor(rdf, 'bull_ratio', '回调期阳线占比')
    analyze_factor(rdf, 'price_vs_ema', '价格距EMA20 (%)')
    analyze_factor(rdf, 'pb_bars_count', '回调周期数')
    
    # =====================================================================
    print("\n" + "=" * 80)
    print("  📊 第四部分：胜负案例对比 (特征均值)")
    print("=" * 80)
    
    win_df = rdf[rdf['outcome'] == 'WIN']
    lose_df = rdf[rdf['outcome'] == 'LOSE']
    
    feature_cols = ['sig_quality', 'pb_consec_bear', 'gap_size_pct', 'retracement_depth',
                    'overlap_pct', 'rr_ratio', 'trend_depth', 'bull_ratio', 'price_vs_ema', 'pb_bars_count']
    
    print(f"  {'特征':>20s} | {'赢均值':>8s} | {'亏均值':>8s} | {'差异方向':>8s}")
    print(f"  {'-'*20}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}")
    for col in feature_cols:
        w_mean = win_df[col].mean() if len(win_df) > 0 else 0
        l_mean = lose_df[col].mean() if len(lose_df) > 0 else 0
        direction = "赢 ↑" if w_mean > l_mean else "亏 ↑"
        print(f"  {col:>20s} | {w_mean:>8.3f} | {l_mean:>8.3f} | {direction:>8s}")
    
    # =====================================================================
    print("\n" + "=" * 80)
    print("  📊 第五部分：组合因子探索 (极品 vs 毒性)")
    print("=" * 80)
    
    working = closed.copy()
    
    # 极品组合变体
    combos = [
        ("质量>0.8 & 连阴<2", (working['sig_quality'] > 0.8) & (working['pb_consec_bear'] < 2)),
        ("质量>0.9 & 连阴=0", (working['sig_quality'] > 0.9) & (working['pb_consec_bear'] == 0)),
        ("质量>0.8 & 重叠<0.5", (working['sig_quality'] > 0.8) & (working['overlap_pct'] < 0.5)),
        ("质量>0.8 & 阳线>0.5", (working['sig_quality'] > 0.8) & (working['bull_ratio'] > 0.5)),
        ("连阴=0 & 阳线>0.6", (working['pb_consec_bear'] == 0) & (working['bull_ratio'] > 0.6)),
        ("趋势深>100% & 质量>0.8", (working['trend_depth'] > 100) & (working['sig_quality'] > 0.8)),
        ("RR>3 & 质量>0.8", (working['rr_ratio'] > 3) & (working['sig_quality'] > 0.8)),
        ("质量<=0.5 & 连阴>=3", (working['sig_quality'] <= 0.5) & (working['pb_consec_bear'] >= 3)),
        ("重叠>0.8 & 阳线<0.3", (working['overlap_pct'] > 0.8) & (working['bull_ratio'] < 0.3)),
    ]
    
    for label, mask in combos:
        subset = working[mask]
        if len(subset) >= 5:
            wr = (subset['outcome'] == 'WIN').sum() / len(subset) * 100
            print(f"  [{label:>30s}] 样本: {len(subset):>4d} | 胜率: {wr:>5.1f}%")
        else:
            print(f"  [{label:>30s}] 样本不足 ({len(subset)})")
    
    # =====================================================================
    print("\n" + "=" * 80)
    print("  📊 第六部分：达标速度分析 (赢家花了多久)")
    print("=" * 80)
    
    if len(win_df) > 0:
        speed_bins = [(1, 2, "1-2周 (闪电)"), (3, 5, "3-5周 (标准)"), 
                      (6, 10, "6-10周 (耐心)"), (11, 999, "11+周 (超长)")]
        for lo, hi, label in speed_bins:
            subset = win_df[(win_df['bars_to_outcome'] >= lo) & (win_df['bars_to_outcome'] <= hi)]
            pct = len(subset) / len(win_df) * 100
            avg_q = subset['sig_quality'].mean() if len(subset) > 0 else 0
            print(f"  {label:>20s}: {len(subset):>4d} ({pct:5.1f}%) | 平均质量: {avg_q:.3f}")
    
    # =====================================================================
    # 保存原始数据到 CSV
    output_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
                               'strategy_lab', 'weekly_gap_reverse_engineering.csv')
    rdf.to_csv(output_path, index=False, encoding='utf-8-sig')
    print(f"\n  📁 原始数据已保存: {output_path}")
    print("=" * 80)
    print("  🎯 分析完毕！")


if __name__ == '__main__':
    run_reverse_analysis()

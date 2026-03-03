# tools/verify_3k_002046.py
"""
验证脚本: 对国机精工(sz.002046) 运行升级后的3K策略
验证要点:
  1. 2025-12-22 触发 signal_3k
  2. 2026-01-08 附近触发 signal_3k_gap_test (缺口测试确认)
  3. Buy Stop / SL / TP 数值正确
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
import logging
logging.basicConfig(level=logging.WARNING)

from core.data_provider import get_stock_data
from core.calculator import add_indicators
from core.strategies.three_k_strategy import ThreeKStrategy

def main():
    print("=" * 74)
    print("  3K策略完整验证: 国机精工 sz.002046")
    print("=" * 74)
    
    df = get_stock_data("sz.002046")
    if df is None or df.empty:
        print("❌ 数据库中未找到数据")
        return
    
    df = add_indicators(df)
    strategy = ThreeKStrategy()
    df = strategy.calculate_signals(df)
    
    # === 阶段1: 3K信号 ===
    mask = (df['date'] >= '2025-12-18') & (df['date'] <= '2026-01-15')
    t = df[mask].copy()
    
    print("\n📋 逐日数据 (2025-12-18 ~ 2026-01-15):")
    print("-" * 74)
    
    for _, r in t.iterrows():
        marks = []
        if r.get('signal_3k', False):
            marks.append('★ 3K信号')
        if r.get('signal_3k_gap_test', False):
            marks.append('★★ 缺口测试确认!')
        
        bull = '阳' if r['is_bullish'] else '阴'
        gap_o = '缺口开放' if r.get('breakout_gap_open', False) else ''
        mark_str = ' | '.join(marks) if marks else ''
        
        line = (f"  {r['date']} O={r['open']:6.2f} H={r['high']:6.2f} "
                f"L={r['low']:6.2f} C={r['close']:6.2f} [{bull}] "
                f"{gap_o:6s} {mark_str}")
        print(line)
    
    # === 阶段1结果 ===
    print("\n" + "=" * 74)
    sig_3k = t[t['signal_3k'] == True]
    print(f"🎯 3K信号: {len(sig_3k)} 个")
    for _, r in sig_3k.iterrows():
        k1_h = df.loc[df[df['date'] == r['date']].index[0] - 2, 'high']
        print(f"   {r['date']} | Close={r['close']:.2f} | SL={r['sl_3k']:.2f} | K1_High={k1_h:.2f}")
    
    # === 阶段2结果 ===
    gap_tests = t[t.get('signal_3k_gap_test', pd.Series(dtype=bool)) == True]
    print(f"\n🎯 缺口测试确认信号: {len(gap_tests)} 个")
    for _, r in gap_tests.iterrows():
        entry = r.get('entry_3k_gap_test', np.nan)
        sl = r.get('sl_3k_gap_test', np.nan)
        tp = r.get('tp_3k_gap_test', np.nan)
        print(f"   {r['date']} | Buy Stop={entry:.2f} | SL={sl:.2f} | TP={tp if not np.isnan(tp) else 'N/A'}")
        
        # 计算盈亏比
        if not np.isnan(entry) and not np.isnan(sl):
            risk = entry - sl
            if not np.isnan(tp) and risk > 0:
                reward = tp - entry
                rr = reward / risk
                print(f"   Risk={risk:.2f} | Reward={reward:.2f} | R:R = 1:{rr:.1f}")
    
    if len(gap_tests) == 0:
        print("   ⚠️ 未检测到缺口测试确认信号！需要排查。")
        # 诊断
        print("\n🔬 诊断:")
        cols = ['signal_3k_gap_test', 'breakout_gap_open', 'entry_3k_gap_test']
        avail = [c for c in cols if c in t.columns]
        if avail:
            print(f"   列存在: {avail}")
        else:
            print("   ❌ signal_3k_gap_test 列不存在!")

if __name__ == '__main__':
    main()

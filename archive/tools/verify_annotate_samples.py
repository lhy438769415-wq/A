# archive/tools/verify_annotate_samples.py
"""
[核查] 用户对两张样本图的质疑:
  1) A止盈样本 (sh.603296): 「起跳支点」标注位置是否错误?
  2) 某新出场止盈样本 (入场2.43/SL2.27/TP2.74): 用户指的K线是否为回调的高1(H1)?

方法: 复现样本数据 → 重跑 GAP H2 状态机 → 打印关键锚点日期/价格/窗口内x坐标。
只读数据, 不写库不改生产代码。
"""
import os
import sys
import numpy as np
import pandas as pd


def _find_project_root(start: str) -> str:
    p = os.path.abspath(start)
    while p and p != os.path.dirname(p):
        if os.path.isdir(os.path.join(p, 'core')) and os.path.isfile(os.path.join(p, 'core', 'paths.py')):
            return p
        p = os.path.dirname(p)
    return p


project_root = _find_project_root(__file__)
if project_root not in sys.path:
    sys.path.insert(0, project_root)
from core.paths import ensure_importable
ensure_importable()

from core.data_provider import get_stock_data
from core.calculator import add_indicators
from core.strategies.gap_h2_strategy import GapH2Strategy


def dump_state_machine(code, tag):
    print(f"\n{'='*70}\n[{tag}] {code}\n{'='*70}")
    df = get_stock_data(code, limit=None)
    if df is None or len(df) < 200:
        print("[!] 数据不足")
        return
    if 'date' not in df.columns and 'trade_date' in df.columns:
        df['date'] = df['trade_date']
    df = add_indicators(df)
    strat = GapH2Strategy()
    sdf = strat.calculate_signals(df.copy())
    sigs = sdf.index[sdf['signal_gap_h2']]
    if len(sigs) == 0:
        print("[!] 无信号")
        return
    # 取最后一个信号 (与样本图对应)
    sig_i = sigs[-1]
    row = sdf.loc[sig_i]
    print(f"信号K: {str(row['date'])[:10]}  entry(信号K高点)={row['entry_gap_h2']:.2f}  "
          f"SL(地板)={row['sl_gap_h2']:.2f}  TP(测量目标)={row['tp_gap_h2']:.2f}")
    print(f"prior_low(前60根最低低, TP锚点)={row['gap_h2_prior_low']:.2f}  "
          f"top_exact(回调期最低,绘图用)={row.get('gap_h2_top_exact', float('nan'))}")

    # 找该信号所属的突破组
    bo = sdf['is_breakout_h2'].cumsum()
    grp = bo.loc[sig_i]

    # 起跳支点标注的实际算法 (structural_gap_strategy._annotate_gap_strategy):
    #   prior_low 取信号K行的 gap_h2_prior_low; origin_date = 信号前 low==prior_low 的第一天
    pre = sdf.loc[:sig_i]
    abs_diff = (pre['low'] - row['gap_h2_prior_low']).abs()
    origin_i = abs_diff.idxmin() if abs_diff.min() < 1e-4 else pre.index[0]
    print(f"\n-- 图上「起跳支点」箭头实际指的K线 (现行算法) --")
    print(f"   {str(sdf.loc[origin_i,'date'])[:10]}  low={sdf.loc[origin_i,'low']:.2f}  "
          f"(距信号K {sig_i - origin_i} 根)")
    # 距离信号K最近的一次触及 prior_low 的日期
    touch = pre[abs_diff < 1e-4]
    if len(touch) > 1:
        print(f"   [注意] 窗口内触及该价位的K线共 {len(touch)} 根, 箭头取第一根 "
              f"{str(sdf.loc[origin_i,'date'])[:10]}, 最后一根 {str(sdf.loc[touch.index[-1],'date'])[:10]}")

    # 状态机时间线: 该突破组内的关键事件
    g = sdf[bo == grp]
    lhll = (g['high'] < g['high'].shift(1)) & (g['low'] < g['low'].shift(1))
    hh = g['high'] > g['high'].shift(1)
    bo_date = str(g.iloc[0]['date'])[:10]
    print(f"\n-- 状态机时间线 (突破组 #{grp}) --")
    print(f"   突破K(HH+HL 且 low>地板): {bo_date}  low={g.iloc[0]['low']:.2f} high={g.iloc[0]['high']:.2f}")
    # 第一腿回调: 首根 LHLL
    lhll_after_bo = g.index[lhll]
    if len(lhll_after_bo):
        pb1_i = lhll_after_bo[0]
        print(f"   首腿回调首根 LHLL  : {str(sdf.loc[pb1_i,'date'])[:10]}  low={sdf.loc[pb1_i,'low']:.2f} (距突破 {pb1_i - g.index[0]} 根)")
        # 高1: 首腿回调后的首根 HH
        after_pb1 = g.loc[pb1_i:]
        hh1 = after_pb1.index[(after_pb1['high'] > after_pb1['high'].shift(1))
                              & (after_pb1['high'].shift(1) <= sdf.loc[pb1_i, 'high'] + 1e9)]
        # 严格口径: LHLL 之后第一根 high > 前一根 high 即 H1 候选, 但需其前一根处于回调中
        h1_i = None
        for ii in after_pb1.index[1:]:
            if ii <= pb1_i:
                continue
            cur = g.loc[ii]
            prev = g.loc[g.index[g.index.get_loc(ii) - 1]]
            if cur['high'] > prev['high']:
                h1_i = ii
                break
        if h1_i is not None:
            print(f"   高1 H1 (回调后首根HH): {str(sdf.loc[h1_i,'date'])[:10]}  high={sdf.loc[h1_i,'high']:.2f} "
                  f"(距首腿LHLL {h1_i - pb1_i} 根)")
        # 信号K = H1 后首根 LHLL
        print(f"   信号K (H1后首根LHLL): {str(row['date'])[:10]}  low={row['low']:.2f}")
    else:
        print("   [!] 该突破组内未检出 LHLL (异常)")

    # 图窗口坐标 (start = sig_pos-90)
    sig_pos = int(np.where(sdf.index == sig_i)[0][0])
    start = max(0, sig_pos - 90)
    win = sdf.iloc[start:sig_pos + 1]
    print(f"\n-- 图窗口 (start=信号前90根) 内的x坐标 (0起) --")
    for name, ii in [('起跳支点箭头', origin_i), ('突破K', g.index[0]),
                     ('首腿LHLL', pb1_i if len(lhll_after_bo) else None),
                     ('高1 H1', h1_i if (len(lhll_after_bo) and h1_i is not None) else None),
                     ('信号K', sig_i)]:
        if ii is not None and ii in win.index:
            print(f"   {name}: x={ii - start}  {str(sdf.loc[ii,'date'])[:10]}")
    return sdf, sig_i


if __name__ == '__main__':
    # 图1: A止盈样本 sh.603296
    dump_state_machine('sh.603296', '图1 A止盈样本')
    # 图2 候选: B止盈 sz.002471 / D止盈 sz.000931 (面板显示 entry 2.43 / SL 2.27 / TP 2.74=2.00R)
    for code in ('sz.002471', 'sz.000931'):
        df = get_stock_data(code, limit=None)
        if df is None:
            continue
        if 'date' not in df.columns and 'trade_date' in df.columns:
            df['date'] = df['trade_date']
        df = add_indicators(df)
        strat = GapH2Strategy()
        sdf = strat.calculate_signals(df.copy())
        sigs = sdf.index[sdf['signal_gap_h2']]
        if len(sigs):
            r = sdf.loc[sigs[-1]]
            print(f"\n[候选] {code} 最后信号 entry={r['entry_gap_h2']:.2f} sl={r['sl_gap_h2']:.2f} "
                  f"tp={r['tp_gap_h2']:.2f}  date={str(r['date'])[:10]}")

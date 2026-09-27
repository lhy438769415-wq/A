# archive/tools/backtest_gap_h2_compare.py
"""
[回测对比] Gap H2 原版 vs 增强版 (历史全量, 日线)

严格复用项目已有框架:
  - 交易模拟 evaluate_trade / 生命周期三过滤 / LIFECYCLE_TIMEOUT_BARS 直接 import 自
    backtest_gap_h2.py (口径与原历史回测完全一致, 保证公平对比)。
  - 数据接口 get_stock_data(limit=None) 取全历史; add_indicators 离线计算。

设计: 同一标的仅加载+算指标一次, 在进程内顺序跑 5 个配置, 复用 evaluate_trade,
      最后汇总专业量化指标对比表。

配置 (隔离每一处改动对原版的影响):
  C0 原版        : 真缺口 / 无重置 / 地板止损 / 窗口40-2   (= STRATEGY_GAP_H2 生产逻辑, 基线)
  C1 实体缺口    : 实体缺口 / 无重置 / 地板止损 / 窗口40-2  (隔离: 缺口定义)
  C2 +新高重置   : 实体缺口 / 有重置 / 地板止损 / 窗口40-2  (隔离: 新高重置)
  C3 +窗口20-3   : 实体缺口 / 有重置 / 地板止损 / 窗口20-3  (隔离: 回调窗口)
  C4 完整增强    : 实体缺口 / 有重置 / 回调低点止损 / 窗口20-3 (= 增强策略卡最终设计)

输出: docs/gap_h2_enhanced_backtest_report.md (专业量化结论表 + 逐年/按回调周期分布 + 结论)
"""
import os
import sys
import time
import json
import warnings
import logging
import numpy as np
import pandas as pd
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed

warnings.filterwarnings('ignore')


def _find_project_root(start: str) -> str:
    """向上搜索含 core/paths.py 的目录作为项目根 (稳健, 不依赖目录层级假设)。"""
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

# —— 严格复用现有框架的交易模拟核心 ——
from backtest_gap_h2 import evaluate_trade, LIFECYCLE_TIMEOUT_BARS
from core.data_provider import get_stock_data, get_stock_list
from core.calculator import add_indicators
from config import settings
from core.log_config import get_logger
from core.strategies.gap_h2_strategy import GapH2Strategy
from core.strategies.gap_h2_enhanced_strategy import GapH2EnhancedStrategy

logger = get_logger(__name__)

# 配置表 (kind: 'orig' 用原版类; 'enh' 用增强类 + params)
CONFIGS = [
    {'label': 'C0 原版', 'kind': 'orig', 'params': {},
     'desc': '真缺口/无重置/地板止损/窗口40-2 (生产基线)'},
    {'label': 'C0+重置(纯)', 'kind': 'enh', 'params': dict(gap_mode='true', reset_on_newhigh=True, sl_mode='floor', max_pb=40, min_pb=2),
     'desc': '真缺口/有重置/地板止损/窗口40-2 (仅加新高重置, 其余=C0)'},
    {'label': 'C1 实体缺口', 'kind': 'enh', 'params': dict(gap_mode='body', reset_on_newhigh=False, sl_mode='floor', max_pb=40, min_pb=2),
     'desc': '缺口定义: 真缺口→实体缺口'},
    {'label': 'C2 +新高重置', 'kind': 'enh', 'params': dict(gap_mode='body', reset_on_newhigh=True, sl_mode='floor', max_pb=40, min_pb=2),
     'desc': '+ 突破后回调期创新高重置H1/H2计数器'},
    {'label': 'C3 +窗口20-3', 'kind': 'enh', 'params': dict(gap_mode='body', reset_on_newhigh=True, sl_mode='floor', max_pb=20, min_pb=3),
     'desc': '+ 回调窗口 40-2→20-3 (仍地板止损)'},
    {'label': 'C4 完整增强', 'kind': 'enh', 'params': dict(gap_mode='body', reset_on_newhigh=True, sl_mode='pullback', max_pb=20, min_pb=3),
     'desc': '+ 回调低点止损 (增强策略卡最终设计)'},
]


def _make_strategy(cfg):
    if cfg['kind'] == 'orig':
        return GapH2Strategy()
    return GapH2EnhancedStrategy(**cfg['params'])


def _worker(code, configs, limit):
    try:
        _lim = None if limit <= 0 else limit
        df = get_stock_data(code, limit=_lim)  # limit<=0 => 全历史; 否则只看最近 limit 根
        if df is None or len(df) < 100:
            return []
        if 'date' not in df.columns and 'trade_date' in df.columns:
            df['date'] = df['trade_date']
        df = add_indicators(df)
        out = []
        for cfg in configs:
            strat = _make_strategy(cfg)
            sdf = strat.calculate_signals(df.copy())
            sig_col = 'signal_gap_h2'
            indices = [i for i, v in enumerate(sdf[sig_col].fillna(False).values) if v]
            for idx in indices:
                t = evaluate_trade(sdf, idx)
                t['code'] = code
                t['config'] = cfg['label']
                sd = sdf.iloc[idx]['date'] if 'date' in sdf.columns else str(sdf.index[idx])
                t['signal_date'] = str(sd)[:10]
                if t['status'] in ('WIN', 'LOSS'):
                    ep = t.get('exit_price', np.nan)
                    en = t.get('entry_price', np.nan)
                    sl = sdf.iloc[idx]['sl_gap_h2']
                    risk = en - sl if (pd.notna(en) and pd.notna(sl)) else np.nan
                    t['rr'] = (ep - en) / risk if (pd.notna(risk) and risk > 0) else 0.0
                out.append(t)
        return out
    except Exception as e:
        logger.error(f"worker {code} error: {e}")
        return []


def run(limit=0):
    codes = get_stock_list()
    if not codes:
        print("[!] 无法获取股票列表")
        return
    if limit > 0:
        codes = codes[:limit]
    n = len(codes)
    print(f"[*] 扫描 {n} 只标的 (全历史日线)")
    print(f"[*] 复用框架: evaluate_trade / 生命周期三过滤 (撤单/作废/超时) 与原回测一致")
    print(f"[*] 配置: {', '.join(c['label'] for c in CONFIGS)}")

    t0 = time.time()
    all_trades = []
    with ProcessPoolExecutor(max_workers=getattr(settings, 'MAX_WORKERS', 4)) as exe:
        futs = {exe.submit(_worker, c, CONFIGS, limit): c for c in codes}
        done = 0
        for f in as_completed(futs):
            done += 1
            if done % 500 == 0:
                print(f"  [{done}/{n}]", flush=True)
            r = f.result()
            if r:
                all_trades.extend(r)
    print(f"[*] 回测完成, 耗时 {time.time()-t0:.1f}s, 总交易记录 {len(all_trades)} 条")
    return all_trades


# =====================================================================
# 专业量化指标汇总
# =====================================================================
def _metrics(trades):
    total = len(trades)
    wins = [t for t in trades if t['status'] == 'WIN']
    losses = [t for t in trades if t['status'] == 'LOSS']
    inv = [t for t in trades if t['status'] == 'INVALIDATED']
    void = [t for t in trades if t['status'] == 'VOIDED']
    tout = [t for t in trades if t['status'] == 'TIMEOUT']
    hold = [t for t in trades if t['status'] in ('HOLDING', 'PENDING')]
    done = len(wins) + len(losses)
    m = {'total': total, 'win': len(wins), 'loss': len(losses), 'invalid': len(inv),
         'void': len(void), 'timeout': len(tout), 'hold': len(hold), 'done': done}
    if done > 0:
        wr = len(wins) / done
        avg_w = float(np.mean([t['rr'] for t in wins])) if wins else 0.0
        avg_l = float(np.mean([t['rr'] for t in losses])) if losses else 0.0
        ev = wr * avg_w + (1 - wr) * avg_l
        sum_w = sum(t['rr'] for t in wins)
        sum_l = sum(t['rr'] for t in losses)
        pf = (sum_w / abs(sum_l)) if sum_l != 0 else float('inf')
        total_r = sum_w + sum_l
        # 权益曲线 (按出场日排序, 每笔以 1R 风险计)
        closed = [t for t in trades if t['status'] in ('WIN', 'LOSS') and 'rr' in t]
        closed.sort(key=lambda t: t.get('exit_date') or t.get('signal_date') or '')
        eq = 0.0
        peak = 0.0
        mdd = 0.0
        max_loss_streak = 0
        max_win_streak = 0
        cur_loss = 0
        cur_win = 0
        max_single_win = 0.0
        max_single_loss = 0.0
        for t in closed:
            rr = t['rr']
            eq += rr
            peak = max(peak, eq)
            mdd = min(mdd, eq - peak)
            if rr >= 0:
                cur_win += 1
                cur_loss = 0
                max_win_streak = max(max_win_streak, cur_win)
            else:
                cur_loss += 1
                cur_win = 0
                max_loss_streak = max(max_loss_streak, cur_loss)
            max_single_win = max(max_single_win, rr)
            max_single_loss = min(max_single_loss, rr)
        m.update({'wr': wr, 'avg_w': avg_w, 'avg_l': avg_l, 'ev': ev, 'pf': pf,
                   'total_r': total_r, 'mdd': mdd, 'max_loss_streak': max_loss_streak,
                   'max_win_streak': max_win_streak, 'max_win_r': max_single_win,
                   'max_loss_r': max_single_loss})
    else:
        m.update({'wr': 0, 'avg_w': 0, 'avg_l': 0, 'ev': 0, 'pf': 0, 'total_r': 0,
                   'mdd': 0, 'max_loss_streak': 0, 'max_win_streak': 0,
                   'max_win_r': 0, 'max_loss_r': 0})
    return m


def analyze(trades):
    rows = {c['label']: [t for t in trades if t['config'] == c['label']] for c in CONFIGS}
    metrics = {label: _metrics(ts) for label, ts in rows.items()}
    # 日期范围
    for label, ts in rows.items():
        dates = [t.get('signal_date', '') for t in ts if t.get('signal_date')]
        metrics[label]['date_min'] = min(dates) if dates else '-'
        metrics[label]['date_max'] = max(dates) if dates else '-'

    # ---------- 生成 Markdown 报告 ----------
    lines = []
    lines.append("# GAP H2 回测对比报告（原版 vs 增强版）\n")
    lines.append("> 数据：本地 `baostock.db` 日线（默认全历史；另以 limit=1500 与原装 `backtest_gap_h2.py` 交叉校验）  ")
    lines.append("> 框架：严格复用 `archive/tools/backtest_gap_h2.py` 的 `evaluate_trade` 与生命周期三过滤（撤单/作废/超时），回测口径与原历史回测一致。  ")
    lines.append("> 增强版策略 `STRATEGY_GAP_H2_ENHANCED` 已按项目正规方法注册（仅入注册表、不入日/周线生产扫描池）。\n")

    # 配置说明表
    lines.append("## 一、配置说明（逐项隔离每一处改动）\n")
    lines.append("| 配置 | 缺口定义 | 新高重置 | 止损 | 回调窗口 | 说明 |")
    lines.append("|------|---------|---------|------|---------|------|")
    lines.append("| C0 原版 | 真缺口 | 否 | 缺口地板 | 40/2 | 生产基线（STRATEGY_GAP_H2） |")
    lines.append("| **C0+重置(纯)** | 真缺口 | **是** | 缺口地板 | 40/2 | **仅加新高重置，其余=C0** |")
    lines.append("| C1 实体缺口 | 实体缺口 | 否 | 缺口地板 | 40/2 | 仅改缺口定义 |")
    lines.append("| C2 +新高重置 | 实体缺口 | **是** | 缺口地板 | 40/2 | 仅加新高重置 |")
    lines.append("| C3 +窗口20-3 | 实体缺口 | 是 | 缺口地板 | **20/3** | 仅改回调窗口 |")
    lines.append("| C4 完整增强 | 实体缺口 | 是 | **回调低点** | 20/3 | 增强策略卡最终设计 |\n")

    # 核心指标对比表
    lines.append("## 二、核心量化指标对比\n")
    header = ("| 配置 | 总信号 | 已结案 | 胜率 | 平均盈R | 平均亏R | 盈亏比 | "
              "单笔EV | 累计净R | 最大回撤 | 最大连亏 | 信号区间 |")
    sep = "|------|------|------|------|------|------|------|------|------|------|------|------|"
    lines.append(header)
    lines.append(sep)
    for c in CONFIGS:
        label = c['label']
        m = metrics[label]
        pf = m['pf']
        pf_s = f"{pf:.2f}" if pf != float('inf') else "∞"
        lines.append(
            f"| {label} | {m['total']} | {m['done']} | {m['wr']*100:.1f}% | "
            f"+{m['avg_w']:.3f} | {m['avg_l']:.3f} | {pf_s} | {m['ev']:+.4f} | "
            f"{m['total_r']:+.1f} | {m['mdd']:.2f} | {m['max_loss_streak']} | "
            f"{m['date_min']}~{m['date_max']} |"
        )
    lines.append("")

    # 信号生命周期分布
    lines.append("## 三、信号生命周期分布（每配置）\n")
    lines.append("| 配置 | 总信号 | 止盈WIN | 止损LOSS | 缺口回填撤单 | 止盈先达作废 | 超时 | 持仓/待触发 |")
    lines.append("|------|------|------|------|------|------|------|------|")
    for c in CONFIGS:
        m = metrics[c['label']]
        lines.append(
            f"| {c['label']} | {m['total']} | {m['win']} | {m['loss']} | {m['invalid']} | "
            f"{m['void']} | {m['timeout']} | {m['hold']} |"
        )
    lines.append("")

    # 逐年分布
    lines.append("## 四、逐年胜率与期望值\n")
    for c in CONFIGS:
        ts = rows[c['label']]
        closed = [t for t in ts if t['status'] in ('WIN', 'LOSS') and t.get('signal_date')]
        if not closed:
            continue
        dfr = pd.DataFrame(closed)
        dfr['year'] = dfr['signal_date'].str[:4]
        dfr['is_win'] = dfr['status'] == 'WIN'
        lines.append(f"### {c['label']}")
        lines.append("| 年份 | 样本 | 胜率 | 平均R |")
        lines.append("|------|------|------|------|")
        for yr, g in dfr.groupby('year'):
            w = g['is_win'].mean() * 100
            r = g['rr'].mean()
            lines.append(f"| {yr} | {len(g)} | {w:.1f}% | {r:+.3f} |")
        lines.append("")

    # 按回调周期
    lines.append("## 五、按回调周期分布（C0 / C4 对照）\n")
    for c in [CONFIGS[0], CONFIGS[-1]]:
        ts = rows[c['label']]
        closed = [t for t in ts if t['status'] in ('WIN', 'LOSS')]
        if not closed:
            continue
        dfr = pd.DataFrame(closed)
        if 'pb_bars' in dfr.columns:
            dfr['pb_tier'] = pd.cut(dfr['pb_bars'], bins=[-np.inf, 5, 10, 20, np.inf],
                                   labels=['<=5', '6-10', '11-20', '>20'])
            lines.append(f"### {c['label']}")
            lines.append("| 回调周期 | 样本 | 胜率 | 平均R |")
            lines.append("|------|------|------|------|")
            for tier, g in dfr.groupby('pb_tier', observed=True):
                if len(g) > 0:
                    lines.append(f"| {tier} | {len(g)} | {g['is_win' if 'is_win' in g else 'status'].map(lambda s: s=='WIN' if isinstance(s,str) else s).mean()*100:.1f}% | {g['rr'].mean():+.3f} |")
            lines.append("")

    # 结论
    m0 = metrics['C0 原版']
    m4 = metrics['C4 完整增强']
    lines.append("## 六、结论\n")
    lines.append(f"- **样本量**：C0 原版全历史 {m0['total']} 笔信号 / {m0['done']} 笔结案；"
                 f"C4 完整增强 {m4['total']} 笔信号 / {m4['done']} 笔结案。")
    lines.append(f"- **期望值 EV（单笔 R）**：C0 = {m0['ev']:+.4f}，C4 = {m4['ev']:+.4f}"
                 f"（{'增强版更优' if m4['ev']>m0['ev'] else '原版更优'}）。")
    lines.append(f"- **胜率**：C0 = {m0['wr']*100:.1f}%，C4 = {m4['wr']*100:.1f}%。")
    lines.append(f"- **盈亏比**：C0 = {m0['pf'] if m0['pf']!=float('inf') else '∞'}，"
                 f"C4 = {m4['pf'] if m4['pf']!=float('inf') else '∞'}。")
    lines.append(f"- **最大回撤（R）**：C0 = {m0['mdd']:.2f}，C4 = {m4['mdd']:.2f}。")
    lines.append("")
    lines.append("### C0 原版 vs C0+重置(纯)（问题1：仅加新高重置，其余与 C0 一致）")
    m0r = metrics['C0+重置(纯)']
    d_n = m0['total'] - m0r['total']
    lines.append(f"- 信号数：C0 = {m0['total']}，C0+重置 = {m0r['total']}（重置过滤 {d_n} 笔，"
                 f"占 C0 的 {d_n/m0['total']*100:.1f}%）")
    lines.append(f"- EV（单笔R）：C0 = {m0['ev']:+.4f}，C0+重置 = {m0r['ev']:+.4f}"
                 f"（{'新高重置更优' if m0r['ev']>m0['ev'] else '新高重置更差'}）")
    lines.append(f"- 胜率：C0 = {m0['wr']*100:.1f}%，C0+重置 = {m0r['wr']*100:.1f}%")
    lines.append(f"- 盈亏比：C0 = {m0['pf']:.2f}，C0+重置 = {m0r['pf']:.2f}")
    lines.append(f"- 平均盈R：C0 = +{m0['avg_w']:.3f}，C0+重置 = +{m0r['avg_w']:.3f}；"
                 f"平均亏R：C0 = {m0['avg_l']:.3f}，C0+重置 = {m0r['avg_l']:.3f}")
    lines.append("")
    lines.append("### 回测方法一致性校验（问题2）")
    lines.append("- 发现：项目原装 `archive/tools/backtest_gap_h2.py` 默认 `limit=1500`（仅看最近 1500 根日线），"
                 "而本对比框架默认 `limit=None`（全历史）。两者交易模拟 `evaluate_trade` 与生命周期三过滤逐字相同。")
    lines.append("- 校验方法：用本框架在 `limit=1500` 窗口重跑 C0，与直接运行原装工具的输出核对；"
                 "若两者一致，则证明本框架忠实复刻原方法，差异仅来自数据窗口（1500 vs 全历史）。"
                 "（具体数值见对话交付，因原装工具为独立进程，不在此报告内生成。）")
    lines.append("")
    lines.append("### 逐项改动贡献（对比相邻配置，增强路径 C0→C1→C2→C3→C4）")
    enh_path = [c for c in CONFIGS if c['label'] != 'C0+重置(纯)']
    prev = None
    for c in enh_path:
        m = metrics[c['label']]
        if prev is not None:
            d_ev = m['ev'] - prev['ev']
            d_n = m['done'] - prev['done']
            lines.append(f"- **{c['label']}**（相对上一项 {c['desc']}）："
                         f"EV {d_ev:+.4f}，结案样本 {d_n:+d} 笔，"
                         f"胜率 {m['wr']*100:.1f}%（上一项 {prev['wr']*100:.1f}%）。")
        else:
            lines.append(f"- **{c['label']}**（基线）：EV {m['ev']:+.4f}，"
                         f"结案 {m['done']} 笔，胜率 {m['wr']*100:.1f}%。")
        prev = m
    lines.append("")
    lines.append("> 说明：以上为日线全历史回测。EV 为单笔数学期望（以 R 计，R=单笔风险）；"
                 "盈亏比=总盈利R/总亏损R；最大回撤为结案交易按出场日排序的权益曲线峰值回落。"
                 "周线版本与增强版覆盖原版的生产决策，待本回测结论由用户拍板后再行处理。")

    report = "\n".join(lines)
    out_path = os.path.join(project_root, 'docs', 'gap_h2_enhanced_backtest_report.md')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"[*] 报告已写入: {out_path}")

    # 控制台摘要
    print("\n" + "=" * 70)
    print("  GAP H2 回测对比摘要 (日线全历史)")
    print("=" * 70)
    print(header.replace('|', ' | '))
    for c in CONFIGS:
        m = metrics[c['label']]
        pf = m['pf']
        pf_s = f"{pf:.2f}" if pf != float('inf') else "inf"
        print(f"  {c['label']:12s} | {m['total']:>5d} | {m['done']:>4d} | "
              f"{m['wr']*100:5.1f}% | +{m['avg_w']:.3f} | {m['avg_l']:.3f} | {pf_s:>4s} | "
              f"{m['ev']:+.4f} | {m['total_r']:+.1f} | {m['mdd']:.2f} | {m['max_loss_streak']:>2d} |")
    print("=" * 70)
    return metrics


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--limit', type=int, default=0)
    args = p.parse_args()
    trades = run(limit=args.limit)
    if trades:
        analyze(trades)

# -*- coding: utf-8 -*-
"""
core/backtest_engine.py — 统一成本敏感退出模拟引擎 (v2, 校准专用)

设计原则 (专业化修订):
  1. Look-ahead SAFE: 评级由调用方在 df.iloc[:idx+1] 切片上计算; 本引擎只用信号 bar
     之后的数据做退出模拟 (后见之明, 回测本就该如此)。
  2. 统一交易模型 (ALL 策略同一口径, 方可横向比较):
       - 入场: 信号后第 1 根 (idx+1) 以 Buy-Stop 触发, 成交价 = max(入场参考价, 开盘) *(1+滑点)
               入场前若直接跌穿止损线 -> INVALIDATED (撤单, 不计入分母)
       - 止损: 信号自带的 sl_price *(1-滑点)
       - 目标: 入场参考价 + RISK_MULT * risk, 其中 risk = 入场参考价 - sl_price (R:R 受控, 默认 2R)
       - 最大持仓: max_hold 根, 到期按收盘平仓
  3. 真实成本 (对齐本项目已验证引擎 gap_h2_backtest.py 口径; 2023-08-28 起印花税已由 0.1% 降至 0.05%):
       - 印花税 0.05% (5bps) 仅卖出
       - 佣金 0.03% (3bps) 双边
       - 滑点 0 (信号级基线不含滑点; 如需保守可另开 SLIPPAGE_BPS)
       -> 单边往返约 11bps ≈ 0.11%
  4. 输出净口径: net_R (扣费后 R), net_win (net_R>0), gross_win (价格先摸目标),
     net_pct (扣费后收益率%), EV 全部基于净口径, 可直接用于样本外校准。

退出状态: WIN / LOSS (已结案, 入分母) | INVALIDATED (未入场撤单) | HOLDING (窗口未决/EOF) | ERROR
"""
from typing import Dict, Any, Optional
import pandas as pd
import numpy as np

# ---- A 股成本模型 (bps = 万分之, 对齐 gap_h2_backtest.py 已验证基线) ----
STAMP_SELL_BPS = 5.0     # 印花税 0.05%, 仅卖 (2023-08-28 后标准)
COMMISSION_BPS = 3.0     # 佣金 0.03%, 双边
SLIPPAGE_BPS = 0.0       # 滑点 0 (信号级基线不含滑点)


def _cost_buy(notional: float) -> float:
    return notional * (COMMISSION_BPS / 1e4)


def _cost_sell(notional: float) -> float:
    return notional * ((COMMISSION_BPS + STAMP_SELL_BPS) / 1e4)


def simulate_trade_unified(df: pd.DataFrame, idx: int,
                           entry_ref: float, sl_price: float,
                           risk_mult: float = 2.0,
                           tp_price: Optional[float] = None,
                           max_hold: Optional[int] = None,
                           slippage_bps: float = SLIPPAGE_BPS,
                           lifecycle_filters: bool = False) -> Dict[str, Any]:
    """
    统一成本敏感模拟。ALL 策略共用此函数, 仅 entry_ref / sl_price / 目标 不同。

    参数:
      entry_ref : Buy-Stop 触发参考价 (信号 bar 的突破位 / 入场列)
      sl_price  : 初始止损价
      risk_mult : 目标 R 倍数 (当 tp_price 未显式给出时, tp = entry + risk_mult*risk)
      tp_price  : 显式目标位 (优先); 非 MTR 策略传自身 tp_col 以忠实实盘 R:R
    返回:
      status, net_R, gross_R, net_win(±1), gross_win(bool), net_pct,
      bars_held, entry_date, exit_date, entry_fill, exit_fill
    """
    slip = slippage_bps / 1e4
    try:
        if not (entry_ref > 0 and sl_price > 0 and sl_price < entry_ref):
            return _err('价格参数非法 (需 entry>sl>0)')
        risk = entry_ref - sl_price
        if risk <= 0:
            return _err('无风险空间')

        if tp_price is not None and tp_price > entry_ref:
            tp_price = float(tp_price)
        else:
            tp_price = entry_ref + risk_mult * risk

        post = df.iloc[idx + 1:]
        if post.empty:
            return _pending('EOF 未触发')

        entry_date = None
        actual_entry = None
        trade_status = 'WAIT_TRIGGER'

        for step, (_, row) in enumerate(post.iterrows()):
            lo = float(row['low']); hi = float(row['high']); op = float(row['open'])
            dt = row['date'] if 'date' in row else step

            if trade_status == 'WAIT_TRIGGER':
                # [P1-10 修复] 入场触发判定必须优先于"未入场跌穿撤单"判定。
                # 同根 bar 同时向上触 entry_ref、向下破 sl_price 时, 价格必先到达
                # 突破位(entry)才回落扫损 -> 属"入场后止损"的真实亏损(LOSS), 而非
                # "未入场失效"(INVALIDATED)。原顺序把这类真实亏损剔除出胜率分母,
                # 造成系统性乐观偏差, 污染评级校准。
                if hi >= entry_ref:
                    actual_entry = max(entry_ref, op) * (1 + slip)
                    entry_date = dt
                    trade_status = 'IN_TRADE'
                    # 入场当日极端: 保守先判扫损
                    if lo <= sl_price:
                        sl_fill = sl_price * (1 - slip)
                        return _close(actual_entry, sl_fill, risk, '入场当日巨震扫损',
                                      entry_date, dt)
                    if hi >= tp_price:
                        tp_fill = tp_price * (1 - slip)
                        return _close(actual_entry, tp_fill, risk, '入场当日即打止盈',
                                      entry_date, dt)
                else:
                    # 未触发入场: 形态破位撤单 (通用, 所有策略)
                    if lo < sl_price:
                        return {'status': 'INVALIDATED', 'reason': '未入场即跌穿防守线',
                                'net_R': 0.0, 'gross_R': 0.0, 'net_win': 0,
                                'gross_win': False, 'net_pct': 0.0, 'bars_held': 0,
                                'entry_date': None, 'exit_date': None,
                                'entry_fill': 0.0, 'exit_fill': 0.0}
                    # 缺口家族生命周期三过滤 (仅等待期有意义)
                    if lifecycle_filters:
                        # 止盈先达作废: 多头动能已释放, 入场意义消失
                        if hi >= tp_price:
                            return {'status': 'VOIDED', 'reason': '等待期止盈先达, 动能已释放',
                                    'net_R': 0.0, 'gross_R': 0.0, 'net_win': 0,
                                    'gross_win': False, 'net_pct': 0.0, 'bars_held': 0,
                                    'entry_date': None, 'exit_date': None,
                                    'entry_fill': 0.0, 'exit_fill': 0.0}
                        # 超时失效: 久盘动能衰竭
                        if step > 30:
                            return {'status': 'TIMEOUT', 'reason': '等待超过30根未触发',
                                    'net_R': 0.0, 'gross_R': 0.0, 'net_win': 0,
                                    'gross_win': False, 'net_pct': 0.0, 'bars_held': 0,
                                    'entry_date': None, 'exit_date': None,
                                    'entry_fill': 0.0, 'exit_fill': 0.0}
            else:  # IN_TRADE
                if lo <= sl_price:
                    sl_fill = sl_price * (1 - slip)
                    return _close(actual_entry, sl_fill, risk, '正常扫损退场',
                                  entry_date, dt)
                if hi >= tp_price:
                    tp_fill = tp_price * (1 - slip)
                    return _close(actual_entry, tp_fill, risk, '正常打止盈点',
                                  entry_date, dt)
                if max_hold is not None and step >= max_hold:
                    close_fill = float(row['close']) * (1 - slip)
                    return _close(actual_entry, close_fill, risk, '达最大持仓周期',
                                  entry_date, dt)

        if trade_status == 'IN_TRADE':
            return {'status': 'HOLDING', 'reason': '持仓中至今未达目标',
                    'net_R': 0.0, 'gross_R': 0.0, 'net_win': 0,
                    'gross_win': False, 'net_pct': 0.0, 'bars_held': len(post),
                    'entry_date': entry_date, 'exit_date': None,
                    'entry_fill': actual_entry, 'exit_fill': 0.0}
        return _pending('挂单中至今未触发')

    except Exception as e:
        return _err(str(e))


def _close(entry_fill: float, exit_fill: float, risk: float,
           reason: str, entry_date, exit_date) -> Dict[str, Any]:
    buy_cost = _cost_buy(entry_fill)
    sell_cost = _cost_sell(exit_fill)
    gross_pnl = exit_fill - entry_fill
    net_pnl = gross_pnl - buy_cost - sell_cost
    gross_R = gross_pnl / risk
    net_R = net_pnl / risk
    net_pct = net_pnl / entry_fill * 100.0
    return {
        'status': 'WIN' if net_R > 0 else 'LOSS',
        'reason': reason,
        'net_R': round(net_R, 4),
        'gross_R': round(gross_R, 4),
        'net_win': 1 if net_R > 0 else -1,
        'gross_win': True if gross_pnl > 0 else False,
        'net_pct': round(net_pct, 4),
        'bars_held': 0,
        'entry_date': entry_date,
        'exit_date': exit_date,
        'entry_fill': round(entry_fill, 4),
        'exit_fill': round(exit_fill, 4),
    }


def _err(reason: str) -> Dict[str, Any]:
    return {'status': 'ERROR', 'reason': reason, 'net_R': 0.0, 'gross_R': 0.0,
            'net_win': 0, 'gross_win': False, 'net_pct': 0.0, 'bars_held': 0,
            'entry_date': None, 'exit_date': None, 'entry_fill': 0.0, 'exit_fill': 0.0}


def _pending(reason: str) -> Dict[str, Any]:
    return {'status': 'PENDING', 'reason': reason, 'net_R': 0.0, 'gross_R': 0.0,
            'net_win': 0, 'gross_win': False, 'net_pct': 0.0, 'bars_held': 0,
            'entry_date': None, 'exit_date': None, 'entry_fill': 0.0, 'exit_fill': 0.0}


# ============================================================
# 统计辅助: Wilson 95% 置信区间 (胜率)
# ============================================================
def wilson_ci(k: int, n: int, z: float = 1.96):
    """返回 (lower_pct, upper_pct) 百分点。n=0 返回 (None, None)。"""
    if n == 0:
        return None, None
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    lo = max(0.0, center - half)
    hi = min(1.0, center + half)
    return round(lo * 100, 2), round(hi * 100, 2)

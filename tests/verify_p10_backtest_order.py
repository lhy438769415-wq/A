"""P1-10 回归: 回测同K线 entry/SL 成交顺序。

核心不变量: 在 WAIT_TRIGGER 状态, 入场触发(hi>=entry_ref)必须优先于
"未入场跌穿撤单"(lo<sl_price) 判定。同根 bar 同时向上触 entry、向下破 sl
时, 价格必先到达突破位才回落扫损 -> 属"入场后止损"的真实亏损(LOSS),
而非"未入场失效"(INVALIDATED)。原顺序把这批复亏损剔除出胜率分母 ->
系统性乐观偏差, 污染评级校准。
"""
import pandas as pd
from core import backtest_engine as be


def _df(rows):
    """rows: list of (open, high, low, close); 自动给 date 列。"""
    data = {
        'open': [r[0] for r in rows],
        'high': [r[1] for r in rows],
        'low': [r[2] for r in rows],
        'close': [r[3] for r in rows],
        'date': [f'2026-0{i+1}-01' for i in range(len(rows))],
    }
    return pd.DataFrame(data)


# entry_ref=100, sl_price=95 (risk=5), tp=110
ENTRY, SL, TP = 100.0, 95.0, 110.0


def test_same_bar_entry_and_stop_is_loss_not_invalidated():
    """[P1-10 主修复] 同根既触 entry(105)又破 sl(94) -> LOSS, 绝不 INVALIDATED。"""
    df = _df([
        (99, 99, 98, 98),        # idx=0 信号 bar
        (100, 105, 94, 96),      # idx=1 高触 entry, 低破 sl
    ])
    r = be.simulate_trade_unified(df, 0, ENTRY, SL, tp_price=TP)
    assert r['status'] != 'INVALIDATED', f"同根触entry又破sl被错判撤单: {r}"
    assert r['status'] == 'LOSS', f"应记为入场后止损亏损: {r}"
    assert r['net_R'] < 0, f"净R应为负: {r}"


def test_legit_invalidated_when_no_entry_and_below_sl():
    """合法撤单: 未触发入场(高98<entry)且跌破 sl(94) -> INVALIDATED。"""
    df = _df([
        (99, 99, 98, 98),
        (96, 98, 94, 95),        # 未触 entry, 跌穿 sl
    ])
    r = be.simulate_trade_unified(df, 0, ENTRY, SL, tp_price=TP)
    assert r['status'] == 'INVALIDATED', f"应合法撤单: {r}"


def test_normal_entry_then_tp_is_win():
    """正常: 入场后当根即达 tp(111) -> WIN。"""
    df = _df([
        (99, 99, 98, 98),
        (100, 111, 100, 110),    # 触 entry 且达 tp, 未破 sl
    ])
    r = be.simulate_trade_unified(df, 0, ENTRY, SL, tp_price=TP)
    assert r['status'] == 'WIN', f"应止盈: {r}"
    assert r['net_R'] > 0


def test_entry_then_stop_on_next_bar_is_loss():
    """入场后次根扫损 -> LOSS (IN_TRADE 路径不受影响)。"""
    df = _df([
        (99, 99, 98, 98),
        (100, 105, 99, 103),     # 触 entry, 未破 sl/未达 tp
        (102, 103, 94, 95),      # 次根破 sl
    ])
    r = be.simulate_trade_unified(df, 0, ENTRY, SL, tp_price=TP)
    assert r['status'] == 'LOSS', f"应止损: {r}"
    assert r['net_R'] < 0


def test_wait_bar_reaching_entry_triggers_entry_not_voided():
    """[P1-10 关联回归] 等待期 bar 高位触 entry(130>=120) -> 正确入场(IN_TRADE),
    不再被原顺序里的 VOIDED/INVALIDATED 抢先误判。

    注: 函数仅在 tp>entry 时尊重显式 tp, 故"等待期 tp 先达(且 tp>entry)"在
    语义上不可达(高位达 tp 必也达 entry), 该 VOIDED 分支为死代码, 不在 P1-10 范围。
    本用例仅锁定"触 entry 必入场"的不变量。
    """
    df = _df([
        (99, 99, 98, 98),
        (125, 130, 115, 128),   # 高130>=entry120 触 entry, 低115>=sl110, 未达默认tp(140)
    ])
    r = be.simulate_trade_unified(df, 0, 120.0, 110.0, lifecycle_filters=True)
    assert r['entry_date'] is not None, f"应已入场: {r}"
    assert r['status'] not in ('INVALIDATED', 'VOIDED'), f"不应被撤单/作废: {r}"

# -*- coding: utf-8 -*-
"""
P0-2 SignalTracker 集成验证 (独立临时库, 绝不触碰生产 baostock.db)。

覆盖 6 个子问题:
  2.1 signal_id 增加 timeframe 维度 -> 同 code/strategy/date 的日/周信号不冲突
  2.2 稳定信号日期 (scanner._build_hit 注入 row['date'], 不再回退运行日)
  2.3 update_signal_entry 精确按 signal_id 更新 (周线行不被误改)
  2.4 get_signals_by_status 从 extra_json 解析真实 signal_bar_idx (不再恒为 -1)
  2.5 周线生命周期计数用周线 bar 数 (旧逻辑用日线 bar 数 -> 8周≈8日 提前过期)
  2.6 周线 3K 信号一并归档进 signal_archive (消除独立状态源)

运行: .venv/Scripts/python.exe tests/verify_p02_signal_tracker.py
(本文件无 test_ 前缀, 不被 pytest 收集, 仅作人工验证/回归工具)
"""
import os
import sys
import queue
import tempfile
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# —— 强制临时库 (在导入任何依赖 settings 的模块之前设置) ——
import config.settings as settings
_TMP = tempfile.mkdtemp(prefix="p02_verify_")
settings.DB_PATH = os.path.join(_TMP, "verify_baostock.db")

import core.database as db
# [P0-3] 重置按路径隔离的全局状态 (全新进程本就干净, 此处仅为显式重置)
db._INIT_DONE_PATHS = set()
db._db_pools = {}

from core.signal_tracker import archive_signal, init_signal_archive
from core.signal_tracker.compat import get_signals_by_status, update_signal_entry
from core.signal_tracker.tracking import _track_single
from core.scanner import _build_hit
import core.data_provider as dp
import pandas as pd


def section(name):
    print(f"\n--- {name} ---")


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  [OK] {msg}")


def main():
    init_signal_archive()

    # ============ 2.1 signal_id 加 timeframe 维度 ============
    section("2.1 signal_id 区分 timeframe")
    sid_d = archive_signal(code="600000", strategy="STRATEGY_3K", timeframe="daily",
                           entry=10, sl=9, tp=11, signal_date="2024-03-15",
                           signal_bar_idx=3, name="PBank")
    sid_w = archive_signal(code="600000", strategy="STRATEGY_3K", timeframe="weekly",
                           entry=10, sl=9, tp=11, signal_date="2024-03-15",
                           signal_bar_idx=3, name="PBank")
    check(sid_d == "600000_STRATEGY_3K_daily_2024-03-15", f"日线 signal_id = {sid_d}")
    check(sid_w == "600000_STRATEGY_3K_weekly_2024-03-15", f"周线 signal_id = {sid_w}")
    check(sid_d != sid_w, "同 code/strategy/date 的日/周 signal_id 不冲突")

    # 幂等: 重复归档不新增行
    sid_d2 = archive_signal(code="600000", strategy="STRATEGY_3K", timeframe="daily",
                            entry=10, sl=9, tp=11, signal_date="2024-03-15",
                            signal_bar_idx=3, name="PBank")
    check(sid_d2 == sid_d, "重复归档返回同一 signal_id (幂等)")
    with db.get_db_connection() as conn:
        n = conn.execute("SELECT COUNT(*) FROM signal_archive WHERE code='600000'").fetchone()[0]
    check(n == 2, f"600000 仅 2 行 (日+周), 实际 {n}")

    # ============ 2.2 稳定信号日期 (真实信号K线日期) ============
    section("2.2 稳定信号日期")
    with db.get_db_connection() as conn:
        d_row = conn.execute("SELECT signal_date FROM signal_archive WHERE signal_id=?",
                             (sid_d,)).fetchone()[0]
    check(d_row == "2024-03-15", f"归档 signal_date 持久化为真实信号日 {d_row}")

    # scanner._build_hit 须从 row['date'] 注入 signal_date (根因修复)
    class FakeStrat:
        name = "STRATEGY_3K"

        def get_metadata(self):
            return {}

        def get_signal_info(self, df):
            return {"extra_info": {}}

    df_strat = pd.DataFrame([{
        "date": "2024-05-20", "close": 10.0, "entry_price": 10.0, "sl_price": 9.0,
        "tp1_price": 11.0, "tp2_price": 12.0, "atr": 1.0,
    }])
    hit = _build_hit("600000", FakeStrat(), df_strat)
    check(hit["info"].get("signal_date") == "2024-05-20",
          f"_build_hit 注入 signal_date = {hit['info'].get('signal_date')} (非运行日)")

    # ============ 2.4 get_signals_by_status 真实 signal_bar_idx ============
    section("2.4 查询返回真实 signal_bar_idx")
    # 注意: get_signals_by_status 接受 JSON 状态名 (WATCHING -> SQL PENDING)
    res = get_signals_by_status(["WATCHING"], timeframe="daily")
    check("600000" in res, f"日线信号在 PENDING 列表: {list(res.keys())}")
    check(res["600000"]["signal_bar_idx"] == 3,
          f"signal_bar_idx 解析为真实值 3, 实际 {res['600000']['signal_bar_idx']}")

    # ============ 2.3 精确按 signal_id 更新 (周线行不受影响) ============
    section("2.3 update_signal_entry 精确更新")
    # 注: signal_archive 无 signal_bar_idx 列 (存于 extra_json), update 仅改 entry_price
    ok = update_signal_entry(code="600000", entry=55.5,
                             timeframe="daily", signal_id=sid_d)
    check(ok is True, "精确更新返回 True")
    with db.get_db_connection() as conn:
        d_entry = conn.execute(
            "SELECT entry_price FROM signal_archive WHERE signal_id=?",
            (sid_d,)).fetchone()[0]
        w_entry = conn.execute(
            "SELECT entry_price FROM signal_archive WHERE signal_id=?",
            (sid_w,)).fetchone()[0]
    check(abs(d_entry - 55.5) < 1e-9, f"日线行 entry 被精确更新为 55.5, 实际 {d_entry}")
    check(abs(w_entry - 10.0) < 1e-9, f"周线行 entry 未被误改 (仍 10.0), 实际 {w_entry}")

    # ============ 2.6 周线 3K 一并归档 ============
    section("2.6 周线 3K 归档进 signal_archive")
    sid_3k = archive_signal(code="601318", strategy="STRATEGY_3K", timeframe="weekly",
                            entry=50, sl=45, tp=60, signal_date="2023-11-03",
                            signal_bar_idx=10, name="PingAn")
    check(sid_3k == "601318_STRATEGY_3K_weekly_2023-11-03", f"3K 周线 signal_id = {sid_3k}")
    with db.get_db_connection() as conn:
        n3k = conn.execute(
            "SELECT COUNT(*) FROM signal_archive WHERE strategy='STRATEGY_3K' AND timeframe='weekly'"
        ).fetchone()[0]
    check(n3k == 2, f"周线 3K 共 2 行 (sid_w + sid_3k), 实际 {n3k}")

    # ============ 2.5 周线生命周期按周线 bar 计数 ============
    section("2.5 周线生命周期按周线 bar 计数")
    sid_wl = archive_signal(code="000001", strategy="STRUCTURAL_GAP", timeframe="weekly",
                            entry=100, sl=0.5, tp=120, signal_date="2024-01-01",
                            signal_bar_idx=1, name="PingAnBank")
    with db.get_db_connection() as conn:
        cols = [d[0] for d in conn.execute(
            "SELECT * FROM signal_archive WHERE signal_id=?", (sid_wl,)).description]
        row = dict(zip(cols, conn.execute(
            "SELECT * FROM signal_archive WHERE signal_id=?", (sid_wl,)).fetchone()))

    # 模拟: 信号日后有 100 根日线 bar, 但仅 1 根周线 bar
    base = date(2024, 1, 2)
    daily_dates = [(base + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(100)]
    weekly_dates = [(date(2024, 1, 5) + timedelta(weeks=i)).strftime("%Y-%m-%d") for i in range(1)]
    daily_df = pd.DataFrame({"date": daily_dates, "open": 1.0, "high": 2.0,
                             "low": 1.0, "close": 1.5, "volume": 1.0})
    weekly_df = pd.DataFrame({"trade_date": weekly_dates, "open": 1.0, "high": 2.0,
                              "low": 1.0, "close": 1.5, "volume": 1.0})
    dp.get_stock_data = lambda code, limit=200: daily_df.copy()
    dp.get_stock_data_weekly = lambda code, limit=200: weekly_df.copy()

    # 日线价全在 entry=100 / sl=0.5 之外 -> 不触发入场/失效, 仅走过期判断
    # 旧逻辑: bars_elapsed = 100 (日线) >= 过期阈值 -> 提前 EXPIRED
    # 新逻辑: bars_elapsed = 1 (周线) < 阈值 -> 保持 PENDING (返回 None)
    result = _track_single(row)
    check(result is None,
          f"周线信号仅过 1 周 -> 不提前过期 (返回 None); 旧逻辑会因 100 日线 bar 误判过期")

    # ============ 2.7 fallback 更新 (生产真实路径: 不传 signal_id) ============
    section("2.7 update_signal_entry fallback (无 signal_id) 真实 SQLite 语法")
    # 生产调用方 watchlist.update_signal_bar 从不传 signal_id, 必走 fallback 分支。
    # 旧实现用 "UPDATE ... ORDER BY scan_date DESC LIMIT 1" -> SQLite 不支持 -> 语法报错。
    ok_fb = update_signal_entry(code="600000", entry=77.7, timeframe="daily")
    check(ok_fb is True, "fallback 更新返回 True (无 ORDER BY 语法错误)")
    with db.get_db_connection() as conn:
        fb_entry = conn.execute(
            "SELECT entry_price FROM signal_archive WHERE signal_id=?",
            (sid_d,)).fetchone()[0]
    check(abs(fb_entry - 77.7) < 1e-9, f"fallback 精确更新日线行 entry=77.7, 实际 {fb_entry}")

    # 多行场景: 同 code+timeframe 两行, fallback 仅改其中 1 行 (不受秒级 scan_date 打平影响)
    archive_signal(code="600111", strategy="STRATEGY_AWIL", timeframe="daily",
                   entry=10, sl=9, tp=11, signal_date="2026-01-01",
                   signal_bar_idx=1, name="Old")
    archive_signal(code="600111", strategy="STRATEGY_AWIL", timeframe="daily",
                   entry=10, sl=9, tp=11, signal_date="2026-07-28",
                   signal_bar_idx=2, name="New")
    ok_fb2 = update_signal_entry(code="600111", entry=99.9, timeframe="daily")
    check(ok_fb2 is True, "多行 fallback 更新返回 True")
    with db.get_db_connection() as conn:
        n_upd = conn.execute(
            "SELECT COUNT(*) FROM signal_archive WHERE code='600111' AND timeframe='daily' AND entry_price=99.9"
        ).fetchone()[0]
        n_old = conn.execute(
            "SELECT COUNT(*) FROM signal_archive WHERE code='600111' AND timeframe='daily' AND entry_price=10.0"
        ).fetchone()[0]
    check(n_upd == 1, f"多行中恰好 1 行被更新为 99.9, 实际 {n_upd}")
    check(n_old == 1, f"另 1 行保持 10.0 未被误改, 实际 {n_old}")

    print("\n========================================")
    print("P0-2 SignalTracker 集成验证全部通过 ✅")
    print(f"临时库: {settings.DB_PATH}")
    print("========================================")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        print(f"\n[FAIL] 验证未通过: {e}")
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(f"\n[ERROR] 验证异常: {e}")
        sys.exit(2)

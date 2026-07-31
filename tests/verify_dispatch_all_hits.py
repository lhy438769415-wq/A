# -*- coding: utf-8 -*-
"""
验证 hunter._classify_signals 的派发不受 watchlist 去重拦截。

复现场景: 用户选 MTR_MASTER 扫描出 44 个标的, 但全部已在 watchlist (signal_archive) 中,
且 MTR 信号K线索引稳定 (恒为 299)。修复前 `all_hits = new_hits` 把已跟踪稳定信号全剔除 -> 0 推送。
修复后派发应用完整 all_hits, 已跟踪信号也应出现在 direct_picks/final_picks (推送列表)。

不依赖真实扫描/Discord: monkeypatch prepare_daily_chart 返回占位 res_item。
"""
import os
import sys
import queue
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config.settings as s

# 用独立临时库, 不碰生产库
_TMP = tempfile.mkdtemp()
s.DB_PATH = os.path.join(_TMP, "verify_dispatch.db")

import core.database as db
db._INIT_DONE_PATHS = set()
db._db_pools = {}

import pandas as pd
from core.signal_tracker import archive_signal, init_signal_archive
import hunter


def check(cond, msg):
    status = "[OK] " if cond else "[FAIL] "
    print(status + msg)
    if not cond:
        raise SystemExit(1)


def main():
    init_signal_archive()

    # 预置 2 个"已跟踪"的 MTR 命中 (复刻 signal_bar_idx=299)
    codes = ["sz.000523", "sz.000617"]
    for c in codes:
        archive_signal(
            code=c, strategy="MTR_V35_STRUCTURAL", timeframe="daily",
            entry=10.0, sl=9.0, tp=12.0, signal_date="2026-07-28",
            signal_bar_idx=299, name="测试股",
        )

    # 构造扫描命中 (与真实 res 结构一致)
    def make_res(code):
        df = pd.DataFrame({"date": [pd.Timestamp("2026-07-28")],
                           "open": [1.0], "high": [2.0], "low": [1.0],
                           "close": [1.5], "volume": [1.0]})
        return {
            "code": code,
            "type": "MTR_V35_STRUCTURAL",
            "name_cn": "测试股",
            "df": df,
            "info": {
                "entry": 10.0, "sl": 9.0, "price": 1.5,
                "signal_bar_idx": 299,  # 与已跟踪值相同 -> 旧逻辑判定"未变"被吞
                "score": 0.5,
                "rating": {"letter": "B", "score": 0.5},
            },
        }

    all_hits = [make_res(c) for c in codes]

    # monkeypatch 图表生成, 避免 DataFrame 画图依赖
    orig = hunter.prepare_daily_chart
    hunter.prepare_daily_chart = lambda res, passed=True, reason="": (
        {"code": res["code"], "info": res.get("info", {})}, None, None)

    try:
        direct_picks, final_picks, rejected_list, wl, status_changes = \
            hunter._classify_signals(
                all_hits,
                queue.Queue(), queue.Queue(),
                hunter_threading().Event(), [], use_ai=False,
            )
    finally:
        hunter.prepare_daily_chart = orig

    dispatched = list(direct_picks) + list(final_picks)
    check(len(dispatched) == 2,
          f"已跟踪稳定信号也应被派发: 实际 dispatched={len(dispatched)} (期望 2)")

    dispatched_codes = {d.get("code") for d in dispatched}
    check(dispatched_codes == set(codes),
          f"派发列表包含全部扫描命中 code: {dispatched_codes}")

    print("\n========================================")
    print("派发可见性修复验证全部通过 ✅")
    print(f"临时库: {s.DB_PATH}")
    print("========================================")
    return 0


def hunter_threading():
    import threading
    return threading


if __name__ == "__main__":
    sys.exit(main())

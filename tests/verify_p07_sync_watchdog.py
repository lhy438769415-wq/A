# -*- coding: utf-8 -*-
"""
P1-7 同步看门狗回归测试。

核心验证: 一个**永不完成**的 future (模拟 hung Baostock worker) 不再让整批同步
无限挂死 —— 看门狗应在 stall 上限内强杀进程池并返回。

测试用真实的 concurrent.futures.Future + 真实的 wait()/shutdown() 语义,
不是 mock 掉看门狗逻辑本身, 因此能真实复现并证明修复有效。
"""
import time
import queue
import concurrent.futures
import pandas as pd

from core.data_provider import _drain_futures_with_watchdog
from tools.fetcher_baostock import BsBlacklistedError


class FakeExecutor:
    """记录 shutdown 调用, 不真正杀进程。"""
    def __init__(self):
        self.shutdown_calls = []

    def shutdown(self, wait=False, cancel_futures=False, kill=False):
        self.shutdown_calls.append((wait, cancel_futures, kill))


def _completed_future(result):
    f = concurrent.futures.Future()
    f.set_result(result)
    return f


def _failed_future(exc):
    f = concurrent.futures.Future()
    f.set_exception(exc)
    return f


def test_watchdog_kills_hung_worker():
    """hung worker 不应让整批同步无限挂死; 看门狗应在 stall 上限内强杀。"""
    ex = FakeExecutor()
    q = queue.Queue()
    df = pd.DataFrame({"trade_date": ["2024-01-01"], "close": [1.0]})
    futures_map = {
        _completed_future(("sh.600000", df)): "sh.600000",
        _completed_future(None): "sz.000001",
        concurrent.futures.Future(): "sh.600519",  # 永不完成 -> 模拟 hung worker
    }
    t0 = time.time()
    res = _drain_futures_with_watchdog(
        ex, futures_map, q,
        label="测试同步", progress_every=200,
        stall_limit=2, wall_limit=30, per_task_timeout=1,
    )
    elapsed = time.time() - t0
    # 不超过 一次轮询(5s) + stall(2s) + 余量
    assert elapsed < 15, f"看门狗未在预期内返回, 耗时 {elapsed:.1f}s (疑似仍挂死)"
    assert res["aborted"] is True, "hung worker 未被看门狗判死"
    assert res["done"] == 2, f"已完成数应为 2, 实际 {res['done']}"
    assert res["total"] == 3
    assert res["download_count"] == 1
    assert res["skip_count"] == 1
    # 必须触发强杀
    assert ex.shutdown_calls, "executor.shutdown 未被调用"
    last = ex.shutdown_calls[-1]
    assert last[1] is True, "未取消剩余任务 (cancel_futures)"
    # Python 3.12+ 才有 kill=; 有则必须为 True
    if len(last) > 2:
        assert last[2] is True, "未 SIGKILL hung worker (kill=True)"


def test_watchdog_happy_path():
    """全部 future 正常完成 -> 不触发强杀, done == total。"""
    ex = FakeExecutor()
    q = queue.Queue()
    df = pd.DataFrame({"trade_date": ["2024-01-01"], "close": [1.0]})
    futures_map = {
        _completed_future(("sh.600000", df)): "sh.600000",
        _completed_future(("sz.000001", df)): "sz.000001",
    }
    res = _drain_futures_with_watchdog(
        ex, futures_map, q, label="测试同步", progress_every=200,
        stall_limit=2, wall_limit=30, per_task_timeout=1,
    )
    assert res["aborted"] is False
    assert res["blacklisted"] is False
    assert res["done"] == 2 and res["total"] == 2
    assert res["download_count"] == 2
    assert not ex.shutdown_calls, "happy path 不应强杀进程池"


def test_watchdog_blacklist_aborts():
    """子进程抛 BsBlacklistedError -> 立即终止并强杀。"""
    ex = FakeExecutor()
    q = queue.Queue()
    futures_map = {
        _completed_future(None): "sz.000001",
        _failed_future(BsBlacklistedError("测试黑名单")): "sh.600519",
    }
    res = _drain_futures_with_watchdog(
        ex, futures_map, q, label="测试同步", progress_every=200,
        stall_limit=2, wall_limit=30, per_task_timeout=1,
    )
    assert res["blacklisted"] is True
    assert ex.shutdown_calls, "黑名单场景应强杀进程池"

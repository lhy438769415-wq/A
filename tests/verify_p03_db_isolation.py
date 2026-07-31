# -*- coding: utf-8 -*-
"""
P0-3 测试隔离集成验证 (两个临时库, 绝不触碰生产 baostock.db)。

根因: core/database.py 曾用全局 _db_pool / _INIT_DONE, 不按 DB 路径区分。
  若某测试把 settings.DB_PATH 指向临时库, 但同进程内已为真实库建过连接/初始化,
  则: (a) init_db 因全局 _INIT_DONE=True 提前返回 -> 临时库无表结构;
      (b) get_db_connection 可能复用指向真实库的旧连接 -> 测试写入真实库。

修复: _db_pools 改为 path->Queue 字典, _INIT_DONE 改为按路径集合。
本脚本验证: 在"已为库A建连接并初始化"之后, 切换到库B仍能独立建表/读写, 且互不污染。

运行: .venv/Scripts/python.exe tests/verify_p03_db_isolation.py
(本文件无 test_ 前缀, 不被 pytest 收集, 仅作人工验证/回归工具)
"""
import os
import sys
import queue
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config.settings as settings
import core.database as db

# 全新进程, 全局状态本就干净; 显式重置按路径隔离的全局
db._INIT_DONE_PATHS = set()
db._db_pools = {}


def section(name):
    print(f"\n--- {name} ---")


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  [OK] {msg}")


def main():
    tmp = tempfile.mkdtemp(prefix="p03_")
    path_a = os.path.join(tmp, "db_a.db")   # 模拟"先初始化过的库"
    path_b = os.path.join(tmp, "db_b.db")   # 模拟"测试重定向的临时库"

    # 1) 先为 path_a 建连接 + 初始化 (模拟生产库已在进程内被使用)
    section("1) 为库 A 初始化并写入")
    settings.DB_PATH = path_a
    db.init_db()
    with db.get_db_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO signal_archive "
            "(signal_id, code, strategy, timeframe, signal_date, scan_date) "
            "VALUES (?,?,?,?,?,?)",
            ("A1", "600000", "STRATEGY_3K", "daily", "2024-01-01", "2024-01-01 00:00:00"))
        conn.commit()
    with db.get_db_connection() as conn:
        n_a = conn.execute("SELECT COUNT(*) FROM signal_archive").fetchone()[0]
    check(n_a == 1, f"库 A 有 1 行 (path={path_a})")

    # 2) 切换到 path_b (模拟测试 patch settings.DB_PATH 指向临时库)
    #    旧逻辑下会因全局 _INIT_DONE=True 提前返回 -> 库 B 无表 -> 下面 INSERT 报错
    section("2) 切换到库 B 独立初始化 (P0-3 关键)")
    settings.DB_PATH = path_b
    db.init_db()  # 修复后: 因 path_b 不在 _INIT_DONE_PATHS, 仍会建表
    with db.get_db_connection() as conn:
        # 若 B 无表结构, 此句将抛 OperationalError -> 验证失败
        conn.execute(
            "INSERT OR IGNORE INTO signal_archive "
            "(signal_id, code, strategy, timeframe, signal_date, scan_date) "
            "VALUES (?,?,?,?,?,?)",
            ("B1", "000001", "STRATEGY_3K", "weekly", "2024-02-02", "2024-02-02 00:00:00"))
        conn.commit()
    with db.get_db_connection() as conn:
        n_b = conn.execute("SELECT COUNT(*) FROM signal_archive").fetchone()[0]
        cols = [d[0] for d in conn.execute("SELECT * FROM signal_archive LIMIT 0").description]
    check("signal_archive" is not None and n_b == 1, f"库 B 独立建表并写入 1 行 (path={path_b})")
    check("signal_id" in cols, f"库 B 表结构完整, 列含 signal_id: {cols[:5]}...")

    # 3) 隔离性: 库 A 的连接不被复用到库 B, 二者数据互不污染
    section("3) 隔离性: 库 A / B 互不污染")
    with db.get_db_connection() as conn:  # 此时 settings.DB_PATH = path_b
        n_b_again = conn.execute("SELECT COUNT(*) FROM signal_archive").fetchone()[0]
    settings.DB_PATH = path_a
    with db.get_db_connection() as conn:  # 切回 A
        n_a_again = conn.execute("SELECT COUNT(*) FROM signal_archive").fetchone()[0]
    check(n_b_again == 1, f"库 B 仍为 1 行, 未被 A 数据污染 (实际 {n_b_again})")
    check(n_a_again == 1, f"库 A 仍为 1 行, 未被 B 数据污染 (实际 {n_a_again})")

    # 4) close_all_connections 应排空所有路径的池 (不报错)
    section("4) close_all_connections 排空所有路径池")
    db.close_all_connections()
    check(True, "close_all_connections 执行无异常")

    print("\n========================================")
    print("P0-3 数据库测试隔离验证全部通过 ✅")
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

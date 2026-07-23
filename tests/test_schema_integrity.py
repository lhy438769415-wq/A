# -*- coding: utf-8 -*-
"""
数据库结构守护测试（运营级护栏）

- test_real_databases_pass: 对真实 data/baostock.db 与 data/ai_journal.db 运行
  schema_guard，确认当前库结构与代码声明完全一致（无漂移、无损坏）。
- test_tamper_detected: 复制真实库到临时文件并人为加一列，确认 guard 能
  抓到"带外改动"（证明这把锁真的锁得住，而非永远绿灯）。

注意：本测试只读取真实库，绝不修改；篡改只在临时副本上进行。
"""
import os
import sys
import shutil
import tempfile

import pytest

# tests/ 不在质量门禁扫描范围，允许此引导；确保能 import core.*
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.schema_guard import (  # noqa: E402
    DB_FILES,
    SchemaDriftError,
    _extract_schema_from_source,
    verify_database,
)


def test_real_databases_pass():
    """真实库必须与声明 schema 完全一致。"""
    for label, meta in DB_FILES.items():
        expected = _extract_schema_from_source(meta['schema_source'])
        # 不抛异常即通过；抛 SchemaDriftError 即结构漂移/损坏
        verify_database(label, meta['path'], expected)


def test_tamper_detected():
    """人为给 daily_bars 加一列（带外改动），guard 必须报错。"""
    import sqlite3

    src = DB_FILES['baostock.db']['path']
    expected = _extract_schema_from_source(DB_FILES['baostock.db']['schema_source'])

    with tempfile.TemporaryDirectory() as d:
        tmp = os.path.join(d, 'baostock.db')
        shutil.copy(src, tmp)

        # 模拟"有人绕过代码直接改了库"
        conn = sqlite3.connect(tmp)
        conn.execute('ALTER TABLE daily_bars ADD COLUMN __tamper_marker__ TEXT')
        conn.commit()
        conn.close()

        with pytest.raises(SchemaDriftError):
            verify_database('baostock.db', tmp, expected)

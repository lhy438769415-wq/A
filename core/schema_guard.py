#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
schema_guard — 数据库结构守护（运营级护栏）

定位
----
代码级门禁（quality_gate）只能防止"在错误文件里写建表语句"。
但它管不到最危险的场景：**有人直接对线上数据文件做了带外改动**
（手敲 ALTER/DROP、用 DB 工具改了列、迁移脚本跑飞），导致磁盘上的
baostock.db / ai_journal.db 的真实 schema 与代码声明不一致。

本模块是第二道防线：直接打开真实数据库文件，做两件事：
  1. PRAGMA integrity_check —— 捕获库文件损坏。
  2. 比对"实际表/列"与"权威 DDL 声明的 schema" —— 捕获结构漂移。

关键设计：期望 schema 不是手写副本，而是**实时解析 schema 主人源码**
（core/database.py / tools/journal.py）里的建表语句得到。这样：
  - 单一来源：guard 永远反映代码里声明的结构，不会和 DDL 脱节；
  - 若有人只改了线上库（没改代码）→ guard 抓到漂移；
  - 若有人改了代码 DDL → 必须同步（否则 guard 也会提醒），倒逼一起改。

零第三方依赖，纯标准库。
"""
import os
import re
import sqlite3

# core/schema_guard.py -> PROJECT_ROOT
CORE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CORE_DIR)

# 每个数据库 = 一个真实文件 + 其 schema 主人源码
DB_FILES = {
    'baostock.db': {
        'path': os.path.join(PROJECT_ROOT, 'data', 'baostock.db'),
        'schema_source': os.path.join(PROJECT_ROOT, 'core', 'database.py'),
    },
    'ai_journal.db': {
        'path': os.path.join(PROJECT_ROOT, 'data', 'ai_journal.db'),
        'schema_source': os.path.join(PROJECT_ROOT, 'tools', 'journal.py'),
    },
}

# 允许实际库里存在、但不在声明 schema 中的系统表（如 AUTOINCREMENT 产生）
ALLOWED_EXTRA_TABLES = {'sqlite_sequence'}

# 列名解析时跳过的约束关键字（非列定义）
_NON_COLUMN_KEYWORDS = {'PRIMARY', 'KEY', 'UNIQUE', 'FOREIGN', 'CONSTRAINT', 'CHECK'}


class SchemaDriftError(Exception):
    """数据库实际结构与声明 schema 不一致。"""


def _parse_columns(inside: str):
    """从建表语句的 (...) 内部文本解析出列名列表（顺序敏感）。"""
    depth = 0
    segments = []
    cur = []
    for ch in inside:
        if ch == '(':
            depth += 1
            cur.append(ch)
        elif ch == ')':
            depth -= 1
            cur.append(ch)
        elif ch == ',' and depth == 0:
            segments.append(''.join(cur))
            cur = []
        else:
            cur.append(ch)
    if cur:
        segments.append(''.join(cur))

    cols = []
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        first = seg.split()[0].rstrip('`"[]').upper()
        if first in _NON_COLUMN_KEYWORDS:
            continue
        # 列名可能是 `name` / "name" / [name] / name
        name = seg.split()[0].strip('`"[]')
        cols.append(name)
    return cols


def _extract_schema_from_source(py_path: str) -> dict:
    """从 schema 主人源码解析出 {表名: [列名...]}。"""
    with open(py_path, encoding='utf-8') as fh:
        src = fh.read()
    # 匹配建表语句 [IF NOT EXISTS] name ( ... ); 或 ( ... )"""
    # 注意：database.py 的 DDL 以 ");" 结尾，journal.py 以 ")" + 三引号结尾，
    # 必须用 ")" 后紧跟 ";" 或 '"""' 来终止，否则非贪婪匹配会越过下一张表。
    pattern = re.compile(
        r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_]\w*)\s*\((.*?)\)\s*(?:;|""")',
        re.S | re.I,
    )
    schema = {}
    for m in pattern.finditer(src):
        name = m.group(1)
        cols = _parse_columns(m.group(2))
        schema[name] = cols
    return schema


def _open_ro(db_path: str) -> sqlite3.Connection:
    """以只读模式打开，避免守护脚本意外写入。"""
    return sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)


def verify_database(db_label: str, db_path: str, expected: dict):
    """校验单个数据库。异常 SchemaDriftError 表示结构受损/漂移。"""
    if not os.path.exists(db_path):
        raise SchemaDriftError(f'[{db_label}] 数据库文件不存在: {db_path}')

    conn = _open_ro(db_path)
    try:
        # 1) 完整性检查
        rows = conn.execute('PRAGMA integrity_check').fetchall()
        verdicts = [r[0] for r in rows]
        if any(v.lower() != 'ok' for v in verdicts):
            raise SchemaDriftError(
                f'[{db_label}] integrity_check 未通过: {verdicts}')

        # 2) 结构比对
        actual_tables = {r[0] for r in
                         conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

        for table, exp_cols in expected.items():
            if table not in actual_tables:
                raise SchemaDriftError(
                    f'[{db_label}] 缺少表 {table}（声明 schema 要求存在）')
            actual_cols = [r[1] for r in
                           conn.execute(f'PRAGMA table_info({table})').fetchall()]
            exp_set, act_set = set(exp_cols), set(actual_cols)
            missing = exp_set - act_set
            extra = act_set - exp_set
            if missing:
                raise SchemaDriftError(
                    f'[{db_label}] 表 {table} 缺少列: {sorted(missing)}')
            if extra:
                raise SchemaDriftError(
                    f'[{db_label}] 表 {table} 多出未声明列(可能是带外改动): {sorted(extra)}')
        return True
    finally:
        conn.close()


def run_all() -> dict:
    """校验全部已知数据库，返回 {label: 'OK'|error}。"""
    results = {}
    for label, meta in DB_FILES.items():
        expected = _extract_schema_from_source(meta['schema_source'])
        try:
            verify_database(label, meta['path'], expected)
            results[label] = 'OK'
        except SchemaDriftError as e:
            results[label] = str(e)
    return results


def main():
    import sys
    results = run_all()
    all_ok = all(v == 'OK' for v in results.values())
    print('=' * 60)
    print('Schema Guard — 数据库结构守护')
    print('=' * 60)
    for label, status in results.items():
        print(f'  {label}: {status}')
    print('=' * 60)
    if all_ok:
        print('结果: 全部通过 (退出码 0)')
        sys.exit(0)
    print('结果: 发现结构漂移/损坏 (退出码 1)')
    sys.exit(1)


if __name__ == '__main__':
    main()

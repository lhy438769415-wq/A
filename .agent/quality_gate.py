#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
质量门禁（Quality Gate）—— 工程护栏第一道。

不依赖任何业务理解，纯静态扫描项目内的"纸面红线"是否真的被违反，
并把"测试函数数"作为守卫（防 AI 偷偷删测试让检查变绿）。

用法：
  python .agent/quality_gate.py            # 跑全部检查，违规则退出码非0
  python .agent/quality_gate.py --init-baseline   # 建立测试数基线（首次）
  python .agent/quality_gate.py --strict   # 把"警告级"也当失败

设计原则（自身也要干净）：
  - 不用 sys.path.insert / logging.basicConfig（避免双标）
  - 纯标准库，零第三方依赖
  - 路径含空格也能跑（用 os.walk，不拼 shell）
"""
import os
import re
import sys

# 本文件位于 <项目根>/.agent/quality_gate.py
AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(AGENT_DIR)

EXCLUDE_DIRS = {'.git', '__pycache__', '.venv', 'node_modules', '.workbuddy',
                '.agent', 'quant', 'strategy_lab', 'docs', 'tests'}

# 红线模式（来自 AGENTS.md / .agent/rules/coding-standards.md）
FORBIDDEN_PATTERNS = {
    'sys.path.insert': re.compile(r'sys\.path\.insert\s*\('),
    'logging.basicConfig': re.compile(r'logging\.basicConfig\s*\('),
    'bare except': re.compile(r'except\s*:\s*(?!#)'),
    # 同时覆盖绝对 (from foo import *) 与相对 (from ... import *) 通配导入
    'from module import *': re.compile(r'from\s+[.\w]+\s+import\s+\*'),
}

# 红线授权的"合规替代物"文件：允许其内部作为唯一的合法注入点。
# sys.path.insert 只允许出现在 core/paths.py 的 ensure_importable() 中；
# 其余文件一律不得出现 sys.path.insert（门禁据此豁免该文件）。
EXEMPT_FROM_FORBIDDEN = {'core/paths.py'}

# DDL 单一来源（每个数据库单一 schema 主人，白名单）：
#   - core/database.py      → 拥有 baostock.db (daily_bars/weekly_bars/abu_indicators/signal_archive/trade_reviews)
#   - tools/journal.py       → 拥有 ai_journal.db (hunter_journal/guardian_journal)
# 其它任何文件出现 CREATE/ALTER TABLE 即视为违规并阻断提交（防止未来迭代随意改库结构）。
DDL_ALLOWLIST = {
    'core/database.py',
    'tools/journal.py',
}

BASELINE_PATH = os.path.join(AGENT_DIR, 'test_baseline.txt')


def iter_py_files():
    for dirpath, dirnames, filenames in os.walk(PROJECT_ROOT):
        # 就地剪枝，避免陷入无关目录
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for fn in filenames:
            if fn.endswith('.py'):
                yield os.path.join(dirpath, fn)


def scan_forbidden(files):
    hits = []
    for fp in files:
        rel = os.path.relpath(fp, PROJECT_ROOT)
        if rel.replace('\\', '/') in EXEMPT_FROM_FORBIDDEN:
            continue
        try:
            with open(fp, encoding='utf-8') as fh:
                for i, line in enumerate(fh, 1):
                    for name, pat in FORBIDDEN_PATTERNS.items():
                        if pat.search(line):
                            hits.append((rel, i, name, line.strip()))
        except (OSError, UnicodeDecodeError):
            pass
    return hits


def scan_ddl_violations(files):
    """DDL 仅允许出现在白名单（schema 主人）文件中；CREATE/ALTER TABLE 一律拦截。"""
    hits = []
    for fp in files:
        rel = os.path.relpath(fp, PROJECT_ROOT).replace('\\', '/')
        if rel in DDL_ALLOWLIST:
            continue
        try:
            with open(fp, encoding='utf-8') as fh:
                for i, line in enumerate(fh, 1):
                    if re.search(r'(CREATE|ALTER)\s+TABLE', line, re.I):
                        hits.append((rel, i, 'DDL outside schema-owner allowlist',
                                     line.strip()))
        except (OSError, UnicodeDecodeError):
            pass
    return hits


def scan_get_logger_order(files):
    """护栏: 模块级 `get_logger(__name__)` 调用不得早于其 import。

    此前出现过 "调用早于 import" 的回归 (NameError 风险), 这里闭环该缺口。
    `core/log_config.py` 本身定义 get_logger, 排除之, 避免自指误报。
    """
    GET_LOGGER_IMPORT = re.compile(
        r'(from\s+[\w.]*log_config\s+import\s+get_logger'
        r'|from\s+core\s+import\s+log_config'
        r'|import\s+[\w.]*get_logger)')
    GET_LOGGER_CALL = re.compile(r'get_logger\s*\(')
    EXEMPT = {'core/log_config.py'}
    hits = []
    for fp in files:
        rel = os.path.relpath(fp, PROJECT_ROOT).replace('\\', '/')
        if rel in EXEMPT:
            continue
        try:
            with open(fp, encoding='utf-8') as fh:
                lines = fh.readlines()
        except (OSError, UnicodeDecodeError):
            continue
        import_line = None
        for i, line in enumerate(lines, 1):
            if GET_LOGGER_IMPORT.search(line):
                import_line = i
                break
        if import_line is None:
            continue  # 该文件未引入 get_logger, 不查顺序
        for i, line in enumerate(lines, 1):
            if i < import_line and GET_LOGGER_CALL.search(line):
                hits.append((rel, i, 'get_logger call before its import',
                             line.strip()))
    return hits


def count_test_functions():
    """单独遍历 tests/ 目录统计 def test_ 数量（守卫：防删测试让检查变绿）。"""
    total = 0
    tests_dir = os.path.join(PROJECT_ROOT, 'tests')
    if not os.path.isdir(tests_dir):
        return 0
    for dirpath, dirnames, filenames in os.walk(tests_dir):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for fn in filenames:
            if not fn.endswith('.py'):
                continue
            fp = os.path.join(dirpath, fn)
            try:
                with open(fp, encoding='utf-8') as fh:
                    for line in fh:
                        if re.match(r'\s*def\s+test_', line):
                            total += 1
            except (OSError, UnicodeDecodeError):
                pass
    return total


def load_baseline():
    if not os.path.exists(BASELINE_PATH):
        return None
    with open(BASELINE_PATH, encoding='utf-8') as fh:
        try:
            return int(fh.read().strip())
        except ValueError:
            return None


def main():
    args = sys.argv[1:]
    init_baseline = '--init-baseline' in args
    strict = '--strict' in args

    files = list(iter_py_files())

    forbidden = scan_forbidden(files)
    forbidden += scan_get_logger_order(files)
    ddl = scan_ddl_violations(files)
    test_count = count_test_functions()

    print('=' * 60)
    print('质量门禁 Quality Gate')
    print('=' * 60)
    print(f'扫描 .py 文件数: {len(files)}')
    print(f'测试函数数: {test_count}')

    if init_baseline:
        with open(BASELINE_PATH, 'w', encoding='utf-8') as fh:
            fh.write(str(test_count))
        print(f'[基线] 已建立测试数基线 = {test_count} -> {BASELINE_PATH}')

    # 违规汇总
    print('-' * 60)
    if forbidden:
        print(f'[违规] 红线模式命中 {len(forbidden)} 处:')
        for rel, i, name, snippet in forbidden:
            print(f'  {rel}:{i}  <{name}>  {snippet[:80]}')
    else:
        print('[OK] 无 sys.path.insert / basicConfig / 裸except / import *')

    if ddl:
        print(f'[违规] DDL 出现在非 schema 主人文件 {len(ddl)} 处 (仅允许 {sorted(x.replace(chr(92), "/") for x in DDL_ALLOWLIST)}):')
        for rel, i, name, snippet in ddl:
            print(f'  {rel}:{i}  {snippet[:80]}')
    else:
        print('[OK] 全部 DDL 均位于 schema 主人白名单 (core/database.py, tools/journal.py)')

    # 测试数守卫
    baseline = load_baseline()
    if baseline is not None:
        if test_count < baseline:
            print(f'[失败] 测试数 {test_count} < 基线 {baseline} '
                  f'(可能有人删了测试让检查变绿)')
            forbidden.append(('TEST_GUARD', 0, 'test count dropped', ''))
        else:
            print(f'[OK] 测试数 {test_count} >= 基线 {baseline}')
    elif not init_baseline:
        print(f'[提示] 无测试基线，跑一次: python .agent/quality_gate.py --init-baseline')

    print('=' * 60)

    if forbidden or ddl:
        print('结果: 未通过 (退出码 1) — DDL 违规将阻断提交, 改库结构须经 schema 主人文件')
        sys.exit(1)
    print('结果: 通过')
    sys.exit(0)


if __name__ == '__main__':
    main()

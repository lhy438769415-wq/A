# -*- coding: utf-8 -*-
"""测试清单网格图 (generate_list_grid_image) 与 extract_rr 盈亏比提取"""
import sys, os, io
if __name__ == '__main__':
    # 仅在直接运行脚本时重设编码; 被 pytest 收集时保持原有 stdout (避免破坏 capture)
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.notifier import generate_list_grid_image, extract_rr

PNG_MAGIC = b'\x89PNG'


def test_extract_rr_direct():
    """已有 rr 直接返回; 缺失时由 entry/sl/tp1 反算; 全缺返回 0"""
    assert extract_rr({'rr': 2.3}) == 2.3
    # (tp1-entry)/(entry-sl) = (12-10)/(10-9) = 2.0
    assert extract_rr({'rr': 0, 'entry': 10, 'sl': 9, 'tp1': 12}) == 2.0
    assert extract_rr({}) == 0


def test_empty_rows_returns_empty():
    """空清单返回空列表, 不生成图"""
    assert generate_list_grid_image([]) == []


def test_16_rows_single_page():
    """16 只 (4列×4行) 应合成 1 张 PNG, 且内容非空"""
    rows = [[f"{600000 + i}", f"股票{i}", 2.3] for i in range(16)]
    bufs = generate_list_grid_image(rows, title="MTR (16只)", cols=4)
    assert len(bufs) == 1
    data = bufs[0].getvalue()
    assert data[:4] == PNG_MAGIC
    assert len(data) > 1000


def test_pagination_over_threshold():
    """超阈值自动分页: 65 只 / 每页 40 只 (4列×10行) → 2 张"""
    rows = [[f"{600000 + i}", f"股票{i}", 1.5] for i in range(65)]
    bufs = generate_list_grid_image(rows, title="MTR (65只)", cols=4, max_rows_per_page=10)
    assert len(bufs) == 2
    for b in bufs:
        assert b.getvalue()[:4] == PNG_MAGIC


def test_chinese_names_no_crash():
    """中文名不抛异常, 返回合法 PNG (字体缺失时 matplotlib 用 fallback, 不 crash)"""
    rows = [["600519", "贵州茅台", 2.3], ["000858", "五粮液", 1.8]]
    bufs = generate_list_grid_image(rows, title="MTR (2只)", cols=4)
    assert len(bufs) == 1
    assert bufs[0].getvalue()[:4] == PNG_MAGIC


def test_zero_rr_renders_no_r_suffix():
    """rr=0 时不拼 R 后缀, 仅代码+名称 (用于确认 0 命中/无 R 的格子干净)"""
    rows = [["600519", "贵州茅台", 0]]
    bufs = generate_list_grid_image(rows, title="X (1只)", cols=4)
    assert len(bufs) == 1
    assert bufs[0].getvalue()[:4] == PNG_MAGIC

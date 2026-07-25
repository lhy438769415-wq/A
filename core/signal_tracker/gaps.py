# -*- coding: utf-8 -*-
"""
历史已止盈缺口查询 (供图表叠加绘制)

get_resolved_gaps(): 查询指定股票的已止盈 GAP 策略历史记录。
拆分自 core/signal_tracker.py (P11)。
"""

from core.database import get_db_connection, init_signal_archive

from ._shared import logger


def get_resolved_gaps(code: str, strategy_pattern: str = 'GAP') -> list:
    """
    查询指定股票的已止盈 GAP 策略历史记录。

    用于在新信号推送图表上叠加绘制"前序已止盈缺口"，
    辅助人工判断该区域缺口策略的历史表现。

    Args:
        code: 股票代码
        strategy_pattern: 策略名模糊匹配关键词 (默认 'GAP'，
                          匹配 STRATEGY_GAP_H2 / STRATEGY_GAP_PINBAR / STRATEGY_STRUCTURAL_GAP)

    Returns:
        list[dict]: 每条记录包含:
            - signal_date, resolved_date
            - entry_price, sl_price (Gap Floor), tp_price, exit_price
            - strategy, status
        按 signal_date DESC 排序 (最近的在前)
    """
    init_signal_archive()
    try:
        with get_db_connection() as conn:
            # 仅查询已止盈 (WIN) 的缺口策略记录
            rows = conn.execute(
                """
                SELECT signal_date, resolved_date, entry_price, sl_price,
                       tp_price, exit_price, strategy, status
                FROM signal_archive
                WHERE code = ?
                  AND strategy LIKE ?
                  AND status = 'WIN'
                ORDER BY signal_date DESC
                """,
                (code, f'%{strategy_pattern}%')
            ).fetchall()

            col_names = [
                'signal_date', 'resolved_date', 'entry_price', 'sl_price',
                'tp_price', 'exit_price', 'strategy', 'status'
            ]
            return [dict(zip(col_names, row)) for row in rows]
    except Exception as e:
        logger.error(f"查询历史止盈缺口失败 {code}: {e}")
        return []

"""
一次性工具: 将 gap_h2_backtest 的盈利交易导入 signal_archive
用于为"历史止盈缺口叠加"功能提供数据基础

使用方法:
    python tools/import_backtest_wins.py
"""
import sys
import os
import csv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.paths import ensure_importable
ensure_importable()

from core.signal_tracker import init_signal_archive
from core.database import get_db_connection


def _symbol_to_code(symbol: str) -> str:
    """将回测中的纯数字 symbol 转为 signal_archive 的 sh./sz. 格式"""
    s = str(symbol).strip()
    if s.startswith(('sh.', 'sz.')):
        return s
    if s.startswith('6'):
        return f'sh.{s}'
    else:
        return f'sz.{s}'


def import_backtest_wins(csv_path: str, dry_run: bool = False):
    """从 gap_h2_trades.csv 导入盈利交易(triggered=TP)到 signal_archive"""
    init_signal_archive()

    if not os.path.exists(csv_path):
        print(f"错误: 文件不存在: {csv_path}")
        return 0

    imported = 0
    skipped = 0

    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            pnl = float(row.get('pnl', row.get('pnl_pct', 0)))
            if pnl <= 0:
                skipped += 1
                continue

            symbol = row.get('symbol', '').strip()
            if not symbol:
                continue

            code = _symbol_to_code(symbol)
            entry_price = float(row.get('entry_price', 0))
            exit_price = float(row.get('exit_price', 0))
            entry_date = row.get('entry_date', '')
            exit_date = row.get('exit_date', '')

            # 优先使用新版 CSV 中的 sl/tp 列
            sl_price = float(row.get('sl', 0) or 0)
            tp_price = float(row.get('tp', 0) or 0)

            # 如果旧版 CSV 没有 sl/tp 列, 用 exit_price 作为 tp
            if tp_price == 0:
                tp_price = exit_price

            signal_id = f"BT_{code}_STRATEGY_GAP_H2_{entry_date}"

            if dry_run:
                print(f"  [预览] {code} | {entry_date} @ {entry_price:.2f} → "
                      f"{exit_date} @ {exit_price:.2f} | "
                      f"SL={sl_price:.2f} TP={tp_price:.2f}")
                imported += 1
                continue

            try:
                with get_db_connection() as conn:
                    conn.execute("""
                        INSERT OR IGNORE INTO signal_archive
                        (signal_id, code, name, strategy, timeframe, signal_date, scan_date,
                         entry_price, sl_price, tp_price, status, resolved_date, exit_price,
                         extra_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (signal_id, code, '', 'STRATEGY_GAP_H2', 'daily',
                          entry_date, entry_date,
                          entry_price, sl_price, tp_price,
                          'WIN', exit_date, exit_price,
                          '{"source": "backtest_import"}'))
                    conn.commit()
                    imported += 1
                    print(f"  导入 {code} | {entry_date} → {exit_date} | "
                          f"SL={sl_price:.2f} TP={tp_price:.2f}")
            except Exception as e:
                print(f"  失败 {code}: {e}")

    print(f"\n完成: 导入 {imported} 条, 跳过 {skipped} 条亏损")
    return imported


if __name__ == '__main__':
    csv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            'gap_h2_trades.csv')

    print("=== Gap H2 回测盈利交易导入 signal_archive ===\n")
    print(f"数据源: {csv_path}\n")

    print("[预览]")
    count = import_backtest_wins(csv_path, dry_run=True)
    print(f"\n共 {count} 条待导入")

    print("\n" + "=" * 50)
    try:
        resp = input("确认导入? (y/N): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        resp = 'n'

    if resp == 'y':
        print("\n[执行导入]")
        import_backtest_wins(csv_path, dry_run=False)
    else:
        print("已取消")

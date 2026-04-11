# -*- coding: utf-8 -*-
"""
Review Bridge 人工复盘桥接层 (core/review_bridge.py)

Phase 1: 建立结构化的人工复盘数据通道,
让交易者的主观判断力能够以标准格式注入系统。

核心价值:
  - 收集「负样本」(跳过的机会及理由)
  - 记录市场语境 (机器无法感知的宏观判断)
  - 标注亏损归因 (区分策略失效 vs 执行失误)
  - 捕获 PA 结构化判断 (市场状态、Always-In、真空、交易者方程)
  - 为后续 Phase 2 进化引擎提供人类判断维度
"""

import csv
import logging
import os
from datetime import datetime

from core.database import get_db_connection

logger = logging.getLogger(__name__)

# =====================================================================
# 建表 DDL — 基于真实复盘报告结构设计
# 覆盖 Al Brooks PA 五阶段复盘: 结构扫描→信号触发→过滤器→执行→持仓管理
# =====================================================================
TRADE_REVIEWS_DDL = """
CREATE TABLE IF NOT EXISTS trade_reviews (
    review_id       TEXT PRIMARY KEY,
    signal_id       TEXT,
    code            TEXT NOT NULL,
    market          TEXT DEFAULT 'CN',
    trade_date      TEXT NOT NULL,
    strategy        TEXT DEFAULT 'STRUCTURAL_GAP',
    direction       TEXT DEFAULT 'LONG',

    -- 阶段1: 结构扫描 (盘面地图)
    market_state    TEXT,
    structure_tf    TEXT,
    key_levels      TEXT,
    vacuum_check    TEXT,

    -- 阶段2: 信号与触发
    entry_tf        TEXT,
    signal_bar_note TEXT,
    micro_pattern   TEXT,
    pattern_tags    TEXT,
    pattern_combo   TEXT,
    momentum_type   TEXT,

    -- 阶段3: 过滤器 (裁判)
    always_in_dir   TEXT,
    trap_check      TEXT,
    planned_rr      REAL,

    -- 阶段4: 执行
    order_type      TEXT,
    entry_price     REAL,
    sl_price        REAL,
    tp_price        REAL,
    open_time       TEXT,

    -- 阶段5: 持仓管理
    exit_price      REAL,
    exit_type       TEXT,
    result          TEXT,
    final_r         REAL,
    close_time      TEXT,
    is_correct      TEXT,

    -- 元数据 (通用)
    context_tag     TEXT,
    entry_reason    TEXT,
    skip_reason     TEXT,
    execution_score INTEGER,
    lesson_tag      TEXT,
    review_report   TEXT,
    notes           TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""
TRADE_REVIEWS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_tr_code ON trade_reviews (code);",
    "CREATE INDEX IF NOT EXISTS idx_tr_date ON trade_reviews (trade_date);",
    "CREATE INDEX IF NOT EXISTS idx_tr_strategy ON trade_reviews (strategy);",
    "CREATE INDEX IF NOT EXISTS idx_tr_direction ON trade_reviews (direction);",
    "CREATE INDEX IF NOT EXISTS idx_tr_market_state ON trade_reviews (market_state);",
    "CREATE INDEX IF NOT EXISTS idx_tr_result ON trade_reviews (result);",
]

# =====================================================================
# 常量: 可选字段的合法值 (CLI 提示用)
# =====================================================================
VALID_MARKETS = ['CN', 'US', 'HK', 'CRYPTO', 'FUTURES', 'OTHER']
VALID_STRATEGIES = ['STRUCTURAL_GAP', 'MTR', '3K', 'GAP_MM', 'OTHER']
VALID_DIRECTIONS = ['LONG', 'SHORT', 'SKIP']

# Al Brooks 市场状态模型 (核心进化维度)
VALID_MARKET_STATES = [
    'STATE_1',     # 趋势突破 (缺口 + Buy Stop 跟随)
    'STATE_2',     # 趋势通道 (回调入场)
    'STATE_3',     # 横盘区间 (逆势交易)
    'STATE_4',     # 窄幅横盘 (等待突破)
    'TRANSITION',  # 状态转换中
]
VALID_ALWAYS_IN = ['LONG', 'SHORT', 'NEUTRAL']
VALID_ORDER_TYPES = ['BUY_STOP', 'SELL_STOP', 'LIMIT', 'MARKET']
VALID_EXIT_TYPES = ['TP_HIT', 'SL_HIT', 'MANUAL_EXIT', 'TRAIL_STOP', 'BREAKEVEN', 'TIMEOUT', 'N/A']
VALID_RESULTS = ['WIN', 'LOSS', 'BREAKEVEN', 'PARTIAL', 'SKIP']
VALID_MOMENTUM_TYPES = ['GAP', 'ORDINARY_GAP', 'MEASURING_GAP', 'EXHAUSTION_GAP', 'BARS', 'NONE']
VALID_CONTEXT_TAGS = ['TREND', 'RANGE', 'CHOPPY', 'NEWS', 'BREAKOUT', 'REVERSAL']
VALID_LESSON_TAGS = [
    'PERFECT_EXECUTION',   # 完美执行
    'FOMO',                # 追高冲动
    'PATIENCE',            # 耐心等待
    'SIZE_TOO_BIG',        # 仓位过重
    'SIZE_TOO_SMALL',      # 仓位过小
    'EARLY_EXIT',          # 过早离场
    'LATE_ENTRY',          # 入场太迟
    'IGNORED_SIGNAL',      # 忽略了信号
    'OVERTRADING',         # 过度交易
    'REVENGE_TRADE',       # 报复性交易
    'SYSTEM_FOLLOW',       # 严格执行系统
    'DISAPPOINTMENT',      # 失望信号出现
    'OTHER'
]


# =====================================================================
# 1. 初始化
# =====================================================================
def init_review_db():
    """初始化 trade_reviews 表 (幂等)"""
    try:
        with get_db_connection() as conn:
            conn.execute(TRADE_REVIEWS_DDL)
            for idx_sql in TRADE_REVIEWS_INDEXES:
                conn.execute(idx_sql)
            conn.commit()
            logger.debug("✅ trade_reviews 表初始化完成")
    except Exception as e:
        logger.error(f"trade_reviews 初始化失败: {e}")


# =====================================================================
# 2. 单条录入 (全量字段 API)
# =====================================================================
def add_review(code, trade_date, direction='LONG', market='CN',
               strategy='STRUCTURAL_GAP',
               # 阶段1: 结构扫描
               market_state='', structure_tf='', key_levels='', vacuum_check='',
               # 阶段2: 信号触发
               entry_tf='', signal_bar_note='', micro_pattern='',
               pattern_tags='', pattern_combo='', momentum_type='',
               # 阶段3: 过滤器
               always_in_dir='', trap_check='', planned_rr=0,
               # 阶段4: 执行
               order_type='', entry_price=0, sl_price=0, tp_price=0, open_time='',
               # 阶段5: 持仓管理
               exit_price=0, exit_type='', result='', final_r=0, close_time='', is_correct='',
               # 元数据
               context_tag='', entry_reason='', skip_reason='',
               execution_score=0, lesson_tag='', review_report='', notes='',
               signal_id='') -> str:
    """
    录入一条人工复盘记录 (覆盖五阶段 PA 结构)。

    Returns:
        review_id: 录入成功返回 ID, 失败返回 ''
    """
    init_review_db()

    review_id = f"RV_{code}_{market}_{trade_date}_{datetime.now().strftime('%H%M%S')}"

    try:
        with get_db_connection() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO trade_reviews
                (review_id, signal_id, code, market, trade_date, strategy, direction,
                 market_state, structure_tf, key_levels, vacuum_check,
                 entry_tf, signal_bar_note, micro_pattern, pattern_tags, pattern_combo, momentum_type,
                 always_in_dir, trap_check, planned_rr,
                 order_type, entry_price, sl_price, tp_price, open_time,
                 exit_price, exit_type, result, final_r, close_time, is_correct,
                 context_tag, entry_reason, skip_reason,
                 execution_score, lesson_tag, review_report, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?, ?)
            """, (review_id, signal_id, code, market, trade_date, strategy, direction,
                  market_state, structure_tf, key_levels, vacuum_check,
                  entry_tf, signal_bar_note, micro_pattern, pattern_tags, pattern_combo, momentum_type,
                  always_in_dir, trap_check, planned_rr,
                  order_type, entry_price, sl_price, tp_price, open_time,
                  exit_price, exit_type, result, final_r, close_time, is_correct,
                  context_tag, entry_reason, skip_reason,
                  execution_score, lesson_tag, review_report, notes))
            conn.commit()

            if conn.total_changes > 0:
                logger.info(f"📝 复盘录入: {code} [{direction}] {trade_date} "
                            f"R={final_r:+.2f}" if final_r else
                            f"📝 复盘录入: {code} [{direction}] {trade_date}")
                return review_id
            else:
                logger.debug(f"复盘已存在, 跳过: {review_id}")
                return review_id
    except Exception as e:
        logger.error(f"复盘录入失败 {code}: {e}")
        return ''


# =====================================================================
# 3. CSV 批量导入
# =====================================================================
def import_reviews_csv(csv_path: str) -> dict:
    """
    从 CSV 批量导入复盘记录。

    CSV 必需列: code, trade_date
    所有其他列均为可选, 自动映射到对应字段。

    Returns:
        dict: {'imported': N, 'skipped': N, 'errors': N}
    """
    init_review_db()
    stats = {'imported': 0, 'skipped': 0, 'errors': 0}

    if not os.path.exists(csv_path):
        logger.error(f"CSV 文件不存在: {csv_path}")
        return stats

    # add_review 接受的所有参数名
    valid_keys = {
        'code', 'trade_date', 'direction', 'market', 'strategy',
        'market_state', 'key_levels', 'vacuum_check',
        'signal_bar_note', 'micro_pattern', 'momentum_type',
        'always_in_dir', 'trap_check', 'planned_rr',
        'order_type', 'entry_price', 'sl_price', 'tp_price',
        'exit_price', 'exit_type', 'result', 'final_r',
        'context_tag', 'entry_reason', 'skip_reason',
        'execution_score', 'lesson_tag', 'notes', 'signal_id'
    }
    # 数值类型字段
    float_keys = {'planned_rr', 'entry_price', 'sl_price', 'tp_price', 'exit_price', 'final_r'}
    int_keys = {'execution_score'}

    try:
        with open(csv_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)

            for row in reader:
                code = row.get('code', '').strip()
                trade_date = row.get('trade_date', '').strip()

                if not code or not trade_date:
                    stats['errors'] += 1
                    continue

                # 动态构建参数字典
                kwargs = {}
                for k, v in row.items():
                    k_clean = k.strip()
                    if k_clean in valid_keys and v:
                        v_clean = v.strip()
                        if k_clean in float_keys:
                            try:
                                kwargs[k_clean] = float(v_clean)
                            except ValueError:
                                kwargs[k_clean] = 0
                        elif k_clean in int_keys:
                            try:
                                kwargs[k_clean] = int(v_clean)
                            except ValueError:
                                kwargs[k_clean] = 0
                        else:
                            kwargs[k_clean] = v_clean

                rid = add_review(**kwargs)
                if rid:
                    stats['imported'] += 1
                else:
                    stats['skipped'] += 1

    except Exception as e:
        logger.error(f"CSV 导入异常: {e}")

    logger.info(f"📥 CSV 导入完成: 成功 {stats['imported']} | "
                f"跳过 {stats['skipped']} | 错误 {stats['errors']}")
    return stats


# =====================================================================
# 4. 查看复盘记录
# =====================================================================
def list_reviews(limit=20, market=None, strategy=None) -> list:
    """查看已录入的复盘记录 (最新在前)"""
    init_review_db()

    try:
        with get_db_connection() as conn:
            conditions = []
            params = []

            if market:
                conditions.append("market = ?")
                params.append(market)
            if strategy:
                conditions.append("strategy = ?")
                params.append(strategy)

            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            sql = f"SELECT * FROM trade_reviews {where} ORDER BY trade_date DESC, created_at DESC LIMIT ?"
            params.append(limit)

            col_names = [desc[0] for desc in conn.execute("SELECT * FROM trade_reviews LIMIT 0").description]
            rows = conn.execute(sql, params).fetchall()

            return [dict(zip(col_names, row)) for row in rows]
    except Exception as e:
        logger.error(f"查询复盘记录失败: {e}")
        return []


# =====================================================================
# 5. 统计概览
# =====================================================================
def review_summary() -> dict:
    """生成复盘数据统计概览"""
    init_review_db()

    try:
        with get_db_connection() as conn:
            total = conn.execute("SELECT COUNT(*) FROM trade_reviews").fetchone()[0]
            if total == 0:
                return {'total': 0, 'message': '暂无复盘数据'}

            # 按方向统计
            directions = conn.execute(
                "SELECT direction, COUNT(*) FROM trade_reviews GROUP BY direction"
            ).fetchall()

            # 按市场统计
            markets = conn.execute(
                "SELECT market, COUNT(*) FROM trade_reviews GROUP BY market"
            ).fetchall()

            # 按市场状态统计 (PA 核心维度)
            states = conn.execute(
                "SELECT market_state, COUNT(*), "
                "  SUM(CASE WHEN result = 'WIN' THEN 1 ELSE 0 END) as wins "
                "FROM trade_reviews "
                "WHERE market_state != '' AND market_state IS NOT NULL "
                "GROUP BY market_state ORDER BY COUNT(*) DESC"
            ).fetchall()

            # 平均 R 值 (去掉 0 值)
            avg_r = conn.execute(
                "SELECT AVG(final_r) FROM trade_reviews "
                "WHERE final_r != 0 AND final_r IS NOT NULL"
            ).fetchone()[0]

            # 按经验标签统计 (只统计非空)
            lessons = conn.execute(
                "SELECT lesson_tag, COUNT(*) FROM trade_reviews "
                "WHERE lesson_tag != '' AND lesson_tag IS NOT NULL "
                "GROUP BY lesson_tag ORDER BY COUNT(*) DESC"
            ).fetchall()

            # 执行质量平均分
            avg_score = conn.execute(
                "SELECT AVG(execution_score) FROM trade_reviews "
                "WHERE execution_score > 0"
            ).fetchone()[0]

            # 负样本统计 (SKIP)
            skip_count = conn.execute(
                "SELECT COUNT(*) FROM trade_reviews WHERE direction = 'SKIP'"
            ).fetchone()[0]

            return {
                'total': total,
                'by_direction': dict(directions),
                'by_market': dict(markets),
                'by_market_state': [(s[0], s[1], s[2]) for s in states] if states else [],
                'avg_final_r': round(avg_r, 2) if avg_r else 0,
                'top_lessons': dict(lessons[:5]) if lessons else {},
                'avg_execution_score': round(avg_score, 1) if avg_score else 0,
                'skip_count': skip_count,
                'skip_pct': round(skip_count / total * 100, 1) if total > 0 else 0,
            }
    except Exception as e:
        logger.error(f"统计概览失败: {e}")
        return {'total': 0, 'error': str(e)}


# =====================================================================
# 6. 交互式 CLI 录入
# =====================================================================
def run_review_cli():
    """交互式 CLI 复盘录入 (hunter.py 菜单选项 5)"""
    init_review_db()

    print("\n" + "═" * 50)
    print("  📝 人工复盘录入系统 (Review Bridge V1.0)")
    print("═" * 50)
    print("  1. ✏️  录入单条复盘 (五阶段 PA 结构)")
    print("  2. 📥 批量导入 CSV")
    print("  3. 📊 查看复盘统计")
    print("  4. 📋 查看最近复盘记录")
    print("═" * 50)

    try:
        choice = input("  请选择 (默认 1): ").strip() or '1'
    except (EOFError, KeyboardInterrupt):
        return

    if choice == '1':
        _cli_add_single_review()
    elif choice == '2':
        _cli_import_csv()
    elif choice == '3':
        _cli_show_summary()
    elif choice == '4':
        _cli_show_recent()


def _input(prompt, default=''):
    """安全输入, 支持默认值"""
    try:
        val = input(prompt).strip()
        return val if val else default
    except (EOFError, KeyboardInterrupt):
        return default


def _input_float(prompt, default=0):
    """安全输入浮点数"""
    val = _input(prompt)
    if not val:
        return default
    try:
        return float(val)
    except ValueError:
        return default


def _cli_add_single_review():
    """交互式单条录入 (五阶段 PA 结构)"""
    print("\n  ╔══════════════════════════════════════════╗")
    print("  ║   五阶段 PA 结构化复盘 (带 * 为必填)    ║")
    print("  ╚══════════════════════════════════════════╝")

    try:
        # ── 基础信息 ──
        code = _input("  * 标的代码 (如 ES / NQ / sh.600519): ")
        if not code:
            print("  ❌ 代码不能为空"); return

        trade_date = _input(f"  * 交易日期 (默认今天): ",
                            datetime.now().strftime('%Y-%m-%d'))

        print(f"    市场: {', '.join(VALID_MARKETS)}")
        market = _input("    市场 (默认 FUTURES): ", 'FUTURES').upper()

        print(f"    策略: {', '.join(VALID_STRATEGIES)}")
        strategy = _input("    策略 (默认 GAP_MM): ", 'GAP_MM').upper()

        print(f"    方向: LONG / SHORT / SKIP")
        direction = _input("    方向 (默认 LONG): ", 'LONG').upper()

        if direction == 'SKIP':
            # 负样本快速通道
            skip_reason = _input("  * 跳过理由 (核心负样本!): ")
            context_tag = _input("    市场语境: ")
            notes = _input("    备注: ")
            rid = add_review(code=code, trade_date=trade_date,
                             direction='SKIP', market=market, strategy=strategy,
                             skip_reason=skip_reason, context_tag=context_tag,
                             notes=notes)
            print(f"  ✅ 负样本录入! ID: {rid}" if rid else "  ❌ 录入失败")
            return

        # ── 阶段1: 结构扫描 ──
        print(f"\n  ── 阶段1: 结构扫描 (盘面地图) ──")
        print(f"    市场状态: {', '.join(VALID_MARKET_STATES)}")
        market_state = _input("    入场时市场状态: ").upper()
        key_levels = _input("    关键价位 (如 支撑2128/MM目标2159): ")
        vacuum_check = _input("    真空检查 (如 入场前有真空推进): ")

        # ── 阶段2: 信号与触发 ──
        print(f"\n  ── 阶段2: 信号与触发 ──")
        signal_bar_note = _input("    信号K线备注 (强弱/形态): ")
        micro_pattern = _input("    微观形态 (如 突破缺口延续): ")
        print(f"    动能类型: {', '.join(VALID_MOMENTUM_TYPES)}")
        momentum_type = _input("    动能类型: ").upper()

        # ── 阶段3: 过滤器 ──
        print(f"\n  ── 阶段3: 过滤器 (裁判) ──")
        print(f"    Always-In: {', '.join(VALID_ALWAYS_IN)}")
        always_in_dir = _input("    持续偏向: ").upper()
        trap_check = _input("    陷阱检查 (如 出现失望信号): ")
        planned_rr = _input_float("    计划盈亏比 (如 1.05): ")

        # ── 阶段4: 执行 ──
        print(f"\n  ── 阶段4: 执行 (订单) ──")
        print(f"    订单类型: {', '.join(VALID_ORDER_TYPES)}")
        order_type = _input("    订单类型 (默认 BUY_STOP): ", 'BUY_STOP').upper()
        entry_price = _input_float("    入场价: ")
        sl_price = _input_float("    止损价: ")
        tp_price = _input_float("    目标价: ")

        # ── 阶段5: 持仓管理 ──
        print(f"\n  ── 阶段5: 持仓管理 ──")
        exit_price = _input_float("    平仓价 (0=未平仓): ")
        print(f"    离场类型: {', '.join(VALID_EXIT_TYPES)}")
        exit_type = _input("    离场类型: ").upper()
        print(f"    结果: {', '.join(VALID_RESULTS)}")
        result = _input("    结果: ").upper()
        final_r = _input_float("    最终R值 (如 0.62 / -1.0): ")

        # ── 元数据 ──
        print(f"\n  ── 经验总结 ──")
        execution_score = int(_input_float("    执行质量 (1-5): "))
        print(f"    经验标签: {', '.join(VALID_LESSON_TAGS[:6])}...")
        lesson_tag = _input("    经验标签: ").upper()
        notes = _input("    备注: ")

        # ── 确认 ──
        print(f"\n  {'─' * 45}")
        print(f"  📋 确认录入:")
        print(f"     {code} | {trade_date} | {market} | {direction}")
        print(f"     状态: {market_state} | Always-In: {always_in_dir}")
        if entry_price:
            print(f"     入场: {entry_price} | 止损: {sl_price} | 目标: {tp_price}")
        if exit_price:
            print(f"     平仓: {exit_price} | 结果: {result} | R: {final_r:+.2f}")
        print(f"  {'─' * 45}")

        confirm = _input("  确认录入? (Y/n): ", 'y').lower()
        if confirm == 'n':
            print("  已取消"); return

        rid = add_review(
            code=code, trade_date=trade_date, direction=direction,
            market=market, strategy=strategy,
            market_state=market_state, key_levels=key_levels,
            vacuum_check=vacuum_check,
            signal_bar_note=signal_bar_note, micro_pattern=micro_pattern,
            momentum_type=momentum_type,
            always_in_dir=always_in_dir, trap_check=trap_check,
            planned_rr=planned_rr,
            order_type=order_type, entry_price=entry_price,
            sl_price=sl_price, tp_price=tp_price,
            exit_price=exit_price, exit_type=exit_type,
            result=result, final_r=final_r,
            execution_score=execution_score, lesson_tag=lesson_tag,
            notes=notes
        )

        if rid:
            print(f"  ✅ 录入成功! ID: {rid}")
        else:
            print(f"  ❌ 录入失败")

    except (EOFError, KeyboardInterrupt):
        print("\n  已取消")


def _cli_import_csv():
    """交互式 CSV 导入"""
    print("\n  --- CSV 批量导入 ---")
    print("  必需列: code, trade_date")
    print("  PA 阶段列: market_state, always_in_dir, planned_rr,")
    print("             entry_price, sl_price, tp_price, exit_price,")
    print("             exit_type, result, final_r, ...")
    print("  元数据列: context_tag, entry_reason, skip_reason,")
    print("            execution_score, lesson_tag, notes")

    try:
        csv_path = _input("\n  请输入 CSV 文件路径: ")
        if not csv_path:
            print("  ❌ 路径不能为空"); return
        csv_path = csv_path.strip('"').strip("'")
        if not os.path.exists(csv_path):
            print(f"  ❌ 文件不存在: {csv_path}"); return

        stats = import_reviews_csv(csv_path)
        print(f"\n  ✅ 导入完成:")
        print(f"     成功: {stats['imported']} 条")
        print(f"     跳过: {stats['skipped']} 条")
        print(f"     错误: {stats['errors']} 条")
    except (EOFError, KeyboardInterrupt):
        print("\n  已取消")


def _cli_show_summary():
    """显示复盘统计概览"""
    summary = review_summary()

    print("\n" + "═" * 55)
    print("  📊 复盘数据统计概览")
    print("═" * 55)

    if summary.get('total', 0) == 0:
        print("  暂无复盘数据，请先录入。")
        return

    print(f"  总复盘数: {summary['total']} 条")
    print(f"  负样本 (SKIP): {summary.get('skip_count', 0)} 条 "
          f"({summary.get('skip_pct', 0)}%)")

    if summary.get('avg_final_r', 0) != 0:
        print(f"  平均 R 值: {summary['avg_final_r']:+.2f}R")
    if summary.get('avg_execution_score', 0) > 0:
        print(f"  平均执行质量: {summary['avg_execution_score']}/5")

    # 市场状态维度 (PA 核心)
    by_state = summary.get('by_market_state', [])
    if by_state:
        print(f"\n  按市场状态 (进化引擎核心维度):")
        for state, cnt, wins in by_state:
            wr = wins / cnt * 100 if cnt > 0 else 0
            print(f"    {state}: {cnt}笔 | 胜率 {wr:.0f}%")

    by_dir = summary.get('by_direction', {})
    if by_dir:
        print(f"\n  按方向:")
        for d, cnt in by_dir.items():
            print(f"    {d}: {cnt} 条")

    by_mkt = summary.get('by_market', {})
    if by_mkt:
        print(f"\n  按市场:")
        for m, cnt in by_mkt.items():
            print(f"    {m}: {cnt} 条")

    top_lessons = summary.get('top_lessons', {})
    if top_lessons:
        print(f"\n  高频经验标签 TOP 5:")
        for tag, cnt in top_lessons.items():
            print(f"    {tag}: {cnt} 次")

    print("═" * 55)


def _cli_show_recent():
    """显示最近复盘记录"""
    reviews = list_reviews(limit=10)

    print("\n" + "═" * 65)
    print("  📋 最近 10 条复盘记录")
    print("═" * 65)

    if not reviews:
        print("  暂无复盘数据。")
        return

    for r in reviews:
        direction = r.get('direction', '?')
        icon = '📈' if direction == 'LONG' else ('📉' if direction == 'SHORT' else '⏸️')
        code = r.get('code', '?')
        date = r.get('trade_date', '?')
        market = r.get('market', '?')
        strategy = r.get('strategy', '?')
        state = r.get('market_state', '')
        final_r = r.get('final_r', 0)
        result = r.get('result', '')

        # 第一行: 基础信息
        r_str = f"R={final_r:+.2f}" if final_r else ""
        result_str = f"[{result}]" if result else ""
        print(f"  {icon} {code} | {date} | {market} | {strategy} {result_str} {r_str}")

        # 第二行: PA 结构 (如果有)
        pa_parts = []
        if state:
            pa_parts.append(f"状态:{state}")
        if r.get('always_in_dir'):
            pa_parts.append(f"AI:{r['always_in_dir']}")
        if r.get('planned_rr') and r['planned_rr'] > 0:
            pa_parts.append(f"RR:{r['planned_rr']:.2f}")
        if pa_parts:
            print(f"     PA: {' | '.join(pa_parts)}")

        # 第三行: 入场/跳过理由
        if r.get('entry_reason'):
            print(f"     理由: {r['entry_reason'][:60]}")
        if r.get('skip_reason'):
            print(f"     跳过: {r['skip_reason'][:60]}")
        if r.get('trap_check'):
            print(f"     陷阱: {r['trap_check'][:60]}")
        if r.get('lesson_tag'):
            print(f"     经验: {r['lesson_tag']}")
        print()

    print("═" * 65)

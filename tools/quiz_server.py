"""
PA 图表读图训练 — Flask REST API 服务器

功能:
    1. 从 signal_archive 表随机抽取已解决的交易信号
    2. 生成"盲图"(隐藏未来数据) 供用户判断方向
    3. 揭示答案图 (含策略标注 + SL/TP 线)
    4. 记录用户判断 & 详细复盘标注
    5. 统计训练准确率 & 系统校准度
"""

import sys
import os

# === Windows 控制台 UTF-8 ===
sys.stdout.reconfigure(encoding='utf-8')

# === 项目根目录注入 (唯一一处 sys.path.insert) ===
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import base64
import random
import logging
from datetime import datetime
from typing import Optional

# 设置 matplotlib 后端 (必须在任何 matplotlib 导入之前)
import matplotlib
matplotlib.use('Agg')

from flask import Flask, request, jsonify, send_file

from config import settings
from core.database import get_db_connection
from core.data_provider import get_stock_data, get_stock_name
from core.calculator import add_indicators
from core.strategy_registry import StrategyRegistry
from core.review_bridge import add_review
from tools.notifier import generate_chart_bytes

logger = logging.getLogger(__name__)

# =========================================================================
# Flask 应用
# =========================================================================
app = Flask(__name__)

# =========================================================================
# 会话统计 (内存级，重启清零)
# =========================================================================
_session: dict = {
    'total': 0,        # 总判断次数
    'correct': 0,      # 正确次数
    'history': [],     # [{signal_id, user_direction, actual_status, correct}, ...]
    'streak': 0,       # 当前连对
    'max_streak': 0,   # 最长连对
}


# =========================================================================
# 工具函数
# =========================================================================
def _encode_chart(chart_bytes) -> Optional[str]:
    """将图表 BytesIO 编码为 base64 字符串。

    Args:
        chart_bytes: generate_chart_bytes 返回的 io.BytesIO 或 None

    Returns:
        base64 编码的 PNG 字符串，失败返回 None
    """
    if chart_bytes is None:
        return None
    chart_bytes.seek(0)
    return base64.b64encode(chart_bytes.read()).decode('utf-8')


def _row_to_dict(cursor, row) -> dict:
    """将 SQLite 游标的一行转为 dict（不修改 conn.row_factory，避免污染连接池）。"""
    cols = [d[0] for d in cursor.description]
    return dict(zip(cols, row))


def _fetchone_dict(conn, sql: str, params=()) -> Optional[dict]:
    """执行查询并返回第一行 dict，无结果返回 None。"""
    cursor = conn.execute(sql, params)
    row = cursor.fetchone()
    return _row_to_dict(cursor, row) if row else None


def _fetchall_dict(conn, sql: str, params=()) -> list[dict]:
    """执行查询并返回所有行的 dict 列表。"""
    cursor = conn.execute(sql, params)
    rows = cursor.fetchall()
    return [_row_to_dict(cursor, r) for r in rows]


def _query_signal(signal_id: str) -> Optional[dict]:
    """按 signal_id 查询 signal_archive 单条记录。

    Args:
        signal_id: 信号唯一标识

    Returns:
        dict 形式的行数据，未找到返回 None
    """
    try:
        with get_db_connection() as conn:
            return _fetchone_dict(conn,
                "SELECT * FROM signal_archive WHERE signal_id = ?",
                (signal_id,)
            )
    except Exception as e:
        logger.error(f"[quiz] 查询信号失败 signal_id={signal_id}: {e}")
        return None


def _judge_correctness(user_direction: str, actual_status: str) -> bool:
    """判断用户方向预测是否正确。

    规则:
        - 做多(LONG) + WIN → 正确
        - 观望(SKIP) + LOSS/INVALIDATED/EXPIRED → 正确
        - 其余 → 错误

    Args:
        user_direction: 用户选择 ('LONG' / 'SKIP')
        actual_status: 信号实际状态

    Returns:
        是否判断正确
    """
    if user_direction == 'LONG' and actual_status == 'WIN':
        return True
    if user_direction == 'SKIP' and actual_status in ('LOSS', 'INVALIDATED', 'EXPIRED'):
        return True
    return False


# =========================================================================
# CORS 支持 (同源也加，方便本地开发)
# =========================================================================
@app.after_request
def _add_cors_headers(response):
    """为所有响应添加 CORS 头。"""
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    return response


# =========================================================================
# 1. GET / — 前端页面
# =========================================================================
@app.route('/')
def index():
    """提供前端 quiz_app.html 页面。"""
    return send_file(os.path.join(os.path.dirname(__file__), 'quiz_app.html'))


# =========================================================================
# 2. GET /api/quiz/random — 随机出题
# =========================================================================
@app.route('/api/quiz/random')
def quiz_random():
    """随机抽取一道读图测验题。

    Query Params:
        strategy: 可选，按策略过滤
        status: 可选，逗号分隔的状态列表，默认 'WIN,LOSS'
        only_annotated: 可选，仅返回已有 human_review 的信号

    Returns:
        JSON: signal_id, code, name, strategy, timeframe,
              signal_date, blind_chart (base64), total_available
    """
    # --- 解析查询参数 ---
    strategy_filter = request.args.get('strategy', '')
    status_csv = request.args.get('status', 'WIN,LOSS')
    only_annotated = request.args.get('only_annotated', '').lower() == 'true'
    statuses = [s.strip() for s in status_csv.split(',') if s.strip()]

    try:
        with get_db_connection() as conn:
            # --- 构建查询 (排除回测信号: signal_date 长度 > 5) ---
            where_parts = ["length(signal_date) > 5"]
            params: list = []

            if statuses:
                placeholders = ','.join('?' * len(statuses))
                where_parts.append(f"status IN ({placeholders})")
                params.extend(statuses)

            if strategy_filter:
                where_parts.append("strategy = ?")
                params.append(strategy_filter)

            if only_annotated:
                where_parts.append("extra_json LIKE '%human_review%'")

            where_clause = ' AND '.join(where_parts)

            # 统计总可用数
            count_row = conn.execute(
                f"SELECT COUNT(*) FROM signal_archive WHERE {where_clause}",
                params
            ).fetchone()
            total_available = count_row[0] if count_row else 0

            if total_available == 0:
                return jsonify({'error': '没有符合条件的信号', 'total_available': 0}), 404

            # 随机抽取一条
            offset = random.randint(0, total_available - 1)
            signal = _fetchone_dict(conn,
                f"SELECT * FROM signal_archive WHERE {where_clause} LIMIT 1 OFFSET ?",
                params + [offset]
            )

    except Exception as e:
        logger.error(f"[quiz] 随机查询失败: {e}")
        return jsonify({'error': f'数据库查询失败: {e}'}), 500

    if not signal:
        return jsonify({'error': '未找到信号'}), 404

    code = signal['code']
    signal_date = signal['signal_date']
    name = signal.get('name') or get_stock_name(code)
    strategy_name = signal['strategy']

    # --- 获取行情数据 & 生成盲图 ---
    try:
        df = get_stock_data(code, limit=500)
        if df is None or df.empty:
            return jsonify({'error': f'{code} 无行情数据'}), 404

        # 关键: 隐藏信号日之后的数据 (盲图)
        df = df[df['date'] <= signal_date].copy()
        if len(df) < 20:
            return jsonify({'error': f'{code} 信号日前数据不足'}), 404

        # 添加技术指标 (不调用策略的 calculate_signals，保持盲图)
        df = add_indicators(df)

        # 取最近 120 根 K 线绘图
        plot_df = df.tail(120).copy()

        chart_buf = generate_chart_bytes(
            code, name, strategy_name,
            sl_price=0, tp1=0, tp2=0,
            reason='', df_override=plot_df
        )
        blind_chart = _encode_chart(chart_buf)

    except Exception as e:
        logger.error(f"[quiz] {code} 盲图生成失败: {e}")
        blind_chart = None

    return jsonify({
        'signal_id': signal['signal_id'],
        'code': code,
        'name': name,
        'strategy': strategy_name,
        'timeframe': signal.get('timeframe', 'daily'),
        'signal_date': signal_date,
        'blind_chart': blind_chart,
        'total_available': total_available,
    })


# =========================================================================
# 3. GET /api/quiz/reveal — 揭示答案
# =========================================================================
@app.route('/api/quiz/reveal')
def quiz_reveal():
    """揭示指定信号的完整答案图 (含策略标注 + SL/TP)。

    Query Params:
        signal_id: 必填，信号唯一标识

    Returns:
        JSON: 完整信号详情 + answer_chart (base64)
    """
    signal_id = request.args.get('signal_id', '')
    if not signal_id:
        return jsonify({'error': '缺少 signal_id 参数'}), 400

    signal = _query_signal(signal_id)
    if not signal:
        return jsonify({'error': f'信号 {signal_id} 不存在'}), 404

    code = signal['code']
    signal_date = signal['signal_date']
    name = signal.get('name') or get_stock_name(code)
    strategy_name = signal['strategy']
    sl_price = signal.get('sl_price', 0) or 0
    tp_price = signal.get('tp_price', 0) or 0

    # --- 答案图: 包含信号日之后最多 40 根 K 线 ---
    try:
        df = get_stock_data(code, limit=500)
        if df is None or df.empty:
            return jsonify({'error': f'{code} 无行情数据'}), 404

        # 找到信号日在 df 中的位置
        sig_mask = df['date'] <= signal_date
        sig_iloc = sig_mask.sum()  # 信号日(含)之前的行数

        # 取到信号日 + 40 根 K 线 (或到最新数据)
        end_iloc = min(sig_iloc + 40, len(df))
        df = df.iloc[:end_iloc].copy()

        # 添加技术指标 + 策略计算 (完整标注)
        df = add_indicators(df)
        try:
            strat = StrategyRegistry.get_strategy(strategy_name)
            df = strat.calculate_signals(df)
        except Exception as e:
            logger.warning(f"[quiz] {code} 策略计算失败，使用纯指标图: {e}")

        plot_df = df.tail(120).copy()

        # 解析 ev_rating
        ev_rating = signal.get('ev_rating')

        chart_buf = generate_chart_bytes(
            code, name, strategy_name,
            sl_price=sl_price, tp1=tp_price, tp2=0,
            reason='', df_override=plot_df,
            ev_rating=ev_rating
        )
        answer_chart = _encode_chart(chart_buf)

    except Exception as e:
        logger.error(f"[quiz] {code} 答案图生成失败: {e}")
        answer_chart = None

    # 解析 extra_json
    extra = {}
    if signal.get('extra_json'):
        try:
            extra = json.loads(signal['extra_json'])
        except (json.JSONDecodeError, TypeError):
            pass

    return jsonify({
        'signal_id': signal['signal_id'],
        'code': code,
        'name': name,
        'strategy': strategy_name,
        'timeframe': signal.get('timeframe', 'daily'),
        'signal_date': signal_date,
        'entry_price': signal.get('entry_price', 0),
        'sl_price': sl_price,
        'tp_price': tp_price,
        'rr_ratio': signal.get('rr_ratio', 0),
        'ev_rating': signal.get('ev_rating', ''),
        'ev_score': signal.get('ev_score', 0),
        'status': signal.get('status', ''),
        'exit_price': signal.get('exit_price', 0),
        'max_favorable': signal.get('max_favorable', 0),
        'max_adverse': signal.get('max_adverse', 0),
        'bars_to_resolve': signal.get('bars_to_resolve', 0),
        'extra': extra,
        'answer_chart': answer_chart,
    })


# =========================================================================
# 4. POST /api/quiz/judge — 用户判断 (揭示前)
# =========================================================================
@app.route('/api/quiz/judge', methods=['POST'])
def quiz_judge():
    """记录用户的方向判断并返回正误结果。

    Body JSON:
        signal_id: 信号 ID
        direction: 用户选择 ('LONG' / 'SKIP')

    Returns:
        JSON: correct (bool), status (实际状态), streak, total, accuracy
    """
    data = request.get_json(silent=True) or {}
    signal_id = data.get('signal_id', '')
    direction = data.get('direction', '').upper()

    if not signal_id or direction not in ('LONG', 'SKIP'):
        return jsonify({'error': '参数无效，需要 signal_id 和 direction(LONG/SKIP)'}), 400

    signal = _query_signal(signal_id)
    if not signal:
        return jsonify({'error': f'信号 {signal_id} 不存在'}), 404

    actual_status = signal.get('status', '')
    correct = _judge_correctness(direction, actual_status)

    # --- 更新会话统计 ---
    _session['total'] += 1
    if correct:
        _session['correct'] += 1
        _session['streak'] += 1
        _session['max_streak'] = max(_session['max_streak'], _session['streak'])
    else:
        _session['streak'] = 0

    _session['history'].append({
        'signal_id': signal_id,
        'user_direction': direction,
        'actual_status': actual_status,
        'correct': correct,
    })

    accuracy = round(_session['correct'] / _session['total'] * 100, 1) if _session['total'] > 0 else 0

    return jsonify({
        'correct': correct,
        'status': actual_status,
        'streak': _session['streak'],
        'max_streak': _session['max_streak'],
        'total': _session['total'],
        'accuracy': accuracy,
    })


# =========================================================================
# 5. POST /api/quiz/annotate — 详细复盘标注 (揭示后)
# =========================================================================
@app.route('/api/quiz/annotate', methods=['POST'])
def quiz_annotate():
    """提交人工复盘标注，写入 signal_archive.extra_json 和 trade_reviews 表。

    Body JSON:
        signal_id: 信号 ID (必填)
        user_direction: 用户方向 ('LONG' / 'SKIP')
        user_quality: 信号质量 ('good' / 'neutral' / 'bad')
        human_rating: 人工评级 ('A+' / 'A' / 'B' / 'C' / 'D')
        lesson_tag: 经验教训标签
        notes: 备注

    Returns:
        JSON: success, review_id
    """
    data = request.get_json(silent=True) or {}
    signal_id = data.get('signal_id', '')
    if not signal_id:
        return jsonify({'error': '缺少 signal_id'}), 400

    signal = _query_signal(signal_id)
    if not signal:
        return jsonify({'error': f'信号 {signal_id} 不存在'}), 404

    # --- 构建 human_review 对象 ---
    human_review = {
        'quality': data.get('user_quality', 'neutral'),
        'direction': data.get('user_direction', ''),
        'human_rating': data.get('human_rating', ''),
        'lesson_tag': data.get('lesson_tag', ''),
        'notes': data.get('notes', ''),
        'reviewed_at': datetime.now().isoformat(timespec='seconds'),
    }

    # --- 合并到 extra_json ---
    extra = {}
    if signal.get('extra_json'):
        try:
            extra = json.loads(signal['extra_json'])
        except (json.JSONDecodeError, TypeError):
            pass

    extra['human_review'] = human_review
    new_extra_json = json.dumps(extra, ensure_ascii=False)

    try:
        with get_db_connection() as conn:
            conn.execute(
                "UPDATE signal_archive SET extra_json = ? WHERE signal_id = ?",
                (new_extra_json, signal_id)
            )
            conn.commit()
    except Exception as e:
        logger.error(f"[quiz] 更新 extra_json 失败 signal_id={signal_id}: {e}")
        return jsonify({'error': f'数据库更新失败: {e}'}), 500

    # --- 同步写入 trade_reviews 表 ---
    review_id = ''
    try:
        code = signal['code']
        review_id = add_review(
            code=code,
            trade_date=signal.get('signal_date', ''),
            direction=data.get('user_direction', 'LONG'),
            strategy=signal.get('strategy', ''),
            lesson_tag=data.get('lesson_tag', ''),
            notes=data.get('notes', ''),
            signal_id=signal_id,
            result=signal.get('status', ''),
        )
    except Exception as e:
        logger.error(f"[quiz] add_review 失败 signal_id={signal_id}: {e}")

    return jsonify({
        'success': True,
        'review_id': review_id,
        'signal_id': signal_id,
    })


# =========================================================================
# 6. GET /api/quiz/stats — 训练统计
# =========================================================================
@app.route('/api/quiz/stats')
def quiz_stats():
    """返回本次训练会话的统计数据 + 系统校准度分析。

    Returns:
        JSON: session (统计), calibration (各评级校准), total_annotations
    """
    accuracy = round(
        _session['correct'] / _session['total'] * 100, 1
    ) if _session['total'] > 0 else 0

    session_data = {
        'total': _session['total'],
        'correct': _session['correct'],
        'accuracy': accuracy,
        'streak': _session['streak'],
        'max_streak': _session['max_streak'],
        'recent_10': _session['history'][-10:],
    }

    # --- 校准度分析: 各 ev_rating 下人工评审的一致性 ---
    calibration: dict = {}
    total_annotations = 0

    try:
        with get_db_connection() as conn:
            rows = _fetchall_dict(conn, """
                SELECT ev_rating, status, extra_json
                FROM signal_archive
                WHERE extra_json LIKE '%human_review%'
            """)

            total_annotations = len(rows)

            # 按 ev_rating 分组统计
            rating_groups: dict = {}
            for row in rows:
                ev = row.get('ev_rating') or 'UNKNOWN'
                if ev not in rating_groups:
                    rating_groups[ev] = {'total': 0, 'agree': 0}

                rating_groups[ev]['total'] += 1

                # 解析 human_review 中的 quality
                try:
                    extra = json.loads(row.get('extra_json', '{}'))
                    quality = extra.get('human_review', {}).get('quality', '')
                    status = row.get('status', '')

                    # 校准: 如果人工认为 good 且结果 WIN，或 bad 且 LOSS → agree
                    if (quality == 'good' and status == 'WIN') or \
                       (quality == 'bad' and status in ('LOSS', 'INVALIDATED')):
                        rating_groups[ev]['agree'] += 1
                except (json.JSONDecodeError, TypeError):
                    pass

            for ev, counts in rating_groups.items():
                agree_pct = round(counts['agree'] / counts['total'] * 100, 1) if counts['total'] > 0 else 0
                calibration[ev] = {
                    'total': counts['total'],
                    'agree': counts['agree'],
                    'agree_pct': agree_pct,
                }

    except Exception as e:
        logger.error(f"[quiz] 校准度查询失败: {e}")

    return jsonify({
        'session': session_data,
        'calibration': calibration,
        'total_annotations': total_annotations,
    })


# =========================================================================
# 7. GET /api/quiz/filters — 可用过滤选项
# =========================================================================
@app.route('/api/quiz/filters')
def quiz_filters():
    """返回 signal_archive 中可用的过滤维度。

    Returns:
        JSON: strategies, statuses, ev_ratings (各自为去重列表)
    """
    strategies: list[str] = []
    statuses: list[str] = []
    ev_ratings: list[str] = []

    try:
        with get_db_connection() as conn:
            # 去重查询各维度
            rows = conn.execute(
                "SELECT DISTINCT strategy FROM signal_archive WHERE length(signal_date) > 5 ORDER BY strategy"
            ).fetchall()
            strategies = [r[0] for r in rows if r[0]]

            rows = conn.execute(
                "SELECT DISTINCT status FROM signal_archive ORDER BY status"
            ).fetchall()
            statuses = [r[0] for r in rows if r[0]]

            rows = conn.execute(
                "SELECT DISTINCT ev_rating FROM signal_archive WHERE ev_rating IS NOT NULL ORDER BY ev_rating"
            ).fetchall()
            ev_ratings = [r[0] for r in rows if r[0]]

    except Exception as e:
        logger.error(f"[quiz] 过滤选项查询失败: {e}")

    return jsonify({
        'strategies': strategies,
        'statuses': statuses,
        'ev_ratings': ev_ratings,
    })


# =========================================================================
# 启动入口
# =========================================================================
def _get_lan_ip() -> str:
    """获取本机局域网 IP，供手机端访问。"""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'


if __name__ == '__main__':
    lan_ip = _get_lan_ip()
    print("=" * 60)
    print("  📊 PA 读图训练服务器启动")
    print(f"  🖥️  电脑访问: http://localhost:5000")
    print(f"  📱 手机访问: http://{lan_ip}:5000")
    print(f"  📁 数据库: {getattr(settings, 'DB_PATH', 'N/A')}")
    print("=" * 60)
    app.run(host='0.0.0.0', port=5000, debug=False)

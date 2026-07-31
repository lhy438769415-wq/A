# core/scanner.py
import traceback
import logging
import time
import warnings
import pandas as pd
# 🟢 Suppress FutureWarnings (e.g., from pandas internals)
warnings.simplefilter(action='ignore', category=FutureWarning)

# 🟢 [Phase1] 统一数据层：直接使用 core.data_provider，不经 tools.data_manager 薄代理
from core.data_provider import get_stock_data
from core.calculator import add_indicators, calculate_targets
from core.strategy_registry import StrategyRegistry
from typing import Optional, Dict, Any, List
from config import settings
from core.log_config import get_logger

# 配置日志
logger = get_logger(__name__)


def _prepare_df(code: str) -> Optional[pd.DataFrame]:
    """[共享] 取数 + 数据清洗 + 计算通用指标 (一次), 失败返回 None."""
    df = get_stock_data(code, limit=settings.STRATEGY_DATA_FETCH_LIMIT)
    if df is None or len(df) < settings.STRATEGY_MIN_DATA_LENGTH:
        return None
    if df.isna().any().any():
        df = df.ffill().bfill().infer_objects(copy=False)
    # 🟢 [Optimization] Reset Index for consistent alignment & avoid FutureWarnings
    df.reset_index(drop=True, inplace=True)
    try:
        df = add_indicators(df)
    except Exception as e:
        logger.error(f"Indicator calculation failed for {code}: {e}")
        return None
    return df


def _build_hit(code: str, strat, df_strat: pd.DataFrame) -> Dict[str, Any]:
    """[共享] 由已算信号的 df_strat 构造单策略命中结果 dict. 不检查信号, 调用方负责."""
    # P1: 使用策略自描述接口替代硬编码列映射
    signal_info = strat.get_signal_info(df_strat)

    # 映射止损列
    sl_col = strat.get_metadata().get('sl_column', '')
    if sl_col and sl_col in df_strat.columns:
        df_strat['sl_price'] = df_strat[sl_col]

    # 映射入场价
    entry_col = strat.get_metadata().get('entry_column', '')
    if entry_col and entry_col in df_strat.columns:
        df_strat['entry_price'] = df_strat[entry_col]

    # 映射止盈价
    tp_cols = strat.get_metadata().get('tp_columns', [])
    if tp_cols and tp_cols[0] in df_strat.columns:
        df_strat['tp1_price'] = df_strat[tp_cols[0]]
        if len(tp_cols) > 1 and tp_cols[1] in df_strat.columns:
            df_strat['tp2_price'] = df_strat[tp_cols[1]]

    # Ensure targets exist
    if 'sl_price' not in df_strat.columns:
        df_strat = calculate_targets(df_strat)

    if 'tp1_price' not in df_strat.columns:
        if 'entry_price' not in df_strat.columns: df_strat['entry_price'] = df_strat['close']
        if 'tp1_target' in df_strat.columns:
            df_strat['tp1_price'] = df_strat['tp1_target']
            df_strat['tp2_price'] = df_strat['tp2_target']
        else:
            risk = (df_strat['entry_price'] - df_strat['sl_price']).abs()
            # P1: 使用 metadata 中的 tp_multiplier 代替硬编码策略名判断
            tp_mult = strat.get_metadata().get('tp_multiplier', 2.0)
            df_strat['tp1_price'] = df_strat['entry_price'] + (risk * tp_mult)
            df_strat['tp2_price'] = df_strat['entry_price'] + (risk * tp_mult * 2.0)

    row = df_strat.iloc[-1]

    # P1: extra_info 从 get_signal_info 获取 (策略自描述)
    extra_info = signal_info.get('extra_info', {})

    # 🟢 [Bugfix] 评级由 compute_rating 产出, 位于 signal_info['rating'] (顶层),
    #    而非 extra_info 内. 必须透传进 info, 否则下游 _extract_rating 取不到 → 全回退 C.
    rating = signal_info.get('rating')
    if rating:
        extra_info['rating'] = rating  # 透传, 下游 _extract_rating 读 info['rating']
        if isinstance(rating, dict) and 'score' in rating:
            extra_info['score'] = float(rating['score'])  # 权威评分

    # 🟢 [Bugfix] 非 MTR 策略用信号K线日期作为稳定去重标识，避免 -1 导致 Watchlist 永久屏蔽
    # 兜底: compute_rating 未注入 rating 时的退化 score
    if 'score' not in extra_info:
        score_col = strat.get_metadata().get('score_column', '')
        if score_col and score_col in row.index:
            val = row.get(score_col, 0)
            extra_info['score'] = float(val) if pd.notna(val) else 0
        elif 'mtr_score' in row.index:
            extra_info['score'] = row.get('mtr_score', 0)

    # 🟢 [P0-2.2] 稳定信号日期: 用信号K线日期, 避免归档回退运行日破坏幂等性
    if 'signal_date' not in extra_info:
        extra_info['signal_date'] = str(row['date']) if 'date' in row.index and pd.notna(row['date']) else ''

    return {
        'code': code,
        'type': strat.name,
        'info': {
            'price': row['close'],
            'entry': row.get('entry_price', row['close']),
            'sl': row.get('sl_price', 0),
            'tp1': row.get('tp1_price', 0),
            'tp2': row.get('tp2_price', 0),
            'atr': row.get('atr', 1),
            'type': strat.name,
            'score': extra_info.get('score', row.get('mtr_score', 0) if 'mtr_score' in row else 0),
            # 🟢 [Bugfix] 非 MTR 策略用信号K线日期作为稳定去重标识，避免 -1 导致 Watchlist 永久屏蔽
            'signal_bar_idx': int(row['mtr_signal_bar_idx']) if ('mtr_signal_bar_idx' in row and row['mtr_signal_bar_idx'] == row['mtr_signal_bar_idx']) else (int(pd.Timestamp(row['date']).strftime('%Y%m%d')) if 'date' in row.index and pd.notna(row['date']) else -1),
            **extra_info
        },
        'df': df_strat.tail(70)
    }


def run_scanner(code: str, strategy_name: str = 'MTR_MASTER') -> Optional[Dict[str, Any]]:
    """
    [V8.8 向量化多策略扫描器 — 兼容旧接口]
    返回该股命中的第一个策略 (保持与旧接口兼容). 全策略扫描请用 run_scanner_all.
    """
    strategy_names = [strategy_name] if isinstance(strategy_name, str) else strategy_name
    if not strategy_names:
        strategy_names = ['MTR_MASTER']

    df = _prepare_df(code)
    if df is None:
        return None

    for name in strategy_names:
        try:
            strat = StrategyRegistry.get_strategy(name)
            df_strat = strat.calculate_signals(df.copy())
            latest_signal = df_strat.iloc[-1][strat.signal_column]
            if latest_signal:
                logger.info(f"✨ 策略命中 [{strat.name}]: {code}")
                return _build_hit(code, strat, df_strat)
        except Exception as e:
            logger.warning(f"Strategy {name} error for {code}: {e}")
            continue
    return None


def run_scanner_all(code: str, strategy_names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """
    [V8.8 多策略 ALL 扫描 — 修复短路]
    返回该股命中的所有策略信号列表 (每个命中策略一个 dict), 不再首个命中即返回.
    用于 hunter ALL 扫描, 让每支股票的全部命中策略都能进入推送/归档.
    """
    if not strategy_names:
        strategy_names = ['MTR_MASTER']

    df = _prepare_df(code)
    if df is None:
        return []

    hits = []
    for name in strategy_names:
        try:
            strat = StrategyRegistry.get_strategy(name)
            df_strat = strat.calculate_signals(df.copy())
            latest_signal = df_strat.iloc[-1][strat.signal_column]
            if latest_signal:
                logger.info(f"✨ 策略命中 [{strat.name}]: {code}")
                hits.append(_build_hit(code, strat, df_strat))
        except Exception as e:
            logger.warning(f"Strategy {name} error for {code}: {e}")
            continue
    return hits

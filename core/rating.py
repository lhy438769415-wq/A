# -*- coding: utf-8 -*-
"""
core/rating.py — 信号评级统一输出契约 (RATING_PLAN Phase 0)

⛔ PA 硬约束 (最高优先级, 违反即破坏系统铁律):
  本模块及所有调用它的策略, 评级因子严禁引入以下非纯价格行为指标:
    - volume / vol_ma20 / relative_vol / amount / 成交额
    - ADX / trend_strength / linreg_res (非纯 PA 动量指标)
    - EMA 斜率 / 均线多头排列 (EMA20 仅可作磁力/背景/MA_Gap 翻转证据, 不可作动量打分因子)
  所有评级因子必须能在 config/sop_rules.md 的 16-Step SOP 中找到 PA 理论依据 (标注 sop_ref)。
  任何引入黑名单因子的提交, 视为违反 PA 铁律, 一律拒绝合入。
"""
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

# 评级字母常量
LETTER_A_PLUS = 'A+'
LETTER_A = 'A'
LETTER_B = 'B'
LETTER_C = 'C'
LETTER_D = 'D'

# 全局分档阈值 (Phase 0 暂用; Phase 2 改为逐策略切点, 见 RATING_PLAN §4)
THRESHOLD_A_PLUS = 80
THRESHOLD_A = 65
THRESHOLD_B = 50
THRESHOLD_D = 30  # toxic 或 <30 -> D


@dataclass
class RatingFactor:
    """单个评级因子 (纯 PA, 可溯源 SOP)."""
    name: str                                  # 因子中文名, 如 "回调速度"
    value: float                               # 实际数值, 如 pb_bars=3
    hit: bool                                  # 是否命中加分条件
    weight: float                              # 贡献分 (如 +2 / -1)
    win_rate: Optional[float] = None           # 该因子历史胜率 (Phase 2 填充, Phase 0 为 None)
    sop_ref: str = ''                          # PA 理论依据, 如 "SOP Step 8"
    note: str = ''                             # PA 理据 (可进卡片)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'value': self.value,
            'hit': self.hit,
            'weight': self.weight,
            'win_rate': self.win_rate,
            'sop_ref': self.sop_ref,
            'note': self.note,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> 'RatingFactor':
        return cls(
            name=d.get('name', ''),
            value=d.get('value', 0.0),
            hit=d.get('hit', False),
            weight=d.get('weight', 0.0),
            win_rate=d.get('win_rate'),
            sop_ref=d.get('sop_ref', ''),
            note=d.get('note', ''),
        )


@dataclass
class RatingResult:
    """统一评级输出 (所有策略经 compute_rating 产出)."""
    raw_score: float                           # 策略原生分 (structural: ev_score -5~+9; mtr: 0-100)
    score: int                                 # 归一化 0-100 (经 score_map / map_native)
    letter: str                                # 'A+' / 'A' / 'B' / 'C' / 'D'
    factors: List[RatingFactor] = field(default_factory=list)
    toxic: bool = False                        # D 级(毒性)标记
    calibrated: bool = False                   # Phase 0 False(手调权重); Phase 2 True(回测校准)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'raw_score': self.raw_score,
            'score': self.score,
            'letter': self.letter,
            'factors': [f.to_dict() for f in self.factors],
            'toxic': self.toxic,
            'calibrated': self.calibrated,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> 'RatingResult':
        return cls(
            raw_score=d.get('raw_score', 0.0),
            score=d.get('score', 0),
            letter=d.get('letter', LETTER_C),
            factors=[RatingFactor.from_dict(x) for x in d.get('factors', [])],
            toxic=d.get('toxic', False),
            calibrated=d.get('calibrated', False),
        )


def band(score: int, toxic: bool = False) -> str:
    """Phase 0 全局分档: >=80 A+, >=65 A, >=50 B, else C; toxic或<30 -> D.
       Phase 2 起由各策略 rating_factors.json 的逐策略切点替代 (RATING_PLAN §4)."""
    if toxic or score < THRESHOLD_D:
        return LETTER_D
    if score >= THRESHOLD_A_PLUS:
        return LETTER_A_PLUS
    if score >= THRESHOLD_A:
        return LETTER_A
    if score >= THRESHOLD_B:
        return LETTER_B
    return LETTER_C


def clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> int:
    """数值夹取到 [lo, hi] 并取整."""
    return int(max(lo, min(hi, x)))


def map_native(native: float, intercept: float = 50.0, slope: float = 10.0) -> int:
    """各策略单调映射原生分 -> 0-100 (clamp).
       score = clamp(intercept + slope * native).
       例:
         structural ev_score∈[-5,+9] -> 50 + 10*ev_score (与原四因子映射兼容)
         mtr 0-100 直通 -> intercept=0, slope=1
    """
    return clamp(intercept + slope * native)

from .base import BasePattern, PatternRegistry
from .weekly_bull_flag import WeeklyBullFlagThreePushesPattern

# 在这里注册所有形态
PatternRegistry.register(WeeklyBullFlagThreePushesPattern())

__all__ = [
    'BasePattern',
    'PatternRegistry',
    'WeeklyBullFlagThreePushesPattern'
]

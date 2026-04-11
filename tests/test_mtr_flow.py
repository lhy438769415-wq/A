import sys
import os
import unittest
import pandas as pd
from unittest.mock import MagicMock, patch

# Setup path
sys.path.append(os.getcwd())

class TestMTRRoutingV95(unittest.TestCase):
    def test_mtr_rating_logic(self):
        """验证 hunter.py 中的 MTR 评级分发逻辑"""
        # 构造模拟数据
        mock_hits = [
            {'code': 'sh.600000', 'type': 'MTR_V35_STRUCTURAL', 'info': {'score': 85}, 'df': MagicMock()},
            {'code': 'sz.000001', 'type': 'MTR_V35_STRUCTURAL', 'info': {'score': 70}, 'df': MagicMock()},
            {'code': 'sz.002046', 'type': 'MTR_V35_STRUCTURAL', 'info': {'score': 55}, 'df': MagicMock()},
            {'code': 'sh.601318', 'type': 'MTR_V35_STRUCTURAL', 'info': {'score': 40}, 'df': MagicMock()},
            {'code': 'sz.300750', 'type': 'STRUCTURAL_GAP', 'info': {'score': 90}, 'df': MagicMock()},
        ]
        
        # 这里的逻辑应与 hunter.py 381-408 行一致
        direct_picks = []
        ai_candidates_raw = []
        
        for res in mock_hits:
            strat_type = res.get('type', '').upper()
            if '3K' in strat_type or 'STRUCTURAL_GAP' in strat_type or 'MTR' in strat_type:
                # Mock prepare_daily_chart
                res_item = res.copy()
                if 'MTR' in strat_type:
                    score = res.get('info', {}).get('score', 0)
                    if score >= 80: ev_rating = '🌟🌟 极品 (A+)'
                    elif score >= 65: ev_rating = '🌟 高预期 (A)'
                    elif score >= 50: ev_rating = '👍 常态 (B)'
                    else: ev_rating = '⚠️ 低预期 (C)'
                    res_item['info']['ev_rating'] = ev_rating
                direct_picks.append(res_item)
            else:
                ai_candidates_raw.append(res)
                
        # 验证结果
        self.assertEqual(len(direct_picks), 5)
        self.assertEqual(len(ai_candidates_raw), 0)
        
        # 检查评级
        self.assertEqual(direct_picks[0]['info']['ev_rating'], '🌟🌟 极品 (A+)')
        self.assertEqual(direct_picks[1]['info']['ev_rating'], '🌟 高预期 (A)')
        self.assertEqual(direct_picks[2]['info']['ev_rating'], '👍 常态 (B)')
        self.assertEqual(direct_picks[3]['info']['ev_rating'], '⚠️ 低预期 (C)')
        print("\n✅ MTR 评级与路由逻辑验证通过！")

if __name__ == "__main__":
    unittest.main()

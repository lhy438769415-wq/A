import sys
import os
import unittest

sys.path.append(os.getcwd())

# 测试真实的评级逻辑 (单一事实来源), 而非在测试体里复制 hunter 的分档阈值。
# 此前该测试把 hunter 的分档逻辑抄进测试体自测 -> hunter 改坏它照样绿。
from core.rating import band, LETTER_A_PLUS, LETTER_A, LETTER_B, LETTER_C, LETTER_D
from hunter import _letter_to_ev_text


class TestMTRRatingRouting(unittest.TestCase):
    def test_band_thresholds(self):
        """真实 band() 分档: 85->A+, 70->A, 55->B, 40->C"""
        self.assertEqual(band(85), LETTER_A_PLUS)
        self.assertEqual(band(70), LETTER_A)
        self.assertEqual(band(55), LETTER_B)
        self.assertEqual(band(40), LETTER_C)

    def test_band_toxic(self):
        self.assertEqual(band(5, toxic=True), LETTER_D)
        # 分数低于 D 阈值(30)即使非 toxic 也归 D
        self.assertEqual(band(10, toxic=False), LETTER_D)
        # B 档需 >= 50; 45 仍归 C
        self.assertEqual(band(45, toxic=False), LETTER_C)
        self.assertEqual(band(55, toxic=False), LETTER_B)

    def test_ev_text_mapping(self):
        """真实 _letter_to_ev_text 映射 (与 hunter 推送文案一致)"""
        self.assertEqual(_letter_to_ev_text('A+'), '🌟🌟 极品 (A+)')
        self.assertEqual(_letter_to_ev_text('A'), '🌟 高预期 (A)')
        self.assertEqual(_letter_to_ev_text('B'), '👍 常态 (B)')
        self.assertEqual(_letter_to_ev_text('C'), '⚠️ 低预期 (C)')


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""
周线多策略扫描与日线AI审计旁路功能测试
"""
import sys
import os
import unittest
from unittest.mock import MagicMock, patch
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from tools.scanner_weekly_gap import _scan_single_code, scan_weekly_gap
from hunter import run_pipeline_once


class TestWeeklyGapScannerMultiStrategy:
    """测试周线多策略扫描与解耦逻辑"""

    @patch('tools.scanner_weekly_gap.fetch_weekly_data')
    @patch('core.data_provider.get_stock_name')
    def test_scan_single_code_structural_gap(self, mock_get_name, mock_fetch):
        """测试周线扫描单个代码 (Structural Gap)"""
        mock_get_name.return_value = "测试股"
        
        # 模拟 150 行周线数据
        np.random.seed(42)
        df = pd.DataFrame({
            'open': np.random.uniform(10, 20, 150),
            'high': np.random.uniform(20, 25, 150),
            'low': np.random.uniform(5, 10, 150),
            'close': np.random.uniform(10, 20, 150),
            'volume': np.random.uniform(1000, 5000, 150)
        })
        # 加上必要列以防指标计算报错
        df['trade_date'] = pd.date_range(start='2020-01-01', periods=150, freq='W')
        df.set_index('trade_date', inplace=True)
        
        mock_fetch.return_value = df

        # 调用 _scan_single_code，传入单个策略
        results = _scan_single_code('sh.600000', recent_weeks=4, strategies=['STRATEGY_STRUCTURAL_GAP'])
        assert isinstance(results, list)

    @patch('tools.scanner_weekly_gap.fetch_weekly_data')
    @patch('core.data_provider.get_stock_name')
    def test_scan_single_code_gap_h2(self, mock_get_name, mock_fetch):
        """测试周线扫描单个代码 (Gap H2 策略字段反射读取)"""
        mock_get_name.return_value = "测试股"
        
        # 模拟 H2 策略有信号的 DataFrame
        # 实际上不需要完整构造，我们只要看是否能正常调用 calculate_signals
        df = pd.DataFrame({
            'open': [10.0] * 120,
            'high': [15.0] * 120,
            'low': [9.0] * 120,
            'close': [12.0] * 120,
        })
        df['trade_date'] = pd.date_range(start='2020-01-01', periods=120, freq='W')
        df.set_index('trade_date', inplace=True)
        mock_fetch.return_value = df

        # 调用 _scan_single_code，指定 STRATEGY_GAP_H2
        results = _scan_single_code('sh.600000', recent_weeks=4, strategies=['STRATEGY_GAP_H2'])
        assert isinstance(results, list)


class TestDailyPipelineBypassAI:
    """测试日线扫描旁路 AI 审计的极速流程"""

    @patch('tools.watchlist.WatchlistManager')
    @patch('tools.notifier.send_discord_images')
    @patch('hunter.send_discord_message')
    @patch('hunter.run_scanner')
    def test_run_pipeline_no_ai(self, mock_scanner, mock_discord_msg, mock_discord_imgs, mock_watchlist_cls):
        """测试 --no-ai 模式下成功旁路 AI，且直接生成 direct_picks"""
        # Mock scanner 返回包含 Gap H2 信号的结果
        mock_scanner.return_value = {
            'code': 'sh.600000',
            'type': 'STRATEGY_GAP_H2',
            'info': {
                'price': 12.00,
                'entry': 12.50,
                'sl': 11.00,
                'tp1': 14.50,
                'score': 75.0,
                'signal_bar_idx': 100,
                'sig_bar_quality_h2': 0.9
            },
            'df': pd.DataFrame({
                'date': pd.date_range(start='2023-01-01', periods=110),
                'close': [12.00] * 110,
                'open': [11.80] * 110,
                'high': [12.20] * 110,
                'low': [11.50] * 110
            })
        }
        
        # Mock Watchlist 状态管理，防止读写本地物理文件
        mock_watchlist = MagicMock()
        mock_watchlist.data = {}
        mock_watchlist.get_watching.return_value = {}
        mock_watchlist_cls.return_value = mock_watchlist

        # 运行日线 Pipeline，且 use_ai=False
        new_signals = run_pipeline_once(
            all_codes=['sh.600000'],
            strategies=['STRATEGY_GAP_H2'],
            seen_signals=set(),
            use_ai=False
        )
        
        # 应该正常返回，并且 mock_discord_msg 被调用以输出报告
        assert 'sh.600000_STRATEGY_GAP_H2' in new_signals
        assert mock_discord_msg.called

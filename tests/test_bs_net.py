# -*- coding: utf-8 -*-
"""Baostock 网络连通性测试 (手动/可选)

本项目离线优先, 该测试默认跳过, 避免 pytest 收集即触发网络 I/O, 且 CI 环境无 Baostock 可达。
需手动验证连通性时: RUN_NETWORK_TESTS=1 pytest tests/test_bs_net.py
"""
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get('RUN_NETWORK_TESTS') != '1',
    reason='网络连通性检查默认跳过 (离线优先); 设 RUN_NETWORK_TESTS=1 启用',
)


def test_baostock_connectivity():
    import socket

    import baostock.common.contants as c

    host = getattr(c, 'BAOSTOCK_SERVER_IP', 'unknown')
    port = int(getattr(c, 'BAOSTOCK_SERVER_PORT', 0))

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    try:
        result = sock.connect_ex((host, port))
        assert result == 0, f"TCP 连接失败 errno={result}"
    finally:
        sock.close()

    import baostock as bs

    lg = bs.login()
    try:
        assert lg.error_code == '0', f"login 失败: {lg.error_msg}"
    finally:
        bs.logout()

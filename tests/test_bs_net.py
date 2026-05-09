# -*- coding: utf-8 -*-
"""最小化 Baostock 网络连通性测试"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import socket, time

# 1. 读取 Baostock 服务器地址
import baostock.common.contants as c
host = getattr(c, 'BAOSTOCK_SERVER_IP', 'unknown')
port = int(getattr(c, 'BAOSTOCK_SERVER_PORT', 0))
print(f"[1] Baostock server: {host}:{port}")

# 2. TCP 连通性测试 (5秒超时)
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(5)
t0 = time.time()
try:
    result = sock.connect_ex((host, port))
    elapsed = time.time() - t0
    status = "OK" if result == 0 else "FAIL"
    print(f"[2] TCP connect: {status} (errno={result}, {elapsed:.2f}s)")
except Exception as e:
    elapsed = time.time() - t0
    print(f"[2] TCP exception: {e} ({elapsed:.2f}s)")
finally:
    sock.close()

# 3. Baostock login 测试 (带超时保护)
print("[3] Testing bs.login() (timeout=15s)...")
import threading
login_result = {'done': False, 'error_code': None, 'error_msg': None, 'exception': None}

def _try_login():
    try:
        import baostock as bs
        lg = bs.login()
        login_result['error_code'] = lg.error_code
        login_result['error_msg'] = lg.error_msg
        login_result['done'] = True
        if lg.error_code == '0':
            # 额外测试: 查询一个简单接口
            rs = bs.query_stock_basic(code="sh.600000")
            print(f"[4] query_stock_basic: error_code={rs.error_code}")
            bs.logout()
    except Exception as e:
        login_result['exception'] = str(e)
        login_result['done'] = True

t = threading.Thread(target=_try_login, daemon=True)
t.start()
t.join(timeout=15)

if not login_result['done']:
    print("[3] TIMEOUT! bs.login() hung for >15s - network issue confirmed")
elif login_result['exception']:
    print(f"[3] EXCEPTION: {login_result['exception']}")
elif login_result['error_code'] == '0':
    print(f"[3] LOGIN OK (error_code=0)")
else:
    print(f"[3] LOGIN FAIL: code={login_result['error_code']}, msg={login_result['error_msg']}")

print("\n--- Test Complete ---")


import threading
import time
import subprocess
import platform
import logging
from typing import Dict, Any

from core.database import get_db_connection
from config import settings

logger = logging.getLogger(__name__)

class SystemMonitor(threading.Thread):
    def __init__(self, interval: int = 10):
        super().__init__()
        self.daemon = True # 🟢 Daemonize: Kill thread when main process exits
        self.interval = interval
        self.running = True
        self.status = {
            "internet": False,
            "database": False,
            "latency": 0
        }
        self._callbacks = []

    def register_callback(self, callback):
        """注册回调函数，当状态更新时调用"""
        self._callbacks.append(callback)

    def check_internet(self) -> bool:
        """
        Check connectivity by attempting to connect to a reliable host (TCP).
        This is more robust than ICMP Ping which is often blocked.
        """
        host = "www.baidu.com"
        port = 80
        timeout = 3
        
        start_time = time.time()
        try:
            import socket
            # Try TCP handshake
            socket.create_connection((host, port), timeout=timeout).close()
            
            latency = (time.time() - start_time) * 1000 # ms
            self.status['latency'] = int(latency)
            return True
        except Exception:
            return False

    def check_database(self) -> bool:
        """检查数据库连接池健康状况"""
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
                return True
        except Exception as e:
            logger.error(f"Monitor DB Check Failed: {e}")
            return False

    def run(self):
        logger.info("🛡️ System Monitor started")
        while self.running:
            try:
                # 1. Check Internet
                is_online = self.check_internet()
                self.status['internet'] = is_online
                
                # 2. Check DB
                db_ok = self.check_database()
                self.status['database'] = db_ok
                
                # 3. Notify UI
                for cb in self._callbacks:
                    try:
                        cb(self.status)
                    except Exception as e:
                        logger.error(f"Monitor Callback Error: {e}")
                        
            except Exception as e:
                logger.error(f"Monitor Loop Error: {e}")
                
            time.sleep(self.interval)
            
    def stop(self):
        self.running = False

# 全局实例
monitor = SystemMonitor()

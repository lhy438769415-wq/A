# -*- coding: utf-8 -*-
"""Brooks-AI 操盘台启动器.

作用: 在 pythonw 无控制台环境下捕获异常并写入日志, 避免双击启动失败时看不到报错。
"""
import os
import sys
import traceback

log_path = os.path.join(os.environ.get("TEMP", "."), "brooks_dashboard.log")


def main():
    try:
        import gui_dashboard

        gui_dashboard.main()
    except Exception:
        with open(log_path, "w", encoding="utf-8") as f:
            traceback.print_exc(file=f)
        sys.exit(1)


if __name__ == "__main__":
    main()

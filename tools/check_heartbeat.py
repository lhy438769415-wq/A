#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
运行监控 / 心跳检查 (Phase 3: 持续进化)

检查 data/last_run.json 的上次成功运行时间是否过期。
默认 >26h 视为"今日盘后扫描未成功运行"，适配日线盘后扫描节奏。

可配合系统定时任务 (Windows 任务计划程序 / cron) 每日运行,
发现过期则发 Discord 告警, 避免"猎手静默死、人收不到信号"却无人知晓。

用法:
  python tools/check_heartbeat.py                      # 仅打印状态 (退出码 0=健康, 2=过期/无记录)
  python tools/check_heartbeat.py --alert              # 过期时发 Discord 告警
  python tools/check_heartbeat.py --max-age-hours 26   # 自定义过期阈值(小时)
"""
import os
import sys
import json
import argparse
import datetime


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, 'data', 'last_run.json')


def _alert(msg):
    try:
        from tools.notifier import send_discord_message
        send_discord_message(f"⚠️ **Brooks-AI 猎手运行监控**\n{msg}")
    except Exception as e:
        print(f"[告警发送失败] {e}")


def main():
    ap = argparse.ArgumentParser(description="Brooks-AI 运行心跳检查")
    ap.add_argument('--max-age-hours', type=float, default=26.0,
                    help='超过该小时数视为过期 (默认 26, 适配日线盘后扫描)')
    ap.add_argument('--alert', action='store_true', help='过期/无记录时发 Discord 告警')
    args = ap.parse_args()

    if not os.path.exists(PATH):
        msg = ("⚠️ 未检测到任何运行记录 (data/last_run.json 缺失) — "
               "猎手可能从未成功运行或记录被删")
        print(msg)
        if args.alert:
            _alert(msg)
        sys.exit(2)

    try:
        with open(PATH, encoding='utf-8') as f:
            info = json.load(f)
        last = datetime.datetime.fromisoformat(info['last_run'])
    except Exception as e:
        msg = f"⚠️ 心跳文件损坏: {e}"
        print(msg)
        if args.alert:
            _alert(msg)
        sys.exit(2)

    now = datetime.datetime.now()
    age_h = (now - last).total_seconds() / 3600.0
    if age_h > args.max_age_hours:
        msg = (f"⚠️ 猎手已超过 {age_h:.1f}h 未成功运行 (上次: {info['last_run']})，"
               f"请检查是否崩溃或未触发")
        print(msg)
        if args.alert:
            _alert(msg)
        sys.exit(2)

    print(f"✅ 运行健康: 上次成功运行 {info['last_run']} (距今 {age_h:.1f}h)")
    sys.exit(0)


if __name__ == "__main__":
    main()

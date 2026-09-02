# -*- coding: utf-8 -*-
#!/usr/bin/env python3
"""
Brooks-AI 操盘台 — Apple 风格初筛台 (v10)
定位: 收盘后"扫描结果 -> 按策略看清单 -> 一键送 TradingView 深研"的传送带。
只读取 signal_archive 已落地的命中标的 (A股多头系统, 不存在多空概念),
不重画深研图、不碰策略逻辑、不展示库里没有的字段。
"""
import sys
import re
import os
import json
import webbrowser
import logging
import threading
import datetime

import tkinter as tk
import ttkbootstrap as ttk
from ttkbootstrap.constants import (  # 显式导入, 禁止 import *
    BOTH, LEFT, RIGHT, X, NSEW, EW, END, W, E, CENTER, HORIZONTAL, VERTICAL, DISABLED, NORMAL,
)

# ---- 业务模块 (优雅降级: 导入失败则对应按钮禁用, 界面仍可开) ----
# 真实入口 (经 grep 核实, 非凭记忆):
#   日线同步 = core.data_provider.update_daily_data_batch
#   周线同步 = core.data_provider.update_weekly_data_batch
#   股票清单 = core.data_provider.get_stock_list   (扫描需要传入 all_codes)
#   日线扫描 = hunter.run_pipeline_once
#   周线扫描 = core.scan_engine.run_weekly_scan
update_daily_data_batch = None
update_weekly_data_batch = None
get_stock_list = None
main_script = None
run_weekly_scan = None
try:
    from core.data_provider import update_daily_data_batch, update_weekly_data_batch, get_stock_list
except Exception as e:  # noqa: BLE001 - 允许降级, 不阻断界面
    logging.warning(f"数据同步模块导入失败(同步按钮将不可用): {e}")
try:
    from core.scan_engine import run_weekly_scan
except Exception as e:  # noqa: BLE001 - 允许降级, 不阻断界面
    logging.warning(f"扫描引擎导入失败(扫描按钮将不可用): {e}")
try:
    import hunter as main_script
except Exception as e:  # noqa: BLE001 - 允许降级, 不阻断界面
    logging.warning(f"扫描模块导入失败(扫描按钮将不可用): {e}")
track_signals = None
try:
    from core.signal_tracker.tracking import track_signals
except Exception as e:  # noqa: BLE001 - 允许降级, 不阻断界面
    logging.warning(f"信号追踪模块导入失败(周线扫描后将跳过状态跟进): {e}")


# ---- 注册表 key 顺序 (决定左栏展示顺序) ----
STRATEGY_ORDER = [
    "MTR_MASTER", "STRATEGY_3K", "STRATEGY_STRUCTURAL_GAP",
    "STRATEGY_GAP_PINBAR", "STRATEGY_GAP_H2", "STRATEGY_AWIL",
]

_DISPLAY_CACHE = {}


def strategy_display(key):
    """注册表 key -> 对外花名 (如 GAP H1)。失败回退原 key。"""
    if key in _DISPLAY_CACHE:
        return _DISPLAY_CACHE[key]
    try:
        from core.strategy_registry import StrategyRegistry
        name = StrategyRegistry.get_metadata(key).get("display_name", key)
    except Exception:  # noqa: BLE001
        name = key
    _DISPLAY_CACHE[key] = name
    return name


# 只统计这 6 个现行策略 key, 过滤历史脏数据 (旧写法 / MTR_V35_STRUCTURAL / TEST_STRAT / UNKNOWN 等)
_VALID_KEYS = tuple(STRATEGY_ORDER)
_KEY_PH = ",".join("?" * len(_VALID_KEYS))
_DATE_LIKE = "____-__-__"  # SQLite LIKE: 匹配 YYYY-MM-DD

# 历史脏数据归一: 库里有一批信号把「结构性缺口 GAP H1」记成了早期内部写法,
# 它与现行注册表 key STRATEGY_STRUCTURAL_GAP 是同一个策略(花名 GAP H1), 并非独立策略。
# 不归一的话, 按现行 key 查询会整批漏掉这些信号 (周线里占比很高)。
_STRAT_NORM_SQL = ("CASE WHEN strategy='STRUCTURAL_GAP' THEN 'STRATEGY_STRUCTURAL_GAP' "
                   "ELSE strategy END")

# 🔴 重复归档去重(只读层处理, 不动库)
# 病根: signal_id 的生成格式中途变过 — 旧格式缺周期字段
#   (sz.003004_STRATEGY_STRUCTURAL_GAP_2026-06-18),
#   新格式带周期 (sz.003004_STRATEGY_STRUCTURAL_GAP_weekly_2026-06-18)。
#   INSERT OR IGNORE 靠 signal_id 防重, 格式一变老数据就对不上新 ID,
#   于是同一条信号被存了两份(入场价一字不差)。周线真实组 526 行里有 150 行是这样的双份。
# 判重口径: 同 票 + 同 周 + 同 策略(归一后) + 同 周期 + 同 入场价 = 同一条信号, 只留最新入库那份。
# ⚠️ 只用于展示, 不修正库里的数据 — 清库属于动数据, 需用户另行批准。
_DEDUP_FROM = (
    "(SELECT * FROM signal_archive sa WHERE sa.rowid = ("
    "  SELECT s2.rowid FROM signal_archive s2 "
    "  WHERE s2.code = sa.code AND s2.signal_date = sa.signal_date "
    "    AND s2.timeframe = sa.timeframe AND s2.entry_price = sa.entry_price "
    f"   AND CASE WHEN s2.strategy='STRUCTURAL_GAP' THEN 'STRATEGY_STRUCTURAL_GAP' ELSE s2.strategy END "
    f"     = CASE WHEN sa.strategy='STRUCTURAL_GAP' THEN 'STRATEGY_STRUCTURAL_GAP' ELSE sa.strategy END "
    "  ORDER BY s2.created_at DESC, s2.rowid DESC LIMIT 1"
    ")) AS sa_dedup"
)


def _db():
    """返回 signal_archive 所在库的只读连接 (core.database 单一来源)。"""
    from core.database import get_db_connection
    return get_db_connection()


def _tv_url(code):
    """拼 TradingView A 股图表地址 (沪市 SSE / 深市 SZSE)。"""
    m = re.search(r"(\d{6})", str(code))
    digits = m.group(1) if m else str(code)
    exch = "SSE" if digits.startswith("6") else "SZSE"
    return f"https://cn.tradingview.com/chart/?symbol={exch}:{digits}"


def _fmt(v, digits=2):
    try:
        if v is None:
            return "—"
        return f"{float(v):.{digits}f}"
    except (TypeError, ValueError):
        return "—"


class TradingDashboard:
    def __init__(self, root):
        self.root = root
        self.root.title("Brooks-AI 操盘台")
        # 注: 窗口尺寸/最小尺寸由 main() 统一设(1600x1000 / 1280x760),
        # 此处不再覆盖, 否则会压回 1080x720 导致窗口偏小。

        self.current_strategy = None
        self.current_rows = []
        self.current_detail_row = None
        self.strat_buttons = {}
        self.photo = None  # 防止 PhotoImage 被 GC
        self.selected_date = None  # 当前选中的信号日; None 时自动取最新
        # 周期: 'daily' | 'weekly'。只由用户手动点顶部切换, 程序不按星期自动判断。
        self.tf_var = tk.StringVar(value="daily")
        self._chart_orig = None  # 原始 K 线 PIL Image, 用于自适应重绘
        self._chart_last_size = (0, 0)  # 上次渲染尺寸, 避免 resize 死循环
        self._hunter_ok = bool(main_script and get_stock_list)  # 扫描模块是否可用
        self._sync_ok = bool(update_daily_data_batch)  # 行情下载模块是否可用
        self._stop_event = threading.Event()  # 手动终止信号: 运行中置位, 后台循环检查
        # 用户手动标记的 "关注" 按周期隔离: {"daily": {code: name}, "weekly": {code: name}}
        self.favorites = self._load_favorites()
        self._tree_hover_iid = None  # 信号清单当前悬停行
        self._watch_hover_iid = None  # 关注列表当前悬停行

        self._build_ui()
        self._refresh_watchlist()
        self._refresh()

    # ================= UI 构建 =================
    def _build_ui(self):
        # 全局网格: 顶栏(0) / 主区(1, 展开) / 状态栏(2)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        # ---- 顶栏 ----
        top = ttk.Frame(self.root, padding=(18, 12))
        top.grid(row=0, column=0, sticky=NSEW)
        ttk.Label(top, text="Brooks-AI 操盘台", font=("Microsoft YaHei", 16, "bold"),
                  foreground="#f5f5f7").pack(side=LEFT, padx=(4, 20))

        # 周期切换 (日线 / 周线): 默认日线; 只由用户手动点, 程序不按星期自动判断
        tf_f = ttk.Frame(top)
        tf_f.pack(side=LEFT, padx=(0, 18))
        ttk.Radiobutton(tf_f, text="日线", value="daily", variable=self.tf_var,
                        bootstyle="toolbutton", command=self._on_timeframe_change).pack(side=LEFT)
        ttk.Radiobutton(tf_f, text="周线", value="weekly", variable=self.tf_var,
                        bootstyle="toolbutton", command=self._on_timeframe_change).pack(side=LEFT)

        # 搜索框 (回车 -> 直接送 TradingView 深研)
        search_f = ttk.Frame(top)
        search_f.pack(side=LEFT, padx=(0, 20))
        self.ent_code = ttk.Entry(search_f, width=14, font=("Consolas", 12))
        self.ent_code.pack(side=LEFT, ipady=3)
        self._search_placeholder = "输入代码送 TV"
        self.ent_code.insert(0, self._search_placeholder)
        self.ent_code.config(foreground="#8e8e93")  # placeholder 暗色
        self.ent_code.bind("<FocusIn>", self._on_search_focus)
        self.ent_code.bind("<FocusOut>", self._on_search_blur)
        self.ent_code.bind("<Return>", lambda e: self._open_tv_for_entry())

        # 动作按钮 (左=策略扫描, 右=下载行情; 运行时出现红色"终止"按钮)
        self.btn_hunter = ttk.Button(top, text="策略扫描", bootstyle="primary",
                                     command=self.start_hunter, state=DISABLED if not self._hunter_ok else NORMAL)
        self.btn_hunter.pack(side=LEFT, padx=5)
        self.btn_sync = ttk.Button(top, text="下载行情", bootstyle="secondary-outline",
                                  command=self.start_sync, state=DISABLED if not self._sync_ok else NORMAL)
        self.btn_sync.pack(side=LEFT, padx=5)
        # 红色"终止"按钮: 默认隐藏, 任何操作运行时出现
        self.btn_stop = ttk.Button(top, text="终止", bootstyle="danger", command=self._on_stop)
        self.btn_stop.pack(side=LEFT, padx=5, after=self.btn_sync)
        self.btn_stop.pack_forget()

        # AI 复核开关 (默认关=纯本地离线扫描; 勾选=相关策略送 DeepSeek 二次审计)
        self.ai_var = tk.BooleanVar(value=False)
        self.chk_ai = ttk.Checkbutton(top, text="AI 复核", variable=self.ai_var,
                                      bootstyle="round-toggle")
        self.chk_ai.pack(side=LEFT, padx=(16, 0))

        # 信号日选择 (拆成年/月/日, 默认最新, 可联动筛选)
        # 周线模式下同一个下拉的含义变为「截至哪一周」, 标签跟着改, 免得看岔。
        self.date_label = ttk.Label(top, text="信号日", font=("Microsoft YaHei", 10),
                                    foreground="#a1a1a6")
        self.date_label.pack(side=LEFT, padx=(20, 4))
        self.year_var = tk.StringVar()
        self.month_var = tk.StringVar()
        self.day_var = tk.StringVar()
        combo_kw = {"state": "readonly", "width": 6, "font": ("Consolas", 11)}
        self.year_combo = ttk.Combobox(top, textvariable=self.year_var, **combo_kw)
        self.year_combo.pack(side=LEFT, padx=(0, 2))
        ttk.Label(top, text="-", foreground="#a1a1a6").pack(side=LEFT)
        self.month_combo = ttk.Combobox(top, textvariable=self.month_var, **combo_kw)
        self.month_combo.pack(side=LEFT, padx=(2, 2))
        ttk.Label(top, text="-", foreground="#a1a1a6").pack(side=LEFT)
        self.day_combo = ttk.Combobox(top, textvariable=self.day_var, **combo_kw)
        self.day_combo.pack(side=LEFT, padx=(2, 0))
        self.year_combo.bind("<<ComboboxSelected>>", self._on_year_change)
        self.month_combo.bind("<<ComboboxSelected>>", self._on_month_change)
        self.day_combo.bind("<<ComboboxSelected>>", self._on_day_change)

        # 右侧数据新鲜度
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(top, textvariable=self.status_var, font=("Consolas", 10),
                  foreground="#a1a1a6").pack(side=RIGHT, padx=6)

        ttk.Separator(self.root, orient=HORIZONTAL).grid(row=0, column=0, sticky=E, padx=0)

        # ---- 主区三栏 ----
        main = ttk.Frame(self.root, padding=(16, 12))
        main.grid(row=1, column=0, sticky=NSEW)
        # 左栏/中栏固定宽度(导航用), 右栏随窗口缩放(给图表最大空间)
        main.columnconfigure(0, weight=0, minsize=180)
        main.columnconfigure(1, weight=0, minsize=420)  # 中栏加宽, 避免三列被挤
        main.columnconfigure(2, weight=1)
        main.rowconfigure(0, weight=1)

        # 左栏: 策略导航 (收窄, 只放名字+数量)
        side = ttk.Frame(main, width=180, padding=(0, 0))
        side.grid(row=0, column=0, sticky=NSEW, padx=(0, 16))
        side.rowconfigure(1, weight=1)
        side.grid_propagate(False)
        ttk.Label(side, text="策略", font=("Microsoft YaHei", 12, "bold"),
                  foreground="#a1a1a6").pack(anchor=W, pady=(0, 10))
        self.sidebar_inner = ttk.Frame(side)
        self.sidebar_inner.pack(fill=BOTH, expand=True)

        # 中栏: 今日信号清单 (恢复独占中栏高度, 不再上下分)
        center = ttk.Frame(main, width=420, padding=(0, 0))
        center.grid(row=0, column=1, sticky=NSEW)
        center.grid_propagate(False)
        center.rowconfigure(1, weight=1)
        center.columnconfigure(0, weight=1)
        center.columnconfigure(1, weight=0)
        self.list_title = ttk.Label(center, text="今日信号", font=("Microsoft YaHei", 14, "bold"),
                                    foreground="#f5f5f7")
        self.list_title.grid(row=0, column=0, sticky=W, padx=4, pady=(0, 12))
        # 周线模式专用视图切换: 已确认信号 / 待建仓(pending, 形态成立但价格未到位)
        self.view_var = tk.StringVar(value="已确认")
        self.view_combo = ttk.Combobox(center, textvariable=self.view_var,
                                       values=["已确认", "待建仓"], state="readonly",
                                       width=8, font=("Microsoft YaHei", 9))
        self.view_combo.grid(row=0, column=1, sticky=E, padx=(8, 0), pady=(0, 12))
        self.view_combo.bind("<<ComboboxSelected>>", self._on_view_change)
        self.view_combo.grid_remove()  # 日线默认隐藏

        # 信号清单列: [红点图] | 序号 | 代码 | 名称
        # 红点用图片画在树形列(#0), 兼容所有 Tk 版本, 且仅圆点变红、整行文字保持白
        cols = ("序号", "代码", "名称")
        self.tree = ttk.Treeview(center, columns=cols, show="tree headings")
        self.tree.heading("#0", text="")
        self.tree.column("#0", width=24, minwidth=24, anchor=CENTER, stretch=False)
        # 宽度: 序号 36, 代码 120, 名称 150(唯一可伸缩)
        widths = (36, 120, 150)
        anchors = (CENTER, W, W)
        for c, w, a in zip(cols, widths, anchors):
            self.tree.heading(c, text=c)
            self.tree.column(c, width=w, minwidth=w, anchor=a, stretch=(c == "名称"))
        # Treeview 跨中栏两列, 避免右侧视图下拉占用列宽导致列表被挤压
        self.tree.grid(row=1, column=0, columnspan=2, sticky=NSEW)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        # 红点图片(已关注时显示在 #0 列); 引用挂 self 防被 GC 回收
        self._dot_img = self._make_dot_image()
        # TV 式关注效果: 悬停变灰; 已关注行不再整行标红(红点已表示关注)
        self.tree.tag_configure("hover", background="#4a4a4e")
        self.tree.bind("<Motion>", self._on_tree_motion)
        self.tree.bind("<Leave>", self._on_tree_leave)
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Button-3>", self._on_tree_right_click)
        self._tree_hover_iid = None

        # 右栏: 选中票详情 + 关注列表
        # 右栏只有一列(图表区占满), 关注列表用 place 浮在右侧留白区, 不抢列宽
        right = ttk.Frame(main, padding=(16, 0))
        right.grid(row=0, column=2, sticky=NSEW, padx=(16, 0))
        right.columnconfigure(0, weight=1)   # 图表区占满右栏宽度(与无关注列表时一致)
        right.rowconfigure(0, weight=1)      # 图表区优先占垂直空间
        right.rowconfigure(1, weight=0)
        right.rowconfigure(2, weight=0)
        right.rowconfigure(3, weight=0)

        # 图表容器: 尺寸只由 grid 权重决定, 不随图片内容膨胀 (切断 resize 反馈回路)
        self.chart_frame = ttk.Frame(right)
        self.chart_frame.grid(row=0, column=0, sticky=NSEW, pady=(0, 14))
        self.chart_frame.columnconfigure(0, weight=1)
        self.chart_frame.rowconfigure(0, weight=1)
        self.chart_frame.bind("<Configure>", self._on_chart_configure)
        self.chart_label = ttk.Label(self.chart_frame, text="选中左侧标的查看缩略图",
                                     foreground="#8e8e93", font=("Microsoft YaHei", 11),
                                     anchor="w")
        self.chart_label.grid(row=0, column=0, sticky=NSEW)

        self.lbl_strat = ttk.Label(right, text="", font=("Microsoft YaHei", 14, "bold"),
                                   foreground="#f5f5f7")
        self.lbl_strat.grid(row=1, column=0, sticky=W, pady=(0, 12))
        self.lbl_facts = ttk.Label(right, text="", justify=LEFT, font=("Consolas", 12),
                                   foreground="#d1d1d6")
        self.lbl_facts.grid(row=2, column=0, sticky=W, pady=(0, 12))

        self.btn_tv = ttk.Button(right, text="在 TradingView 打开", bootstyle="primary",
                                 command=self._open_tv, state=DISABLED)
        # 按钮跨两列, 恢复原先占满右栏底部的宽度
        self.btn_tv.grid(row=3, column=0, sticky=NSEW, pady=(8, 0), ipady=6)

        # 关注列表: 用 place 浮在右栏右侧空白区(K线图左对齐后, 其右侧留白本就空着)
        # 关键: 不另开一列抢宽度 -> 图表容器保持原宽, K线图尺寸不变, 仅占用原有留白
        watch = ttk.Frame(right, width=360, padding=(0, 0))
        watch.place(relx=1.0, x=-8, rely=0.0, relheight=1.0, height=-56, anchor="ne")
        watch.grid_propagate(False)
        self.watch_frame = watch  # 供几何自检/后续使用
        watch.rowconfigure(1, weight=1)
        watch.columnconfigure(0, weight=1)
        ttk.Label(watch, text="关注列表", font=("Microsoft YaHei", 12, "bold"),
                  foreground="#a1a1a6").grid(row=0, column=0, sticky=W, padx=4, pady=(0, 12))
        watch_cols = ("序号", "代码", "名称")
        self.watch_tree = ttk.Treeview(watch, columns=watch_cols, show="headings")
        # 宽度: 序号 40, 代码 130, 名称可伸缩(与信号清单三列宽度一致)
        watch_widths = (40, 130, 120)
        watch_anchors = (CENTER, W, W)
        for c, w, a in zip(watch_cols, watch_widths, watch_anchors):
            self.watch_tree.heading(c, text=c)
            self.watch_tree.column(c, width=w, minwidth=w, anchor=a, stretch=(c == "名称"))
        self.watch_tree.grid(row=1, column=0, sticky=NSEW)
        self.watch_tree.bind("<<TreeviewSelect>>", self._on_watchlist_select)
        # 关注列表保持白色, 仅悬停变灰
        self.watch_tree.tag_configure("hover", background="#4a4a4e")
        self.watch_tree.bind("<Motion>", self._on_watchlist_motion)
        self.watch_tree.bind("<Leave>", self._on_watchlist_leave)
        self._watch_hover_iid = None

        # ---- 状态栏 ----
        status = ttk.Frame(self.root, padding=(18, 8))
        status.grid(row=2, column=0, sticky=NSEW)
        status.columnconfigure(0, weight=1)  # 进度条占满剩余宽度
        # 进度条: 默认隐藏, 数据同步/扫描时显示
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(status, variable=self.progress_var,
                                            maximum=100, mode="determinate",
                                            bootstyle="primary-striped")
        self.progress_bar.grid(row=0, column=0, sticky=EW, padx=(0, 12))
        self.progress_bar.grid_remove()
        # 状态文字: 数据新鲜度 / 操作进度 / 完成摘要
        ttk.Label(status, textvariable=self.status_var, font=("Consolas", 10),
                  foreground="#a1a1a6").grid(row=0, column=1, sticky=E, padx=(8, 0))

    # ================= 数据读取 =================
    def _is_weekly(self):
        """当前是否处于周线模式 (只取用户手动选择的结果, 不做任何自动判断)。"""
        return self.tf_var.get() == "weekly"

    def _on_timeframe_change(self):
        """手动切换日线/周线: 两个周期的信号完全隔离, 切换后各自回到自己最新的信号日。"""
        # 同一个日期下拉在不同周期下的含义不同: 「截至哪一周」/「信号日」
        if self._is_weekly():
            self.date_label.configure(text="截至周")
        else:
            self.date_label.configure(text="信号日")
        # 仅周线模式显示"已确认/待建仓"切换, 日线/月线隐藏
        if self._is_weekly():
            self.view_combo.grid()
        else:
            self.view_combo.grid_remove()
        self.view_var.set("已确认")  # 切周期时重置为已确认视图
        self.selected_date = None  # 置空 -> _refresh 自动定位到该周期的最新信号日
        self._refresh()

    def _tf_sql(self):
        """当前周期的 SQL 过滤片段 (无参数, 条件为常量)。

        日线: 只取 timeframe='daily'
        周线: 只取 timeframe='weekly', 并排除历史回测回填
              (判据: 扫描日期与入库日期同一天 = 真实扫描; 回填的扫描日期是历史日期)
              ⚠️ 该判据只对周线成立, 日线存在跨午夜扫描导致两日期差一天的情况, 不可套用。
        """
        if self.tf_var.get() == "weekly":
            return " AND timeframe='weekly' AND date(scan_date)=date(created_at)"
        return " AND timeframe='daily'"

    def _scope_where(self):
        """选中日的范围口径。返回 (SQL 片段, 该片段所需参数元组)。

        日线: 只看「那一天触发」的信号 — 日线每天都有新货, 按天切片没问题。
        周线: 看「截至那一周仍存活」的信号 — 周线信号会活好几周,
              按天切片会让最新周只剩一两条(不是信号少, 是切法不对)。
              存活 = 信号已出现(触发日 <= 那一周) 且 尚未了结(无了结日, 或了结日在那一周之后)。
        """
        if self._is_weekly():
            return (" AND signal_date<=?"
                    " AND (resolved_date IS NULL OR resolved_date='' OR resolved_date>?)",
                    (self.selected_date, self.selected_date))
        return " AND signal_date=?", (self.selected_date,)

    def _date_title(self):
        """当前选中日在标题里的说法: 日线=那一天, 周线=截至那一周。"""
        d = self.selected_date or "—"
        if self._is_weekly():
            return f"截至 {d} 那周"
        return f"{d} 信号"

    def _weekly_fridays(self):
        """返回 weekly_bars 中所有可用周线日期, 统一到当周周五, 降序(最新在前)。"""
        try:
            with _db() as conn:
                rows = conn.execute(
                    "SELECT DISTINCT trade_date FROM weekly_bars "
                    "WHERE trade_date LIKE ? ORDER BY trade_date DESC",
                    (_DATE_LIKE,),
                ).fetchall()
            dates = []
            for (d,) in rows:
                if not d:
                    continue
                try:
                    dt = datetime.datetime.strptime(d, "%Y-%m-%d").date()
                    # 统一到当周周五: 周线视角下用户认的是"第几周"
                    friday = dt + datetime.timedelta(days=(4 - dt.weekday()))
                    dates.append(friday.strftime("%Y-%m-%d"))
                except ValueError:
                    continue
            # 去重并保持降序
            seen = set()
            result = []
            for d in dates:
                if d not in seen:
                    seen.add(d)
                    result.append(d)
            return result
        except Exception:  # noqa: BLE001
            return []

    def _signal_dates(self):
        """返回所有真实信号日, 降序(最新在前), 过滤脏日期与无效策略。

        日线: 从 signal_archive 取有真实信号的那一天。
        周线: 从 weekly_bars 取所有可用周线日期(统一到周五), 这样即使某周没有新命中,
              用户也能切到那一周看"截至该周仍存活"的信号。
        """
        if self._is_weekly():
            return self._weekly_fridays()
        try:
            with _db() as conn:
                rows = conn.execute(
                    f"SELECT DISTINCT signal_date FROM signal_archive "
                    f"WHERE signal_date LIKE ? AND {_STRAT_NORM_SQL} IN ({_KEY_PH}) "
                    f"{self._tf_sql()} "
                    f"ORDER BY signal_date DESC",
                    (_DATE_LIKE,) + _VALID_KEYS,
                ).fetchall()
                return [r[0] for r in rows if r[0]]
        except Exception:  # noqa: BLE001
            return []

    def _latest_signal_date(self):
        # 取「含真实有效信号」的最近一天, 避开 99 / 2099 等历史脏日期
        dates = self._signal_dates()
        return dates[0] if dates else None

    def _refresh(self):
        dates = self._signal_dates()
        self._valid_dates = set(dates)
        if not dates:
            self.selected_date = None
            self._clear_date_combos()
            self.list_title.configure(text="今日信号 (暂无扫描记录)")
            self._clear_sidebar()
            self._clear_detail()
            self._update_status(None, {})
            self._refresh_watchlist()
            return

        # 若当前选中日不在列表(或从未选), 默认切到最新
        if self.selected_date not in self._valid_dates:
            self.selected_date = dates[0]

        self._refresh_dates(dates)
        self._refresh_content()

    def _clear_date_combos(self):
        for c in (self.year_combo, self.month_combo, self.day_combo):
            c.configure(values=[])
            c.set("")

    def _refresh_dates(self, dates):
        """根据可用信号日更新年/月/日下拉框，并静默定位到 selected_date。"""
        # 构造年/月/日索引
        years = sorted({d[:4] for d in dates}, reverse=True)
        self._months_by_year = {}
        self._days_by_ym = {}
        for d in dates:
            y, m, day = d[:4], d[5:7], d[8:10]
            self._months_by_year.setdefault(y, set()).add(m)
            self._days_by_ym.setdefault((y, m), set()).add(day)
        for y in self._months_by_year:
            self._months_by_year[y] = sorted(self._months_by_year[y])
        for ym in self._days_by_ym:
            self._days_by_ym[ym] = sorted(self._days_by_ym[ym])

        # 默认到 selected_date
        y, m, day = self.selected_date[:4], self.selected_date[5:7], self.selected_date[8:10]

        self.year_combo.configure(values=years)
        self.year_var.set(y)

        months = self._months_by_year.get(y, [])
        self.month_combo.configure(values=months)
        self.month_var.set(m if m in months else (months[0] if months else ""))

        days = self._days_by_ym.get((y, m), [])
        self.day_combo.configure(values=days)
        self.day_var.set(day if day in days else (days[0] if days else ""))

    def _refresh_content(self):
        """按 selected_date 刷新策略计数、列表、详情、状态。不重建日期下拉。"""
        counts = {}
        if self.selected_date:
            scope, sparams = self._scope_where()
            try:
                with _db() as conn:
                    for strat, cnt in conn.execute(
                        f"SELECT {_STRAT_NORM_SQL} AS s, COUNT(*) FROM {_DEDUP_FROM} "
                        f"WHERE 1=1{scope} AND {_STRAT_NORM_SQL} IN ({_KEY_PH})"
                        f"{self._tf_sql()} GROUP BY s",
                        sparams + _VALID_KEYS,
                    ):
                        counts[strat] = cnt
            except Exception:  # noqa: BLE001
                pass

        self._build_sidebar(counts)

        # 周线待建仓视图: 读扫描产物中的 pending 信号, 不动库
        if self._is_weekly() and self.view_var.get() == "待建仓":
            self._show_pending_list()
            self._update_status(self.selected_date, counts)
            self._refresh_watchlist()
            return

        keys = self._sidebar_strategies()
        for k in counts:
            if k not in keys:
                keys.append(k)

        first = next((k for k in keys if counts.get(k, 0) > 0), keys[0] if keys else None)
        if first:
            self._on_strategy(first)
        else:
            self.list_title.configure(text=f"{self._date_title()} (无命中)")
            self._clear_detail()
        self._update_status(self.selected_date, counts)
        self._refresh_watchlist()

    def _on_view_change(self, event=None):
        """周线模式下切换"已确认 / 待建仓"视图。"""
        self._refresh_content()

    def _load_pending_signals(self, filter_strategy=None):
        """读取周线扫描产物 weekly_gap_watchlist.json 中 is_pending=True 的信号, 不动库。"""
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "weekly_gap_watchlist.json")
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            sigs = data.get("signals_gap", [])
            rows = []
            for s in sigs:
                if not s.get("is_pending"):
                    continue
                if filter_strategy and s.get("strategy_name") != filter_strategy:
                    continue
                rows.append({
                    "code": s.get("code", ""),
                    "name": s.get("name", ""),
                    "strategy": s.get("strategy_name", ""),
                    "entry_price": s.get("entry"),
                    "sl_price": s.get("sl"),
                    "tp_price": s.get("tp"),
                    "signal_date": s.get("date"),
                    "sig_quality": s.get("sig_quality", 0),
                    "bears": s.get("bears", 0),
                    "is_pending": True,
                })
            return rows
        except Exception:  # noqa: BLE001
            return []

    def _show_pending_list(self, filter_strategy=None):
        """显示待建仓列表 (周线模式专用)。"""
        rows = self._load_pending_signals(filter_strategy)
        self.current_rows = rows
        self._populate_list(rows)
        disp = strategy_display(filter_strategy) if filter_strategy else "全部"
        tail = " (无扫描产物或文件为空)" if not rows else ""
        self.list_title.configure(
            text=f"待建仓 · {disp} · 共 {len(rows)} 只{tail}"
        )
        if rows:
            self.tree.selection_set(self.tree.get_children()[0])
            self._on_select(None)
        else:
            self._clear_detail()

    def _on_year_change(self, event=None):
        y = self.year_var.get()
        if not y:
            return
        months = self._months_by_year.get(y, [])
        self.month_combo.configure(values=months)
        cur_m = self.month_var.get()
        if cur_m not in months:
            cur_m = months[0] if months else ""
        self.month_var.set(cur_m)
        self._on_month_change()

    def _on_month_change(self, event=None):
        y = self.year_var.get()
        m = self.month_var.get()
        if not y or not m:
            return
        days = self._days_by_ym.get((y, m), [])
        self.day_combo.configure(values=days)
        cur_d = self.day_var.get()
        if cur_d not in days:
            cur_d = days[0] if days else ""
        self.day_var.set(cur_d)
        self._on_day_change()

    def _on_day_change(self, event=None):
        y, m, d = self.year_var.get(), self.month_var.get(), self.day_var.get()
        if not (y and m and d):
            return
        chosen = f"{y}-{m}-{d}"
        if chosen in self._valid_dates and chosen != self.selected_date:
            self.selected_date = chosen
            self._refresh_content()

    def _clear_sidebar(self):
        for w in self.sidebar_inner.winfo_children():
            w.destroy()
        self.strat_buttons = {}

    def _sidebar_strategies(self):
        """当前周期允许的策略 key (决定左栏按钮), 保持 STRATEGY_ORDER 展示顺序。

        周线只显示缺口家族三个; 日线显示其周期内策略。避免把另一周期的
        策略按钮(如日线的 3K/MTR)误摆在周线左栏。
        """
        tf = "weekly" if self._is_weekly() else "daily"
        try:
            from core.strategy_registry import StrategyRegistry
            valid = set(StrategyRegistry.get_strategies_by_timeframe(tf))
        except Exception:  # noqa: BLE001 - 注册表不可用则退回全量
            valid = set(STRATEGY_ORDER)
        return [k for k in STRATEGY_ORDER if k in valid]

    def _build_sidebar(self, counts):
        self._clear_sidebar()
        keys = self._sidebar_strategies()
        for k in counts:
            if k not in keys:
                keys.append(k)
        for k in keys:
            disp = strategy_display(k)
            n = counts.get(k, 0)
            b = ttk.Button(self.sidebar_inner, text=f"{disp}   {n}",
                           bootstyle="primary" if k == self.current_strategy else "light",
                           command=lambda kk=k: self._on_strategy(kk))
            b.pack(fill=X, pady=3, anchor=W, ipady=4)
            self.strat_buttons[k] = b

    def _on_strategy(self, key):
        self.current_strategy = key
        for k, b in self.strat_buttons.items():
            b.configure(bootstyle="primary" if k == key else "light")

        # 周线待建仓视图下, 策略按钮用于过滤 pending 信号
        if self._is_weekly() and self.view_var.get() == "待建仓":
            self._show_pending_list(filter_strategy=key)
            return

        rows = []
        if self.selected_date:
            scope, sparams = self._scope_where()
            try:
                with _db() as conn:
                    col_names = [d[0] for d in conn.execute("SELECT * FROM signal_archive LIMIT 0").description]
                    # 按代码稳定排序 (库内无可靠的信号质量分, 不做假排名)
                    # 周线按「截至该周仍存活」取, 并按状态分组排(先活口后了结), 便于交易员盯盘
                    order = ("ORDER BY CASE WHEN status IN ('PENDING','ACTIVE') THEN 0 ELSE 1 END,"
                             " code ASC" if self._is_weekly() else "ORDER BY code ASC")
                    sql = (f"SELECT * FROM {_DEDUP_FROM} WHERE 1=1{scope} "
                           f"AND {_STRAT_NORM_SQL}=?{self._tf_sql()} "
                           f"{order} LIMIT 100")
                    for r in conn.execute(sql, sparams + (key,)):
                        rows.append(dict(zip(col_names, r)))
            except Exception:  # noqa: BLE001
                pass

        self.current_rows = rows
        self._populate_list(rows)
        # 周线额外报存活条数: 清单里混着已了结的, 一眼看出还有几个活口
        alive = sum(1 for r in rows if r.get("status") in ("PENDING", "ACTIVE"))
        tail = f" · 存活 {alive}" if self._is_weekly() and rows else ""
        self.list_title.configure(
            text=f"{self._date_title()} · {strategy_display(key)} · 共 {len(rows)} 只{tail}"
        )
        if rows:
            self.tree.selection_set(self.tree.get_children()[0])
            self._on_select(None)
        else:
            self._clear_detail()

    def _make_dot_image(self):
        """生成已关注的红色圆点图片(14x14), 画在树形列 #0, 便于看清/点击。"""
        img = tk.PhotoImage(width=14, height=14)
        r = 6
        cx = cy = 7
        for y in range(14):
            for x in range(14):
                if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                    img.put("#ff375f", (x, y))
        return img

    def _populate_list(self, rows):
        for item in self.tree.get_children():
            self.tree.delete(item)
        fav = self._current_favorites()
        for i, row in enumerate(rows):
            code = row.get("code", "")
            marked = code in fav
            values = (
                i + 1,
                code,
                row.get("name") or "—",
            )
            self.tree.insert("", END, iid=str(i), values=values,
                             image=self._dot_img if marked else "")

    def _favorites_path(self):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "gui_favorites.json")

    def _load_favorites(self):
        """加载用户手动"关注"的股票, 按日线/周线隔离。

        新格式: {"daily": {code: name}, "weekly": {code: name}}
        兼容旧格式:
          - list -> 视为日线关注
          - dict(不含 daily/weekly 键) -> 视为日线关注
        """
        path = self._favorites_path()
        empty = {"daily": {}, "weekly": {}}
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return {"daily": {code: self._lookup_name_in_db(code) or code for code in data},
                            "weekly": {}}
                if isinstance(data, dict):
                    if "daily" in data or "weekly" in data:
                        return {
                            "daily": data.get("daily", {}),
                            "weekly": data.get("weekly", {}),
                        }
                    # 旧版单一字典 -> 全部视为日线关注
                    return {"daily": dict(data), "weekly": {}}
        except Exception as e:  # noqa: BLE001
            logging.warning(f"加载关注列表失败: {e}")
        return empty

    def _save_favorites(self):
        """持久化关注字典到 JSON, 按日线/周线隔离, 下次打开仍在。"""
        path = self._favorites_path()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.favorites, f, ensure_ascii=False, indent=2, sort_keys=True)
        except Exception as e:  # noqa: BLE001
            logging.warning(f"保存关注列表失败: {e}")

    def _current_favorites(self):
        """当前周期(daily/weekly)下的关注字典。"""
        return self.favorites.setdefault(self.tf_var.get(), {})

    def _toggle_favorite(self, iid=None):
        """切换指定行的关注状态; 不传 iid 则取当前选中行。立即更新列表与文件, 并同步关注列表。"""
        if iid is None:
            sel = self.tree.selection()
            if not sel:
                return
            iid = sel[0]
        values = list(self.tree.item(iid, "values"))
        if not values:
            return
        code = values[1]
        fav = self._current_favorites()
        if code in fav:
            del fav[code]
        else:
            # 存真实中文名; 当前查不到就存空串, 后续刷新会自动回填
            fav[code] = self._watchlist_name_for_code(code) or ""
        marked = code in fav
        # 只切换树形列 #0 的红点图片, 行文字列不变
        self.tree.item(iid, image=self._dot_img if marked else "")
        self._save_favorites()
        self._refresh_watchlist()

    def _on_tree_motion(self, event):
        """TV 式: 鼠标悬停在标记列时, 整行背景变灰。"""
        region = self.tree.identify("region", event.x, event.y)
        # 树形列 #0 在 ttk 里属于 "tree" 区域(非 "cell"), 也要允许悬停/点击
        if region not in ("cell", "tree"):
            self._clear_tree_hover()
            return
        iid = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if not iid or col != "#0":
            self._clear_tree_hover()
            return
        if self._tree_hover_iid == iid:
            return
        self._clear_tree_hover()
        self._tree_hover_iid = iid
        tags = list(self.tree.item(iid, "tags"))
        if "hover" not in tags:
            tags.append("hover")
            self.tree.item(iid, tags=tags)

    def _on_tree_leave(self, event=None):
        self._clear_tree_hover()

    def _clear_tree_hover(self):
        iid = self._tree_hover_iid
        if not iid:
            return
        self._tree_hover_iid = None
        tags = [t for t in self.tree.item(iid, "tags") if t != "hover"]
        self.tree.item(iid, tags=tags)

    def _on_tree_click(self, event):
        """TV 式: 点击标记列切换关注; 点击其他列保持选中行。"""
        region = self.tree.identify("region", event.x, event.y)
        # 树形列 #0 的区域是 "tree" 不是 "cell"; 点其它列不触发关注
        if region not in ("cell", "tree"):
            return
        iid = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if not iid or col != "#0":
            return
        self._toggle_favorite(iid)
        return "break"

    def _on_tree_right_click(self, event):
        """右键单击任意行切换关注(兜底交互, 避免树形列太难点不到)。"""
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        self._toggle_favorite(iid)

    def _refresh_watchlist(self):
        """把当前周期 favorites 同步到右侧关注列表, 最新关注的排在最上面。

        每次刷新都会重新解析中文名: 如果当前清单或库里已经能查到名字,
        就自动覆盖 favorites 里旧的名字(包括之前用代码兜底的情况)。
        """
        for item in self.watch_tree.get_children():
            self.watch_tree.delete(item)
        fav = self._current_favorites()
        changed = False
        # favorites 字典保持插入顺序, reversed 后最新加入的在上
        for idx, code in enumerate(reversed(list(fav.keys())), start=1):
            name = self._resolve_watchlist_name(code)
            if fav.get(code) != name:
                fav[code] = name
                changed = True
            self.watch_tree.insert("", END, iid=code, values=(idx, code, name))
        if changed:
            self._save_favorites()

    def _resolve_watchlist_name(self, code):
        """综合解析关注标的的中文名; 实在没有才用代码本身兜底。"""
        name = self._watchlist_name_for_code(code)
        if name and name != code:
            return name
        stored = self._current_favorites().get(code)
        if stored and stored != code:
            return stored
        return code

    def _watchlist_name_for_code(self, code):
        """根据代码查名称; 优先当前列表, 否则去 signal_archive 找最新一条。"""
        for row in self.current_rows:
            if row.get("code") == code:
                return row.get("name") or ""
        return self._lookup_name_in_db(code)

    def _lookup_name_in_db(self, code):
        """从 signal_archive 查代码最近一次出现的中文名称; 查不到返回空串。"""
        try:
            with _db() as conn:
                r = conn.execute(
                    "SELECT name FROM signal_archive WHERE code=? ORDER BY signal_date DESC LIMIT 1",
                    (code,),
                ).fetchone()
                if r and r[0]:
                    return r[0]
        except Exception:  # noqa: BLE001
            pass
        return ""

    def _on_watchlist_motion(self, event):
        """Watchlist 悬停变灰。"""
        iid = self.watch_tree.identify_row(event.y)
        if self._watch_hover_iid == iid:
            return
        self._clear_watchlist_hover()
        if not iid:
            return
        self._watch_hover_iid = iid
        tags = list(self.watch_tree.item(iid, "tags"))
        if "hover" not in tags:
            tags.append("hover")
            self.watch_tree.item(iid, tags=tags)

    def _on_watchlist_leave(self, event=None):
        self._clear_watchlist_hover()

    def _clear_watchlist_hover(self):
        iid = self._watch_hover_iid
        if not iid:
            return
        self._watch_hover_iid = None
        tags = [t for t in self.watch_tree.item(iid, "tags") if t != "hover"]
        self.watch_tree.item(iid, tags=tags)

    def _on_watchlist_select(self, event):
        """点击 Watchlist 行: 右侧显示该票 K 线。"""
        sel = self.watch_tree.selection()
        if not sel:
            return
        code = sel[0]
        row = None
        # 优先在当前策略列表里找完整信息
        for r in self.current_rows:
            if r.get("code") == code:
                row = r
                break
        if row is None:
            # 从库里取最新一条信号记录(可能跨日期/策略)
            try:
                with _db() as conn:
                    col_names = [d[0] for d in conn.execute("SELECT * FROM signal_archive LIMIT 0").description]
                    r = conn.execute(
                        f"SELECT * FROM signal_archive WHERE code=? "
                        f"AND {_STRAT_NORM_SQL} IN ({_KEY_PH}){self._tf_sql()} "
                        "ORDER BY signal_date DESC LIMIT 1",
                        (code,) + _VALID_KEYS,
                    ).fetchone()
                    if r:
                        row = dict(zip(col_names, r))
            except Exception:  # noqa: BLE001
                pass
        if not row:
            self.status_var.set(f"{code} 无本地信号记录")
            return
        self.current_detail_row = row
        self._render_detail_text(row)
        self.btn_tv.configure(state=NORMAL)
        self._show_thumbnail(row)

    # ================= 详情 =================
    def _on_select(self, event):
        sel = self.tree.selection()
        if not sel:
            return
        try:
            row = self.current_rows[int(sel[0])]
        except (ValueError, IndexError):
            return
        self.current_detail_row = row
        self._render_detail_text(row)
        self.btn_tv.configure(state=NORMAL)
        self._show_thumbnail(row)

    def _render_detail_text(self, row):
        self.lbl_strat.configure(
            text=f"{strategy_display(row.get('strategy', ''))}  {row.get('code', '')} {row.get('name') or ''}")
        # 只展示库里真实填实的字段, 不显示空列/假指标
        facts = [f"触发价  {_fmt(row.get('entry_price'))}"]
        if row.get("sl_price") is not None:
            facts.append(f"止损价  {_fmt(row.get('sl_price'))}")
        if row.get("tp_price") is not None:
            facts.append(f"目标价  {_fmt(row.get('tp_price'))}")
        facts.append(f"信号日  {row.get('signal_date') or '—'}")
        self.lbl_facts.configure(text="\n".join(facts))

    def _show_thumbnail(self, row):
        def _run():
            try:
                from tools.notifier import generate_chart_bytes
                code = row.get("code", "")
                name = row.get("name") or code
                strategy = row.get("strategy", "")
                sl = float(row.get("sl_price") or 0)
                entry = float(row.get("entry_price") or 0)
                tp = float(row.get("tp_price") or 0)
                if self._is_weekly():
                    # 周线模式必须画周K, 否则看不出"最新一周有没有走出建仓形态"
                    from core.scan_engine import fetch_weekly_data
                    from core.calculator import add_indicators
                    from core.strategy_registry import StrategyRegistry
                    df = fetch_weekly_data(code, weeks=300)
                    if df is None or df.empty:
                        return
                    df = add_indicators(df)
                    strat = StrategyRegistry.get_strategy(strategy)
                    df = strat.calculate_signals(df)
                    buf = generate_chart_bytes(
                        code, name, strategy, sl, tp1=tp, entry=entry,
                        df_override=df, timeframe='周K',
                        sig_quality=row.get('sig_quality', 0),
                        bears=row.get('bears', 0),
                    )
                else:
                    buf = generate_chart_bytes(
                        code, name, strategy, sl, tp1=tp, entry=entry,
                        timeframe='日K',
                    )
                if buf is None:
                    return
                from PIL import Image
                self._chart_orig = Image.open(buf)
                self._chart_last_size = (0, 0)
                self.root.after(0, self._fit_chart_to_label)
            except Exception as e:  # noqa: BLE001
                logging.warning(f"缩略图生成失败: {e}")
        threading.Thread(target=_run, daemon=True).start()

    def _on_chart_configure(self, event=None):
        # 窗口/面板尺寸变化时, 按可用空间等比重绘 K 线图
        self._fit_chart_to_label()

    def _fit_chart_to_label(self):
        if self._chart_orig is None:
            return
        try:
            from PIL import Image, ImageTk
            # 测量容器 Frame (尺寸仅由 grid 决定, 不受图片内容影响)
            label_w = max(1, self.chart_frame.winfo_width())
            label_h = max(1, self.chart_frame.winfo_height())
            pad = 20
            avail_w = max(1, label_w - pad)
            avail_h = max(1, label_h - pad)
            orig_w, orig_h = self._chart_orig.size
            scale = min(avail_w / orig_w, avail_h / orig_h)
            target_w = int(orig_w * scale)
            target_h = int(orig_h * scale)
            if target_w < 1 or target_h < 1:
                return
            if (target_w, target_h) == self._chart_last_size:
                return
            img = self._chart_orig.resize((target_w, target_h), Image.LANCZOS)
            self.photo = ImageTk.PhotoImage(img)
            self.chart_label.configure(image=self.photo, text="")
            self._chart_last_size = (target_w, target_h)
        except Exception as e:  # noqa: BLE001
            logging.warning(f"图表自适应失败: {e}")

    def _clear_detail(self):
        self.current_detail_row = None
        self._chart_orig = None
        self._chart_last_size = (0, 0)
        self.lbl_strat.configure(text="")
        self.lbl_facts.configure(text="")
        self.chart_label.configure(image="", text="选中左侧标的查看缩略图")
        self.btn_tv.configure(state=DISABLED)

    # ================= 动作 =================
    def _open_tv(self):
        if not self.current_detail_row:
            return
        webbrowser.open(_tv_url(self.current_detail_row.get("code", "")))

    def _on_search_focus(self, _event=None):
        txt = self.ent_code.get()
        if txt == self._search_placeholder:
            self.ent_code.delete(0, END)
            self.ent_code.config(foreground="#f5f5f7")

    def _on_search_blur(self, _event=None):
        txt = self.ent_code.get().strip()
        if not txt:
            self.ent_code.insert(0, self._search_placeholder)
            self.ent_code.config(foreground="#8e8e93")

    def _open_tv_for_entry(self):
        code = self.ent_code.get().strip()
        if code and code != self._search_placeholder:
            webbrowser.open(_tv_url(code))

    def start_sync(self):
        """下载行情: 按顶部所选周期路由 (日线走日线同步, 周线走周线同步)。"""
        if not self._sync_ok:
            return
        if self._is_weekly() and not update_weekly_data_batch:
            self.status_var.set("周线同步模块不可用")
            return
        cb = self._make_progress_cb("下载行情")
        self._set_busy(True)
        self._show_progress()
        self.status_var.set("周线行情下载中…" if self._is_weekly() else "行情下载中…")

        def _task():
            if self._is_weekly():
                return update_weekly_data_batch(progress_callback=cb, cancel_event=self._stop_event)
            return update_daily_data_batch(progress_callback=cb, cancel_event=self._stop_event)

        self._run_thread(_task, "行情下载",
                         on_done=self._on_sync_done,
                         on_fail=lambda e: self.status_var.set(f"行情下载失败: {e}"))

    def _on_sync_done(self, ret):
        """行情下载收尾文案: 区分 用户终止 / 正常完成 / 无新数据。"""
        if self._stop_event.is_set():
            self.status_var.set("已终止 · 已下载的部分已入库")
        elif isinstance(ret, tuple):
            self.status_var.set(f"行情下载完成 · 下载 {ret[0]}/{ret[1]} 只")
        else:
            self.status_var.set("行情下载完成 (无新数据)")

    def start_hunter(self):
        """策略扫描: 日线走原流水线, 周线走周线引擎。"""
        if self._is_weekly():
            self._start_scan_weekly()
        else:
            self._start_scan_daily()

    def _start_scan_daily(self):
        if not self._hunter_ok:
            return
        use_ai = bool(self.ai_var.get())
        cb = self._make_progress_cb("策略扫描", phase2="分类与出图中…")
        self._set_busy(True)
        self._show_progress()
        self.status_var.set("策略扫描中… (AI 复核开)" if use_ai else "策略扫描中… (离线)")

        def _task():
            codes = get_stock_list()
            if not codes:
                logging.warning("本地数据库为空, 请先下载行情")
                return "NO_CODES"
            return main_script.run_pipeline_once(codes, use_ai=use_ai,
                                                 progress_callback=cb, cancel_event=self._stop_event)

        self._run_thread(_task, "策略扫描",
                         on_done=self._on_scan_done,
                         on_fail=lambda e: self.status_var.set(f"策略扫描失败: {e}"))

    def _start_scan_weekly(self):
        """周线扫描: 结果直接推送并归档进库, 界面从库里读 (与日线一致的数据流)。"""
        if not run_weekly_scan:
            self.status_var.set("周线扫描模块不可用")
            return
        # 周线只跑 3 个 gap 家族策略 (用户 2026-08-30 拍板), 故总进度 = 股票数本身,
        # 不再有"两个家族 × 2"的翻倍, 用普通进度回调即可。
        cb = self._make_progress_cb("周线扫描")
        self._set_busy(True)
        self._show_progress()
        self.status_var.set("周线扫描中… (缺口家族)")

        def _task():
            codes = get_stock_list()
            if not codes:
                logging.warning("本地数据库为空, 请先下载行情")
                return "NO_CODES"
            try:
                from core.strategy_registry import StrategyRegistry
                strats = StrategyRegistry.get_strategies_by_timeframe("weekly")
            except Exception:  # noqa: BLE001 - 注册表不可用则退回已知周线策略 (仅 gap 家族, 不含 3K)
                strats = ["STRATEGY_STRUCTURAL_GAP", "STRATEGY_GAP_PINBAR",
                          "STRATEGY_GAP_H2"]
            stats = run_weekly_scan(strats, weeks=4, all_codes=codes,
                                    progress_callback=cb, cancel_event=self._stop_event)

            # 🟢 扫描后顺带把周线信号的状态推进一遍(进场/止盈/止损/过期)。
            #    零自动化: 这是同一个手动动作里的第二步, 不是定时任务。
            #    🔴 必须跑两轮才收敛 — _track_pending 把「待确认」推进到「已入场」后,
            #       本轮不再检查持仓是否超期, 要等下一轮才结得掉(周线持仓上限 20 根周线)。
            #       补跑实测: 第1轮了结 185 条, 第2轮归零。只跑一轮会剩下一堆假活口。
            #    real_scan_only=True: 跳过 2026-05 那批回测回填数据, 不污染回测口径。
            if track_signals and not self._stop_event.is_set():
                self.root.after(0, self._apply_progress, 100, "跟进信号状态…")
                for _round in range(2):
                    track_signals(timeframe="weekly", real_scan_only=True)
            # 回执用专属 key 包一层: 日线 run_pipeline_once 的返回值绝不会带 weekly_stats
            return {"weekly_stats": stats} if isinstance(stats, dict) else None

        self._run_thread(_task, "周线扫描",
                         on_done=self._on_scan_done,
                         on_fail=lambda e: self.status_var.set(f"周线扫描失败: {e}"))

    def _on_scan_done(self, ret):
        """策略扫描收尾文案: 区分 用户终止 / 无本地数据 / 正常完成。"""
        if self._stop_event.is_set():
            self.status_var.set("已终止 · 已扫描部分已保留, 未推送")
        elif ret == "NO_CODES":
            self.status_var.set("本地数据库为空, 请先点击「下载行情」")
        else:
            extra = " · 信号状态已跟进" if self._is_weekly() and track_signals else ""
            if isinstance(ret, dict) and "weekly_stats" in ret:
                self.status_var.set(self._weekly_scan_summary(ret["weekly_stats"]) + extra)
            else:
                self.status_var.set(f"策略扫描完成 · 清单已刷新{extra}")

    @staticmethod
    def _weekly_scan_summary(s):
        """把周线扫描回执翻译成一句人话。

        为什么要这句: 扫描跑完只说"完成"两个字, 你分不清"扫了但 0 命中"
        和"压根没扫"。后者是真 bug, 必须能一眼看出来。
        """
        if not s.get("ran_gap"):
            return "周线扫描完成 · 未识别到周线缺口策略"
        return (f"周线扫描完成 · 扫 {s.get('stocks', 0)} 只 · "
                f"缺口家族 {s.get('gap', 0)} 条")

    def _run_thread(self, func, name, on_done=None, on_fail=None):
        def _wrap():
            ok = False
            ret = None
            err = None
            try:
                logging.info(f"{name} 启动")
                ret = func()
                ok = True
                logging.info(f"{name} 完成")
            except Exception as e:  # noqa: BLE001
                err = e
                logging.error(f"{name} 异常: {e}")
            # 统一在 UI 线程收尾: 先刷新清单/状态, 再写结论, 最后恢复按钮并收起进度条
            def _finish():
                self._refresh()
                if ok and on_done is not None:
                    on_done(ret)
                elif not ok and on_fail is not None:
                    on_fail(err)
                elif not ok:
                    self.status_var.set(f"{name} 失败: {err}")
                self._set_busy(False)
                self._hide_progress()
            self.root.after(0, _finish)
        threading.Thread(target=_wrap, daemon=True).start()

    # ================= 进度条 =================
    def _show_progress(self):
        """操作开始: 显示进度条。"""
        self.progress_bar.grid()  # 取消 grid_remove 的隐藏

    def _hide_progress(self):
        """操作结束: 收起进度条并归零。"""
        self.progress_bar.grid_remove()
        self.progress_var.set(0)

    # ================= 手动终止 =================
    def _set_busy(self, busy):
        """操作运行中: 禁用两个动作按钮、显示红色"终止"; 结束后恢复。"""
        if busy:
            self.btn_hunter.configure(state=DISABLED)
            self.btn_sync.configure(state=DISABLED)
            self._stop_event.clear()
            self.btn_stop.pack(side=LEFT, padx=5, after=self.btn_sync)
            self.btn_stop.configure(state=NORMAL)
        else:
            self.btn_hunter.configure(state=NORMAL if self._hunter_ok else DISABLED)
            self.btn_sync.configure(state=NORMAL if self._sync_ok else DISABLED)
            self.btn_stop.pack_forget()

    def _on_stop(self):
        """红色"终止"被点击: 置位终止信号, 后台循环下一只股票处停下。"""
        self._stop_event.set()
        self.btn_stop.configure(state=DISABLED)
        self.status_var.set("正在终止…")

    def _make_progress_cb(self, label, phase2=None):
        """生成后台进度回调(在后台线程被调用), 经 root.after 安全更新 UI。

        label : 操作名(如 '下载行情' / '策略扫描')
        phase2: 扫描阶段1完成(done>=total)后显示的后续阶段文字(如 '分类与出图中…')
        """
        last = {"pct": -1}

        def cb(done, total, info=0):
            pct = int(done * 100 / total) if total else 0
            if pct == last["pct"]:
                return  # 节流: 百分比不变不刷新, 避免 UI 线程被打爆
            last["pct"] = pct
            if done >= total and phase2:
                text = phase2
            elif done >= total:
                text = f"{label} 完成 ({pct}%)"
            else:
                extra = f" · 命中 {info}" if label in ("策略扫描", "周线扫描") else ""
                text = f"{label} {done}/{total} · {pct}%{extra}"
            self.root.after(0, self._apply_progress, pct, text)

        return cb

    # 注: 原 _make_weekly_progress_cb (把 3312×2=6624 拆成"缺口家族/3K"两段显示)
    # 已于 2026-08-30 删除 —— 周线不再跑 3K, 总进度回归股票数本身, 用通用的
    # _make_progress_cb('周线扫描') 即可, 无需专门的分阶段回调。

    def _apply_progress(self, pct, text):
        self.progress_bar.grid()
        self.progress_var.set(pct)
        self.status_var.set(text)

    def _update_status(self, signal_date, counts=None):
        try:
            with _db() as conn:
                # 周线看周线库、日线看日线库: 两个周期的数据新鲜度互不相干
                freshness_sql = ("SELECT MAX(trade_date) FROM weekly_bars"
                                 if self._is_weekly() else
                                 "SELECT MAX(trade_date) FROM daily_bars")
                d = conn.execute(freshness_sql).fetchone()[0]
                # 「上次周线扫描」= 最近一次真实周线扫描的入库日。
                # 判定口径与 _tf_sql 一致(扫描日=入库日), 用于排除 2026-05 那批历史回测回填。
                last_scan = None
                if self._is_weekly():
                    last_scan = conn.execute(
                        "SELECT MAX(date(created_at)) FROM signal_archive "
                        "WHERE timeframe='weekly' AND date(scan_date)=date(created_at)"
                    ).fetchone()[0]
                n = 0
                alive_n = 0
                if signal_date:
                    scope, sparams = self._scope_where()
                    n = conn.execute(
                        f"SELECT COUNT(*) FROM {_DEDUP_FROM} WHERE 1=1{scope} "
                        f"AND {_STRAT_NORM_SQL} IN ({_KEY_PH}){self._tf_sql()}",
                        sparams + _VALID_KEYS,
                    ).fetchone()[0]
                    if self._is_weekly():
                        # 周线额外报「仍存活」条数: 未了结 = 待确认/已入场
                        alive_n = conn.execute(
                            f"SELECT COUNT(*) FROM {_DEDUP_FROM} WHERE 1=1{scope} "
                            f"AND status IN ('PENDING','ACTIVE') "
                            f"AND {_STRAT_NORM_SQL} IN ({_KEY_PH}){self._tf_sql()}",
                            sparams + _VALID_KEYS,
                        ).fetchone()[0]
            # scan_date 列含历史脏数据(99/97...), 不可信, 不显示; 仅展示真实数据日期与信号日。
            # 🔴 红线 B: 状态栏只报数(数据到哪天/上次哪天扫的), 不做任何"该跑了"的提醒或建议。
            if self._is_weekly():
                self.status_var.set(
                    f"就绪 · 周线数据 {d or '—'} · 上次周线扫描 {last_scan or '—'}"
                    f" · 截至 {signal_date or '—'} 那周 · 共 {n} 条(存活 {alive_n})"
                )
            else:
                self.status_var.set(
                    f"就绪 · 数据 {d or '—'} · 信号日 {signal_date or '—'} · 共 {n} 标的"
                )
        except Exception as e:  # noqa: BLE001
            self.status_var.set(f"就绪 · 数据库读取失败: {e}")

    def on_closing(self):
        self.root.destroy()
        sys.exit(0)


def main():
    # Windows pythonw + multiprocessing: 必须 freeze_support, 否则子进程会递归启动 GUI
    try:
        from multiprocessing import freeze_support
        freeze_support()
    except Exception:  # noqa: BLE001
        pass

    app = ttk.Window(themename="darkly", title="Brooks-AI 操盘台")
    # 深色主题: 保留 Apple 蓝作为强调色, 其余交给 darkly 默认 palette
    try:
        app.style.colors.primary = "#007AFF"
        app.style.configure("Treeview", rowheight=34)
        app.style.configure("Treeview.Heading", font=("Microsoft YaHei", 11, "bold"))
    except Exception:  # noqa: BLE001
        pass
    app.geometry("1600x1000+80+40")
    app.minsize(1280, 760)

    dashboard = TradingDashboard(app)
    app.protocol("WM_DELETE_WINDOW", dashboard.on_closing)
    try:
        app.mainloop()
    except KeyboardInterrupt:
        dashboard.on_closing()


if __name__ == "__main__":
    main()

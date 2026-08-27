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

import tkinter as tk
import ttkbootstrap as ttk
from ttkbootstrap.constants import (  # 显式导入, 禁止 import *
    BOTH, LEFT, RIGHT, X, NSEW, EW, END, W, E, CENTER, HORIZONTAL, DISABLED, NORMAL,
)

# ---- 业务模块 (优雅降级: 导入失败则对应按钮禁用, 界面仍可开) ----
# 真实入口 (经 grep 核实, 非凭记忆):
#   同步   = core.data_provider.update_daily_data_batch
#   股票清单 = core.data_provider.get_stock_list   (扫描需要传入 all_codes)
#   扫描   = hunter.run_pipeline_once
update_daily_data_batch = None
get_stock_list = None
main_script = None
try:
    from core.data_provider import update_daily_data_batch, get_stock_list
except Exception as e:  # noqa: BLE001 - 允许降级, 不阻断界面
    logging.warning(f"数据同步模块导入失败(同步按钮将不可用): {e}")
try:
    import hunter as main_script
except Exception as e:  # noqa: BLE001 - 允许降级, 不阻断界面
    logging.warning(f"扫描模块导入失败(扫描按钮将不可用): {e}")


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


# 只统计这 6 个现行策略 key, 过滤历史脏数据 (STRUCTURAL_GAP / MTR_V35_STRUCTURAL / TEST_STRAT / UNKNOWN 等)
_VALID_KEYS = tuple(STRATEGY_ORDER)
_KEY_PH = ",".join("?" * len(_VALID_KEYS))
_DATE_LIKE = "____-__-__"  # SQLite LIKE: 匹配 YYYY-MM-DD


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
        self._chart_orig = None  # 原始 K 线 PIL Image, 用于自适应重绘
        self._chart_last_size = (0, 0)  # 上次渲染尺寸, 避免 resize 死循环
        self._hunter_ok = bool(main_script and get_stock_list)  # 扫描模块是否可用
        self._sync_ok = bool(update_daily_data_batch)  # 行情下载模块是否可用
        self._stop_event = threading.Event()  # 手动终止信号: 运行中置位, 后台循环检查
        self.favorites = self._load_favorites()  # 用户手动标记的 "关注" 集合
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
        ttk.Label(top, text="信号日", font=("Microsoft YaHei", 10),
                  foreground="#a1a1a6").pack(side=LEFT, padx=(20, 4))
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

        # ---- 主区四栏 ----
        main = ttk.Frame(self.root, padding=(16, 12))
        main.grid(row=1, column=0, sticky=NSEW)
        # 左栏/中栏固定宽度(导航用), 右栏随窗口缩放(给图表最大空间)
        main.columnconfigure(0, weight=0, minsize=160)
        main.columnconfigure(1, weight=0, minsize=180)
        main.columnconfigure(2, weight=0, minsize=360)
        main.columnconfigure(3, weight=1)
        main.rowconfigure(0, weight=1)

        # 最左栏: 关注列表 (Watchlist), 单列, 复制策略清单里被标红的标的
        watch = ttk.Frame(main, width=160, padding=(0, 0))
        watch.grid(row=0, column=0, sticky=NSEW, padx=(0, 16))
        watch.rowconfigure(1, weight=1)
        watch.grid_propagate(False)
        ttk.Label(watch, text="关注列表", font=("Microsoft YaHei", 12, "bold"),
                  foreground="#a1a1a6").pack(anchor=W, pady=(0, 10))
        self.watch_tree = ttk.Treeview(watch, columns=("名称",), show="headings")
        self.watch_tree.heading("名称", text="名称")
        self.watch_tree.column("名称", width=140, minwidth=100, anchor=W)
        self.watch_tree.pack(fill=BOTH, expand=True)
        self.watch_tree.bind("<<TreeviewSelect>>", self._on_watchlist_select)
        # TV 式: 悬停变灰, 选中变红
        self.watch_tree.tag_configure("hover", background="#4a4a4e")
        self.watch_tree.tag_configure("favorite", foreground="#ff375f")
        self.watch_tree.bind("<Motion>", self._on_watchlist_motion)
        self.watch_tree.bind("<Leave>", self._on_watchlist_leave)
        self._watch_hover_iid = None

        # 左栏: 策略导航 (收窄, 只放名字+数量)
        side = ttk.Frame(main, width=180, padding=(0, 0))
        side.grid(row=0, column=1, sticky=NSEW, padx=(0, 16))
        side.rowconfigure(1, weight=1)
        side.grid_propagate(False)
        ttk.Label(side, text="策略", font=("Microsoft YaHei", 12, "bold"),
                  foreground="#a1a1a6").pack(anchor=W, pady=(0, 10))
        self.sidebar_inner = ttk.Frame(side)
        self.sidebar_inner.pack(fill=BOTH, expand=True)

        # 中栏: 清单 (紧凑导航)
        center = ttk.Frame(main, width=360, padding=(0, 0))
        center.grid(row=0, column=2, sticky=NSEW)
        center.grid_propagate(False)
        center.rowconfigure(1, weight=1)
        center.columnconfigure(0, weight=1)
        self.list_title = ttk.Label(center, text="今日信号", font=("Microsoft YaHei", 14, "bold"),
                                    foreground="#f5f5f7")
        self.list_title.grid(row=0, column=0, sticky=W, padx=4, pady=(0, 12))

        # 信号清单列: [标记] | 序号 | 代码 | 名称
        cols = (" ", "序号", "代码", "名称")
        # 深色主题下用默认 Treeview, 不强制 light 变体
        self.tree = ttk.Treeview(center, columns=cols, show="headings")
        # 宽度: 标记列 30(只放红点), 序号 40, 代码 130, 名称唯一可伸缩
        widths = (30, 40, 130, 145)
        anchors = (CENTER, CENTER, W, W)
        for c, w, a in zip(cols, widths, anchors):
            self.tree.heading(c, text=c)
            self.tree.column(c, width=w, minwidth=w, anchor=a, stretch=(c == "名称"))
        self.tree.grid(row=1, column=0, sticky=NSEW)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        # TV 式关注效果: 悬停变灰, 点击标记列标红; 已关注行整体红字
        self.tree.tag_configure("favorite", foreground="#ff375f")
        self.tree.tag_configure("hover", background="#4a4a4e")
        self.tree.bind("<Motion>", self._on_tree_motion)
        self.tree.bind("<Leave>", self._on_tree_leave)
        self.tree.bind("<Button-1>", self._on_tree_click)
        self._tree_hover_iid = None

        # 右栏: 选中票详情 (图表为主, 占满剩余空间)
        right = ttk.Frame(main, padding=(16, 0))
        right.grid(row=0, column=3, sticky=NSEW, padx=(16, 0))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)  # 图表区优先占垂直空间
        right.rowconfigure(2, weight=0)

        # 图表容器: 尺寸只由 grid 权重决定, 不随图片内容膨胀 (切断 resize 反馈回路)
        self.chart_frame = ttk.Frame(right)
        self.chart_frame.grid(row=0, column=0, sticky=NSEW, pady=(0, 14))
        self.chart_frame.columnconfigure(0, weight=1)
        self.chart_frame.rowconfigure(0, weight=1)
        self.chart_frame.bind("<Configure>", self._on_chart_configure)
        self.chart_label = ttk.Label(self.chart_frame, text="选中左侧标的查看缩略图",
                                     foreground="#8e8e93", font=("Microsoft YaHei", 11))
        self.chart_label.grid(row=0, column=0, sticky=NSEW)

        self.lbl_strat = ttk.Label(right, text="", font=("Microsoft YaHei", 14, "bold"),
                                   foreground="#f5f5f7")
        self.lbl_strat.grid(row=1, column=0, sticky=W, pady=(0, 12))
        self.lbl_facts = ttk.Label(right, text="", justify=LEFT, font=("Consolas", 12),
                                   foreground="#d1d1d6")
        self.lbl_facts.grid(row=2, column=0, sticky=W, pady=(0, 12))

        self.btn_tv = ttk.Button(right, text="在 TradingView 打开", bootstyle="primary",
                                 command=self._open_tv, state=DISABLED)
        self.btn_tv.grid(row=3, column=0, sticky=NSEW, pady=(8, 0), ipady=6)

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
    def _signal_dates(self):
        """返回所有真实信号日, 降序(最新在前), 过滤脏日期与无效策略。"""
        try:
            with _db() as conn:
                rows = conn.execute(
                    f"SELECT DISTINCT signal_date FROM signal_archive "
                    f"WHERE signal_date LIKE ? AND strategy IN ({_KEY_PH}) "
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
            try:
                with _db() as conn:
                    for strat, cnt in conn.execute(
                        f"SELECT strategy, COUNT(*) FROM signal_archive "
                        f"WHERE signal_date=? AND strategy IN ({_KEY_PH}) GROUP BY strategy",
                        (self.selected_date,) + _VALID_KEYS,
                    ):
                        counts[strat] = cnt
            except Exception:  # noqa: BLE001
                pass

        self._build_sidebar(counts)

        keys = list(STRATEGY_ORDER)
        for k in counts:
            if k not in keys:
                keys.append(k)

        first = next((k for k in keys if counts.get(k, 0) > 0), keys[0] if keys else None)
        if first:
            self._on_strategy(first)
        else:
            date_label = self.selected_date or "—"
            self.list_title.configure(text=f"{date_label} 信号 (无命中)")
            self._clear_detail()
        self._update_status(self.selected_date, counts)
        self._refresh_watchlist()

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

    def _build_sidebar(self, counts):
        self._clear_sidebar()
        keys = list(STRATEGY_ORDER)
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

        rows = []
        if self.selected_date:
            try:
                with _db() as conn:
                    col_names = [d[0] for d in conn.execute("SELECT * FROM signal_archive LIMIT 0").description]
                    # 按代码稳定排序 (库内无可靠的信号质量分, 不做假排名)
                    sql = ("SELECT * FROM signal_archive WHERE signal_date=? AND strategy=? "
                           "ORDER BY code ASC LIMIT 100")
                    for r in conn.execute(sql, (self.selected_date, key)):
                        rows.append(dict(zip(col_names, r)))
            except Exception:  # noqa: BLE001
                pass

        self.current_rows = rows
        self._populate_list(rows)
        self.list_title.configure(text=f"{strategy_display(key)} · 共 {len(rows)} 只")
        if rows:
            self.tree.selection_set(self.tree.get_children()[0])
            self._on_select(None)
        else:
            self._clear_detail()

    def _populate_list(self, rows):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for i, row in enumerate(rows):
            code = row.get("code", "")
            marked = code in self.favorites
            values = (
                "●" if marked else "",
                i + 1,
                code,
                row.get("name") or "—",
            )
            self.tree.insert("", END, iid=str(i), values=values,
                             tags=("favorite",) if marked else ())

    def _favorites_path(self):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "gui_favorites.json")

    def _load_favorites(self):
        """加载用户手动"关注"的股票代码集合。文件不存在或损坏则返回空集合。"""
        path = self._favorites_path()
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return set(data)
        except Exception as e:  # noqa: BLE001
            logging.warning(f"加载关注列表失败: {e}")
        return set()

    def _save_favorites(self):
        """持久化关注集合到 JSON, 下次打开仍在。"""
        path = self._favorites_path()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(sorted(self.favorites), f, ensure_ascii=False, indent=2)
        except Exception as e:  # noqa: BLE001
            logging.warning(f"保存关注列表失败: {e}")

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
        code = values[2]
        if code in self.favorites:
            self.favorites.discard(code)
        else:
            self.favorites.add(code)
        marked = code in self.favorites
        values[0] = "●" if marked else ""
        self.tree.item(iid, values=tuple(values),
                       tags=("favorite",) if marked else ())
        self._save_favorites()
        self._refresh_watchlist()

    def _on_tree_motion(self, event):
        """TV 式: 鼠标悬停在标记列时, 整行背景变灰。"""
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            self._clear_tree_hover()
            return
        iid = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if not iid or col != "#1":
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
        if region != "cell":
            return
        iid = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if not iid or col != "#1":
            return
        self._toggle_favorite(iid)
        return "break"

    def _refresh_watchlist(self):
        """把当前 favorites 同步到左侧 Watchlist, 按加入时间倒序。"""
        for item in self.watch_tree.get_children():
            self.watch_tree.delete(item)
        # 倒序: 最新关注的在上面
        for code in reversed(sorted(self.favorites)):
            name = self._watchlist_name_for_code(code)
            self.watch_tree.insert("", END, iid=code, values=(f"{code} {name}",),
                                   tags=("favorite",))

    def _watchlist_name_for_code(self, code):
        """根据代码查名称; 优先当前列表, 否则去 signal_archive 找最新一条。"""
        for row in self.current_rows:
            if row.get("code") == code:
                return row.get("name") or "—"
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
        return "—"

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
                        f"SELECT * FROM signal_archive WHERE code=? AND strategy IN ({_KEY_PH}) "
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
                buf = generate_chart_bytes(
                    row.get("code", ""), row.get("name") or row.get("code", ""),
                    row.get("strategy", ""), float(row.get("sl_price") or 0),
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
        if not self._sync_ok:
            return
        cb = self._make_progress_cb("下载行情")
        self._set_busy(True)
        self._show_progress()
        self.status_var.set("行情下载中…")
        def _task():
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

    def _on_scan_done(self, ret):
        """策略扫描收尾文案: 区分 用户终止 / 无本地数据 / 正常完成。"""
        if self._stop_event.is_set():
            self.status_var.set("已终止 · 已扫描部分已保留, 未推送")
        elif ret == "NO_CODES":
            self.status_var.set("本地数据库为空, 请先点击「下载行情」")
        else:
            self.status_var.set("策略扫描完成 · 清单已刷新")

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
                extra = f" · 命中 {info}" if label == "策略扫描" else ""
                text = f"{label} {done}/{total} · {pct}%{extra}"
            self.root.after(0, self._apply_progress, pct, text)

        return cb

    def _apply_progress(self, pct, text):
        self.progress_bar.grid()
        self.progress_var.set(pct)
        self.status_var.set(text)

    def _update_status(self, signal_date, counts=None):
        try:
            with _db() as conn:
                d = conn.execute("SELECT MAX(trade_date) FROM daily_bars").fetchone()[0]
                n = conn.execute(
                    f"SELECT COUNT(*) FROM signal_archive WHERE signal_date=? AND strategy IN ({_KEY_PH})",
                    (signal_date,) + _VALID_KEYS,
                ).fetchone()[0] if signal_date else 0
            # scan_date 列含历史脏数据(99/97...), 不可信, 不显示; 仅展示真实数据日期与信号日
            self.status_var.set(f"就绪 · 数据 {d or '—'} · 信号日 {signal_date or '—'} · 共 {n} 标的")
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

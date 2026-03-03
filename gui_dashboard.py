# -*- coding: utf-8 -*-
#!/usr/bin/env python3
"""
量化交易系统图形化Dashboard (v8.0 Benchmarking Upgrade)
集成 ttkbootstrap 深色主题、系统监控与多线程任务管理
"""

import sys
import os
import threading
import queue
import logging
import time
import re
from datetime import datetime
import pandas as pd

# 3rd Party
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from ttkbootstrap.dialogs import Messagebox
from ttkbootstrap.widgets.scrolled import ScrolledText

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.pyplot as plt
import mplfinance as mpf

# Local Modules
# 确保控制台支持UTF-8
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# Import business logic modules
main_script = None
for_hold = None
data_manager = None
monitor = None  # Fix: Initialize monitor to avoid NameError
import_error_msg = None

try:
    from tools import data_manager
    import hunter as main_script
    from tools import for_hold
    from core.monitor import monitor
    from core.backtest import VectorBacktester
except ImportError as e:
    import_error_msg = str(e)
    logging.error(f"Import Error: {e}")
except Exception as e:
    import_error_msg = str(e)
    logging.error(f"Import Error: {e}")

class QueueHandler(logging.Handler):
    """Log handler that sends records to a queue."""
    def __init__(self, log_queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record):
        self.log_queue.put(record) # Put the record object, format later

class TradingSystemDashboard:
    def __init__(self, root):
        self.root = root
        self.root.title("QuanTrader Pro V8.8 - 极简操盘台")
        self.root.geometry("1280x800") 
        
        # Data & State
        self.log_queue = queue.Queue()
        self.status_queue = queue.Queue()
        self.is_running = False
        self.current_thread = None
        self.selected_symbol = None
        self.backtester = VectorBacktester(data_manager) if data_manager else None
        
        # Setup Logger
        self.setup_logging()
        
        # Start Monitor
        self.monitor = monitor
        if self.monitor:
            self.monitor.register_callback(self.status_queue.put) 
            self.monitor.start()

        # Build UI
        self._build_ui()
        
        # Start Loops
        self.root.after(100, self.process_log_queue)
        self.root.after(500, self.process_status_queue)
        self.update_time()

    def setup_logging(self):
        queue_handler = QueueHandler(self.log_queue)
        root_logger = logging.getLogger()
        root_logger.addHandler(queue_handler)
        root_logger.setLevel(logging.INFO)

    def _build_ui(self):
        """V8.8 Command Center Layout"""
        # Global Grid
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1) # Main content expands
        
        # 1. Top Command Bar (The "Command Center")
        cmd_frame = ttk.Frame(self.root, padding=10, bootstyle="secondary")
        cmd_frame.grid(row=0, column=0, sticky=EW)
        
        # Logo/Title
        ttk.Label(cmd_frame, text="⚡ QUANT", font=('Impact', 20), bootstyle="inverse-secondary").pack(side=LEFT, padx=10)
        
        # Search Box (Prominent)
        search_frame = ttk.Frame(cmd_frame)
        search_frame.pack(side=LEFT, padx=20, fill=Y)
        
        ttk.Label(search_frame, text="🔍 代码搜索:", bootstyle="inverse-secondary").pack(side=LEFT)
        self.ent_code = ttk.Entry(search_frame, font=('Consolas', 14, 'bold'), width=15, bootstyle="primary")
        self.ent_code.pack(side=LEFT, padx=5)
        self.ent_code.bind("<Return>", self.on_search_enter) # Bind Enter Key
        self.ent_code.focus_set()
        
        # Quick Actions (Specific)
        self.btn_analyze = ttk.Button(cmd_frame, text="🧠 深度诊断", command=self.start_ai_analysis, bootstyle="warning", state=DISABLED)
        self.btn_analyze.pack(side=LEFT, padx=5)
        
        # Separator
        ttk.Separator(cmd_frame, orient=VERTICAL).pack(side=LEFT, padx=15, fill=Y, pady=5)
        
        # System Ops (Daily Routine) - SOLID COLORS (High Visibility)
        # 1. Update
        self.btn_sync = ttk.Button(cmd_frame, text="🔄 更新数据", command=self.start_sync, bootstyle="info")
        self.btn_sync.pack(side=LEFT, padx=5)
        
        # 2. Hunter
        self.btn_hunter = ttk.Button(cmd_frame, text="🔭 猎手扫描", command=self.start_hunter, bootstyle="primary")
        self.btn_hunter.pack(side=LEFT, padx=5)
        
        # 3. Positions
        self.btn_hold = ttk.Button(cmd_frame, text="🛡️ 持仓管家", command=self.start_positions, bootstyle="success")
        self.btn_hold.pack(side=LEFT, padx=5)
        
        # Time & Status
        self.time_label = ttk.Label(cmd_frame, text="00:00:00", font=('Consolas', 12, 'bold'), bootstyle="inverse-secondary")
        self.time_label.pack(side=RIGHT, padx=10)
        
        # 2. Main Content (New Layout: Left Chart + Right Info)
        main_h_pane = ttk.Panedwindow(self.root, orient=HORIZONTAL)
        main_h_pane.grid(row=1, column=0, sticky=NSEW)
        self.root.rowconfigure(1, weight=1)
        
        # --- Left: Chart Area ---
        left_v_pane = ttk.Panedwindow(main_h_pane, orient=VERTICAL)
        main_h_pane.add(left_v_pane, weight=4)
        
        # Chart Frame
        self.chart_frame = ttk.Frame(left_v_pane, padding=2)
        left_v_pane.add(self.chart_frame, weight=5)
        
        self.fig = plt.Figure(figsize=(10, 6), dpi=100, facecolor='#1e1e1e')
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.chart_frame)
        self.canvas.get_tk_widget().pack(fill=BOTH, expand=True)
        
        # Log Frame
        log_frame = ttk.Labelframe(left_v_pane, text="📊 运行日志", padding=2)
        left_v_pane.add(log_frame, weight=1)
        
        self.log_text = ScrolledText(log_frame, state=NORMAL, font=('Consolas', 9), height=8, bootstyle="secondary-round")
        self.log_text.pack(fill=BOTH, expand=True)
        self.log_text.tag_config('INFO', foreground='#bdc3c7')
        self.log_text.tag_config('WARNING', foreground='#f39c12')
        self.log_text.tag_config('ERROR', foreground='#e74c3c')
        
        # --- Right: Info Panel ---
        right_frame = ttk.Frame(main_h_pane, padding=5, width=300)
        main_h_pane.add(right_frame, weight=1)
        
        ttk.Label(right_frame, text="🧠 AI 诊断细节", font=('Microsoft YaHei', 12, 'bold'), bootstyle="warning").pack(fill=X, pady=5)
        self.ai_text = ScrolledText(right_frame, state=DISABLED, font=('Microsoft YaHei', 10), height=30, bootstyle="secondary")
        self.ai_text.pack(fill=BOTH, expand=True)
        
        # Bottom Buttons in Right Panel
        btn_box = ttk.Frame(right_frame, padding=5)
        btn_box.pack(fill=X, side=BOTTOM)
        
        ttk.Button(btn_box, text="🧪 实验室", command=self.open_lab, bootstyle="outline-info").pack(side=LEFT, padx=2, fill=X, expand=True)
        ttk.Button(btn_box, text="📜 日志库", command=self.open_journal, bootstyle="outline-secondary").pack(side=LEFT, padx=2, fill=X, expand=True)

        # 3. Status Bar
        status_bar = ttk.Frame(self.root, padding=2)
        status_bar.grid(row=2, column=0, sticky=EW)
        
        self.lbl_net = ttk.Label(status_bar, text="● NET", bootstyle="danger", font=('Arial', 9))
        self.lbl_net.pack(side=RIGHT, padx=5)
        
        self.progress = ttk.Progressbar(status_bar, mode='indeterminate', bootstyle="success-striped")
        self.progress.pack(side=LEFT, fill=X, expand=True)

    # ================= Logic Interop =================
    
    def open_journal(self):
        """打开 AI 决策日志窗口"""
        journal_win = ttk.Toplevel(title="Brooks-AI 决策日志库", size=(1000, 600))
        
        # Table
        cols = ("日期", "代码", "策略", "AI观点", "止损")
        tree = ttk.Treeview(journal_win, columns=cols, show="headings", bootstyle="secondary")
        for col in cols: tree.heading(col, text=col)
        tree.column("日期", width=100)
        tree.column("代码", width=80)
        tree.column("策略", width=80)
        tree.column("AI观点", width=600)
        tree.column("止损", width=80)
        tree.pack(fill=BOTH, expand=True, padx=10, pady=10)
        
        # Load Data
        import sqlite3
        from tools.journal import DB_PATH
        if os.path.exists(DB_PATH):
            try:
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                cursor.execute("SELECT trade_date, symbol, strategy_type, daily_reason, sl_price FROM hunter_journal ORDER BY created_at DESC LIMIT 100")
                rows = cursor.fetchall()
                for row in rows:
                    tree.insert("", END, values=row)
                conn.close()
            except Exception as e:
                logging.error(f"Failed to load journal: {e}")

    def open_lab(self):
        """打开策略实验室 (回测指标展示)"""
        lab_win = ttk.Toplevel(title="Strategy Lab - MTR V29.5", size=(600, 400))
        
        frame = ttk.Frame(lab_win, padding=20)
        frame.pack(fill=BOTH, expand=True)
        
        ttk.Label(frame, text="📊 MTR V29.5 Master 核心指标", font=('Arial', 14, 'bold')).pack(pady=10)
        
        stats = [
            ("数学期望 (Avg R)", "+0.254 R", "success"),
            ("1R 触达率 (心理胜率)", "50.6%", "info"),
            ("2R 触达率 (核心胜率)", "29.4%", "primary"),
            ("入场精度 (1R内未损率)", "66.2%", "warning")
        ]
        
        for label, val, color in stats:
            f = ttk.Frame(frame)
            f.pack(fill=X, pady=5)
            ttk.Label(f, text=label, font=('Arial', 11)).pack(side=LEFT)
            ttk.Label(f, text=val, font=('Consolas', 12, 'bold'), bootstyle=color).pack(side=RIGHT)
            
        ttk.Separator(frame, orient=HORIZONTAL).pack(fill=X, pady=15)
        
        def run_full():
            logging.info("🚀 正在启动全市场回测 (Background)...")
            os.system("start python tools/backtest_mtr_v29_full.py")
            
        ttk.Button(frame, text="🔥 运行全量市场审计 (1-2小时)", command=run_full, bootstyle="danger").pack(fill=X, pady=10)

    def on_search_enter(self, event):
        """User hits Enter in search bar -> Draw Chart immediately"""
        code = self.ent_code.get().strip()
        if not code: return
        
        # Normalize code
        if len(code) == 6 and code.isdigit():
            code = f"sh.{code}" if code.startswith('6') else f"sz.{code}"
            self.ent_code.delete(0, END)
            self.ent_code.insert(0, code)
            
        self.selected_symbol = code
        logging.info(f"🔎 搜索股票: {code}")
        
        # Load Latest AI Verdict from DB
        self._load_ai_detail(code)
        
        # Enable AI Button
        self.btn_analyze.configure(state=NORMAL)
        
        # Draw Chart
        self.draw_chart_pingan_style(code)

    def _load_ai_detail(self, code):
        """从数据库读取最近的 AI 分析详情"""
        import sqlite3
        from tools.journal import DB_PATH
        content = "⚠️ 尚无本轮 AI 诊断记录"
        if os.path.exists(DB_PATH):
            try:
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                cursor.execute("SELECT daily_reason FROM hunter_journal WHERE symbol=? ORDER BY created_at DESC LIMIT 1", (code,))
                row = cursor.fetchone()
                if row:
                    content = row[0]
                conn.close()
            except: pass
            
        self.ai_text.configure(state=NORMAL)
        self.ai_text.delete(1.0, END)
        self.ai_text.insert(END, content)
        self.ai_text.configure(state=DISABLED)

    def draw_chart_pingan_style(self, code):
        """Ping An Style Chart: MA5/10/20/60 + Volume + Red/Green"""
        if not data_manager: return

        def _fetch_and_draw():
            try:
                df = data_manager.get_stock_data(code, limit=200)
                if df is None or df.empty:
                    logging.warning(f"无法获取数据: {code}")
                    return

                df = df.rename(columns={'date': 'Date', 'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume'})
                df['Date'] = pd.to_datetime(df['Date'])
                df.set_index('Date', inplace=True)
                
                self.root.after(0, lambda: self._render_mpl(df, code))
            except Exception as e:
                logging.error(f"绘图失败: {e}")

        threading.Thread(target=_fetch_and_draw, daemon=True).start()

    def _render_mpl(self, df, code):
        """Actual Matplotlib Rendering"""
        self.fig.clear()
        ax1 = self.fig.add_subplot(2, 1, 1)
        ax2 = self.fig.add_subplot(2, 1, 2, sharex=ax1)
        
        gs = self.fig.add_gridspec(2, 1, height_ratios=[3, 1])
        ax1.set_position(gs[0].get_position(self.fig))
        ax2.set_position(gs[1].get_position(self.fig))
        
        mc = mpf.make_marketcolors(up='#e74c3c', down='#2ecc71', edge='inherit', wick='inherit', volume='in', ohlc='inherit')
        s = mpf.make_mpf_style(base_mpf_style='nightclouds', marketcolors=mc, gridstyle=':', 
                               rc={'font.family': 'SimHei', 'axes.grid': True, 'axes.labelcolor': 'white', 
                                   'xtick.color': 'white', 'ytick.color': 'white', 'text.color': 'white'})
        
        mpf.plot(df.tail(100), type='candle', mav=(5, 10, 20, 60), volume=ax2, ax=ax1, style=s, 
                 axtitle=f"{code} Daily Chart", ylabel="Price", ylabel_lower="Volume", xrotation=0, datetime_format='%Y-%m-%d')
        
        self.canvas.draw()

    def start_ai_analysis(self):
        """Trigger AI Analysis for selected stock"""
        if not self.selected_symbol: return
        
        def run_task():
            if main_script:
                main_script.run_ask_stock(self.selected_symbol, mode="ENTRY")
                # Reload UI detail
                self.root.after(1000, lambda: self._load_ai_detail(self.selected_symbol))
        
        self.start_thread(run_task, f"AI分析: {self.selected_symbol}")

    def start_sync(self):
        if data_manager:
            self.start_thread(data_manager.update_daily_data_batch, "数据同步 (Data Sync)", is_long_running=True)

    def start_hunter(self):
        def run_task():
            if not main_script or not data_manager:
                logging.error("核心模块未加载")
                return
            codes = data_manager.get_stock_list()
            if not codes:
                logging.warning("本地数据库为空，请先运行数据更新")
                return
            logging.info(f"🔭 启动猎手模式，即将扫描 {len(codes)} 只股票...")
            main_script.run_pipeline_once(codes)
        self.start_thread(run_task, "猎手扫描 (Hunter)", is_long_running=True)

    def start_positions(self):
        def run_task():
            if not for_hold: 
                logging.error("持仓模块未加载")
                return
            logging.info("🛡️ 启动持仓管家...")
            try:
                holdings = for_hold.load_holdings_with_cost()
                if not holdings:
                    logging.warning("⚠️ 持仓列表为空 (请检查 hold_list.txt)")
                    return
                for item in holdings:
                    for_hold.analyze_single_stock_micro(item)
                logging.info(f"✅ 持仓巡检完成")
            except Exception as e:
                logging.error(f"Guardian 运行失败: {e}")
        self.start_thread(run_task, "持仓管家 (Guardian)", is_long_running=True)

    def start_thread(self, func, name, is_long_running=False):
        if not is_long_running: self.progress.start(10)
        def wrapper():
            logging.info(f"🚀 {name} 启动")
            try:
                func()
                logging.info(f"✅ {name} 完成")
            except Exception as e:
                logging.error(f"❌ {name} 异常: {e}")
            finally:
                if not is_long_running: self.root.after(0, self.progress.stop)
        threading.Thread(target=wrapper, daemon=True).start()

    # ... (Keep existing helper methods like process_log_queue, setup_logging, etc.)
    # I will inline simplified versions for brevity if needed, but better to keep full class structure.
    
    # Re-implementing helpers to ensure class validity
    def process_status_queue(self):
        try:
            while not self.status_queue.empty():
                status = self.status_queue.get_nowait()
                if status['internet']:
                    self.lbl_net.configure(bootstyle="success", text=f"NET: {status['latency']}ms")
                else:
                    self.lbl_net.configure(bootstyle="danger", text="NET: Err")
        except: pass
        self.root.after(1000, self.process_status_queue)

    def process_log_queue(self):
        while not self.log_queue.empty():
            try:
                record = self.log_queue.get_nowait()
                msg = f"[{datetime.fromtimestamp(record.created).strftime('%H:%M:%S')}] {record.getMessage()}"
                self.log_text.insert(END, msg + "\n", record.levelname)
                self.log_text.see(END)
            except: break
        self.root.after(100, self.process_log_queue)

    def update_time(self):
        self.time_label.configure(text=datetime.now().strftime("%H:%M:%S"))
        self.root.after(1000, self.update_time)

    def on_closing(self):
        self.root.destroy()
        sys.exit(0)


def main():
    # Theme: heavily inspired by professional trading terminals
    # Options: 'solar', 'superhero', 'darkly', 'cyborg'
    app = ttk.Window(themename="darkly") 
    dashboard = TradingSystemDashboard(app)
    app.protocol("WM_DELETE_WINDOW", dashboard.on_closing)
    
    # 🟢 Signal Handling: Allow Ctrl+C to kill the app
    def check_signal():
        app.after(500, check_signal)
    app.after(500, check_signal)
    
    try:
        app.mainloop()
    except KeyboardInterrupt:
        logging.info("🛑 KeyboardInterrupt received (Ctrl+C). Exiting...")
        dashboard.on_closing()

if __name__ == "__main__":
    main()
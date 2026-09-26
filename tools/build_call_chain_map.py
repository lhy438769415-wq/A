# -*- coding: utf-8 -*-
"""画一张单屏架构全景图（横版 PNG），风格对齐 1 月 architecture_v7.1.png。"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Rectangle
from matplotlib import font_manager as fm

FONT = "C:/Windows/Fonts/simhei.ttf"
fm.fontManager.addfont(FONT)
PROP = fm.FontProperties(fname=FONT)

W, H = 1880, 1050
fig = plt.figure(figsize=(W / 100, H / 100), dpi=130)
ax = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(0, W); ax.set_ylim(0, H); ax.axis("off")

def box(x, y, w, h, lines, fill):
    p = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.6,rounding_size=10",
                       linewidth=1.5, edgecolor="#333333", facecolor=fill, zorder=3)
    ax.add_patch(p)
    cx = x + w / 2
    if len(lines) == 1:
        ax.text(cx, y + h / 2, lines[0], ha="center", va="center",
                fontsize=20, color="white", fontproperties=PROP, weight="bold", zorder=4)
    else:
        ax.text(cx, y + h * 0.68, lines[0], ha="center", va="center",
                fontsize=18, color="white", fontproperties=PROP, weight="bold", zorder=4)
        for i, ln in enumerate(lines[1:]):
            ax.text(cx, y + h * (0.68 - 0.36 * (i + 1)), ln, ha="center", va="center",
                    fontsize=14, color="white", fontproperties=PROP, zorder=4)

def arrow(x1, y1, x2, y2, color="#333333", style="-|>", lw=2.2, ls="-"):
    a = FancyArrowPatch((x1, y1), (x2, y2), arrowstyle=style, mutation_scale=18,
                        linewidth=lw, color=color, linestyle=ls, zorder=2)
    ax.add_patch(a)

def lane_header(x, y, w, label, color):
    r = Rectangle((x, y), w, 32, facecolor=color, edgecolor="none", alpha=0.95, zorder=1)
    ax.add_patch(r)
    ax.text(x + 14, y + 16, label, ha="left", va="center", fontsize=17,
            color="white", fontproperties=PROP, weight="bold", zorder=2)

C = {"sync": "#2E75B6", "scan": "#27AE60", "ai": "#8E44AD", "store": "#E67E22",
     "push": "#16A085", "guard": "#C0392B", "base": "#7F8C8D"}

# ---------- 标题 ----------
ax.text(W/2, 1015, "Brooks-AI 量化系统 · 调用关系全景图", ha="center", va="center",
        fontsize=29, color="#1a1a1a", fontproperties=PROP, weight="bold")
ax.text(W/2, 982, "交易员也能一眼看懂：系统怎么干活、哪里容易出岔子  ·  2026-09-26",
        ha="center", va="center", fontsize=14, color="#666666", fontproperties=PROP)

# ---------- 入口条 ----------
ax.text(60, 968, "你从哪进（决定状态跟不跟）", ha="left", va="center",
        fontsize=15, color="#333333", fontproperties=PROP, weight="bold")
chips = [
    ("桌面操作台 GUI", "周线：自动跟    日线：不跟", "#2E75B6"),
    ("命令行 hunter", "周线：不跟    日线：不跟", "#C0392B"),
    ("交互菜单", "选「信号追踪」跟   选「扫描」不跟", "#2E8B57"),
]
cw, cgap, ey, eh = 470, 26, 890, 62
for i, (t, s, col) in enumerate(chips):
    box(60 + i*(cw+cgap), ey, cw, eh, [t, s], col)

# ---------- ① 主流程 猎人扫描循环 ----------
lane_header(60, 846, 1760, "① 猎人扫描循环（主流程：找新机会）", C["scan"])
mb_w, mb_h, mb_y = 320, 150, 690
gap = (1760 - 5*mb_w) / 4
main_boxes = [
    ("① 数据同步", "拉最新行情进本机库\n（日线/周线各一招）", C["sync"]),
    ("② 策略扫描", "全市场翻 K 线\n按形态初筛候选", C["scan"]),
    ("③ AI 二次审计", "（可选）DeepSeek 把关\n过滤假信号", C["ai"]),
    ("④ 信号归档", "命中写 signal_archive\n先标「待命」", C["store"]),
    ("⑤ 推送 Discord", "发文字指令 + K 线图\n提醒你去看", C["push"]),
]
xs = []
for i, (t, s, col) in enumerate(main_boxes):
    x = 60 + i*(mb_w+gap)
    xs.append((x, x+mb_w))
    box(x, mb_y, mb_w, mb_h, [t, s], col)
for i in range(4):
    arrow(xs[i][1], mb_y+mb_h/2, xs[i+1][0], mb_y+mb_h/2)

# ---------- ② 守护 / 状态循环（旁路） ----------
gbox_w, gbox_h, gy = 520, 150, 390
gx0 = (W - (3*gbox_w + 2*40)) / 2
lane_header(60, 560, 1760, "② 守护 / 状态循环（旁路：管老信号死活）", C["guard"])
guard_boxes = [
    ("信号状态机 track_signals", "翻所有「待命/持仓」信号\n对照最新价推进状态", C["guard"]),
    ("持仓管家 for_hold", "读你手填持仓清单\n带成本线 AI 判断", C["guard"]),
    ("守护报告", "更新后状态 / 持仓结论\n发 Discord 或写报告", C["push"]),
]
gxs = []
for i, (t, s, col) in enumerate(guard_boxes):
    x = gx0 + i*(gbox_w+40)
    gxs.append((x, x+gbox_w))
    box(x, gy, gbox_w, gbox_h, [t, s], col)
for i in range(2):
    arrow(gxs[i][1], gy+gbox_h/2, gxs[i+1][0], gy+gbox_h/2)

ax.text(W/2, 358, "注意：状态机只接 GUI 周线按钮 / --track 命令 / 菜单「信号追踪」；命令行·日线均断开",
        ha="center", va="center", fontsize=14, color="#C0392B", fontproperties=PROP, weight="bold")

# ---------- 断线（核心缺陷） ----------
ax.annotate("", xy=(gxs[0][0]+gbox_w/2, gy+gbox_h+8), xytext=(xs[3][0]+mb_w/2, mb_y),
            arrowprops=dict(arrowstyle="-|>", color="#C0392B", lw=3, linestyle=(0,(6,4)),
                            mutation_scale=20), zorder=2)
ax.text(xs[3][0]+mb_w/2+240, 648,
        "断线！扫完未自动跟进\n命令行 / 日线均断开\n→ 老信号状态冻住 → 死票显活",
        ha="center", va="center", fontsize=14, color="#C0392B",
        fontproperties=PROP, weight="bold",
        bbox=dict(boxstyle="round,pad=0.45", fc="#fdecea", ec="#C0392B", lw=1.5))

# ---------- ③ 基础设施地基 ----------
lane_header(60, 250, 1760, "③ 基础设施（地基：数据从哪来）", C["base"])
base_boxes = [
    ("SQLite 行情 + 信号库", "data/*.db（离线 T+1）\n扫描 / 状态机都读写它", C["base"]),
    ("数据层 data_provider", "拉行情、本地聚合周线\n所有扫描吃它的数据", C["base"]),
    ("数据库层 database", "signal_archive 表\n唯一 schema 主人", C["base"]),
]
bw, bgap, by, bh = 560, 40, 110, 110
bx0 = (W - (3*bw+2*bgap)) / 2
for i, (t, s, col) in enumerate(base_boxes):
    box(bx0 + i*(bw+bgap), by, bw, bh, [t, s], col)

plt.savefig("D:/life/Trading view/_Project_A/Data_from_Akshare/debugV7.1_for_workbuddy/docs/call_chain_map.png",
            dpi=130, bbox_inches="tight", facecolor="white")
print("PNG_SAVED")

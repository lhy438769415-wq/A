# P2-heavy：日线/周线双引擎收敛方案（人工评审稿 v2）

> 状态：v2 草案（据独立 subagent 架构评审修订）。待用户拍板边界后再动手。
> 关联：P2 在 codex.md 标为「最复杂，排最后」。本方案只收敛**编排层 + 周线特有扫描语义**，不动策略核心计算。

---

## 1. 现状诊断（双引擎到底差在哪）

| 层 | 日线（hunter.py） | 周线（scanner_weekly_gap.py + scanner_weekly_3k.py） |
|---|---|---|
| 策略逻辑 | 6 策略 `calculate_signals` / `get_signal_info` / `compute_rating` | gap 周线脚本已调 `compute_rating`（scanner_weekly_gap.py:234）；**3K 周线脚本未调**——直读原始列 `signal_3k`/`signal_3k_gap_test`/`entry_3k_gap_test` 手算 RR（scanner_weekly_3k.py:67-101），完全没沾 `compute_rating`/`get_signal_info`。⚠️ 见 §8 评审修订 R-A |
| 取数 | `data_provider` 日线 | 各自内联 `fetch_weekly_data` → `dp.get_stock_data_weekly` |
| 扫描+建信号 | `hunter._scan_market`（273） | 两脚本各自重写 `_scan_single_code` / `scan_weekly_3k` |
| 评级/AI审计 | `_classify_signals`（334，元数据 `ai_audit` 跳过 + 评级 + Discord） | gap 已调 `compute_rating`（一致）；**无 AI 审计步骤**（周线策略 `ai_audit` 全为 False，跳过亦无影响）；3K 无评级 |
| 图表/Discord | `_dispatch_charts`（630）统一 | 两脚本各自调 `notifier.generate_chart_bytes` + `send_discord_*`（**已复用 notifier，非各自重写**） |
| 报告/监控产物 | `_compose_report`（502） | gap→`weekly_struct_gap_plan.md`+`weekly_gap_watchlist.json`；3K→`weekly_ambush_plan.md`+`weekly_watchlist.json`（各自独有） |

**裂缝分布（修正 v1 误判）**：
- **裂缝 1（编排层）**：日线走 hunter 四段流水线，周线散在两个独立脚本重写取数/扫描/分发。
- **裂缝 2（周线特有扫描语义，v1 漏判）**：以下逻辑写在 scanner 而非 strategy 里，Phase 1 引擎必须纳入，否则会丢信号：
  - 多信号遍历：扫 `df.tail(60)` 内**所有仍存活信号**(scanner_weekly_gap.py:128-129)，非末行单信号。
  - 生命周期过滤：①缺口击穿 SL 剔除 ②达 TP 剔除(scanner_weekly_gap.py:180-192)。
  - pending 派生：仅 `STRATEGY_STRUCTURAL_GAP` 的 breakout 未翻转情形构造 pending 信号(`:132-169`)。
- **裂缝 3（3K 评级缺失）**：3K 周线扫描器绕过了策略层评级，统一前必须先给它接入 `compute_rating`(R-A)。

**另两处裂缝（收敛时要决策）**：
- 3K 策略 `get_metadata` 声明 `supported_timeframes=['daily']`（three_k_strategy.py:61），但 `scanner_weekly_3k.py` 偷偷拿它扫周线——元数据与现实不符。
- 周线脚本的 `_get_strategy_cols` 后缀 hack 已在 P2-light 消除主路径（scanner_weekly_gap.py:71-75 改读 metadata），后缀推导仅剩**兜底**(`:76-86`)，三个周线策略已声明字段、主路径永不走兜底；兜底可标注为防御性、后续逐步删除（非阻塞）。

---

## 2. 收敛目标（Done 定义）

1. 周线扫描**不再各自重写**取数/扫描/建信号/评级/图表逻辑——统一调用一个共享编排核心。
2. 日线 hunter 与周线扫描器**共用同一套**「取数→扫描→评级→图表/Discord」编排；周线保留其独有报告/监控产物 + 周线特有扫描语义（多信号/生命周期/pending）。
3. 所有现有产物（`.md` 报告、`.json` 监控、Discord 推送样式）**文件名与形状零变化**（web_viewer.py:71 / deploy_dashboard.py:15 强依赖 `weekly_gap_watchlist.json` 的 `{'signals_gap':[...]}`，见 D3）。

---

## 3. 方案设计（分阶段，低风险优先）

### Phase 1 — 提取共享编排核心 `core/scan_engine.py`（不动日线行为）
新增模块，把「取数 + 扫描 + 多信号/生命周期/pending 语义 + 评级」抽出来：

```python
@dataclass
class ScanHit:
    code: str; name: str; strategy_type: str; timeframe: str   # 'daily'|'weekly'
    entry: float; sl: float; tp: float; tp_multiplier: float
    rating: Optional[RatingResult]        # 来自 strategy.compute_rating (3K 须先接入, 见 R-A)
    quality: float; is_pending: bool
    extra: dict   # 周线特有: gap_stats / phase / bars_since_breakout / gap_top_exact / breakout 派生字段

def fetch_market_data(codes, timeframe, limit) -> dict[code, DataFrame]:
    # daily -> dp.get_stock_data_daily ; weekly -> dp.get_stock_data_weekly

def scan_strategies(codes, strategies, timeframe='daily', limit=300,
                    max_workers=4) -> list[ScanHit]:
    # ⚠️ 并发保留: ThreadPoolExecutor(max_workers) 因 SQLite 读取 (scanner_weekly_gap.py:288-297)
    # ⚠️ 多信号: 遍历 df.tail(N) 内所有存活信号 (非末行)
    # ⚠️ 生命周期过滤: 缺口击穿 SL 剔除 + 达 TP 剔除 (scanner_weekly_gap.py:180-192)
    # ⚠️ pending 派生: 仅 STRUCTURAL_GAP 的 breakout 未翻转 (scanner_weekly_gap.py:132-169)
    # ⚠️ 评级: strat.compute_rating(df) 集成 (3K 须先补)
    for strat_name in strategies:
        strat = StrategyRegistry.get_strategy(strat_name)
        cols  = _resolve_cols(StrategyRegistry.get_metadata(strat_name))  # 复用已元数据化逻辑
        for code, df in data.items():
            df = add_indicators(df); sig = strat.calculate_signals(df)
            for sig_date, row in _iter_alive_signals(sig, cols, N):   # 多信号 + 生命周期
                if pending_case(strat, df, ...):  # STRUCTURAL_GAP 专属
                    hits.append(_make_pending_hit(...))
                else:
                    hits.append(ScanHit(..., rating=strat.compute_rating(df)))
    return hits
```
- 纯提取，**日线 hunter 暂不改**（Phase 1 零行为变化，仅新增模块 + 单测）。
- 必须自带真单测：信号数 / 评级 / pending / 生命周期过滤（R4 已降级旧测试，见 §5）。

### Phase 2 — 周线两脚本改写为 scan_engine 的薄封装（去重）
- `scanner_weekly_gap.py`：删除内联 `fetch_weekly_data`/`add_indicators`/`calculate_signals`/`_get_strategy_cols`/信号字典构造/多信号循环/生命周期/pending 派生，**改为 `scan_strategies(codes, weekly_gap_strategies, 'weekly')`**；保留且仅保留：分组/简报（`format_push_brief`）/图表（`generate_chart_bytes` + `timeframe='周K'`）/Discord 发送/`weekly_struct_gap_plan.md`+`weekly_gap_watchlist.json` 写入。
- `scanner_weekly_3k.py`：**先解决 R-A**——把 `scanner_weekly_3k.py:67-101` 的原始列手算逻辑上移至 `ThreeKStrategy.calculate_signals`（产出标准 `entry`/`sl`/`tp` 列）+ `get_signal_info` 产出 `phase`（缺口确认/新雏形）进 `extra`；薄封装委托 `scan_strategies` 后只做格式化。
- **D2 决策**：3K 元数据 `supported_timeframes` 补 `'weekly'`，使 `StrategyRegistry.get_strategies_by_timeframe('weekly')` 能正确返回它（与现实一致；日线 hunter 用 `list_strategies()` 全量扫描，不受此影响 → 安全）。
- 风险：仅删重 + 委托；周线脚本的**独有产物代码原样保留**，格式不变。

### Phase 3（可选 / 高风险）— 周线并入 hunter 流水线，彻底单引擎
- ⚠️ 修正 v1：hunter.py:902-924 **已有 `--timeframe weekly` 委托**，但只接 gap、未接 3K。所以：
  - 对 gap：Phase 3 只是把委托体从 `scanner_weekly_gap` 换成 `scan_engine`，低风险。
  - 对 3K：是**新增接入 + 行为变更**——3K 原**不归档 signal_tracker**（gap 才在 scanner_weekly_gap.py:352 归档），并入 hunter 会改变 3K 是否入池/归档的行为，须单独评估。
- 触及日线热路径部分（daily 经 `_scan_market`）必须放到最后、且做字节级等价验证（见 §5）。
- **推荐：Phase 1+2 先行交付，Phase 3 单独评估（含 3K 行为变更影响分析）**。

---

## 4. 关键决策点（需你拍板）

- **D1 范围**：仅 Phase 1+2（去重，低风险，推荐）｜还是连 Phase 3 一起（彻底单引擎，高险——且 3K 涉及行为变更）。
- **D2 3K 元数据**：补 `'weekly'`（推荐，对齐现实，且是 R-A 前置）｜还是保持 `daily`-only + 周线脚本特殊硬编码。
- **D3 产物形状（v1 仅说文件名，已升级）**：严格保留 `weekly_gap_watchlist.json` 的 `{'signals_gap':[...]}` 形状（web_viewer.py:71 / deploy_dashboard.py:15 强依赖）；3K 的 `weekly_watchlist.json` 是 `{'signals_3k':..., 'signals_gap_test':...}` **不同形状，勿混**。薄封装须锁定这两套 JSON 契约，不得统一成单一形状。

---

## 5. 风险与缓解

| 风险 | 缓解 |
|---|---|
| R-A ⚠️ 3K 无评级（评审核心发现） | Phase 2 前先把 3K 扫描逻辑上移到 `ThreeKStrategy`（标准 entry/sl/tp + `compute_rating`），薄封装才能拿到 rating；否则统一后 3K 反而丢失手算 RR |
| R1 周线独有报告/监控格式被破坏 | 格式化代码留在薄封装；新增 golden-file 测试比对生成的 `.md`/`.json` 形状（尤其 `{'signals_gap':[...]}`） |
| R2 3K 元数据改动误伤日线 | 日线用 `list_strategies()` 不受 `supported_timeframes` 影响；加回归测试确认日线 3K 仍扫描 |
| R3 Phase 3 触碰日线热路径 + 3K 行为变更 | 延后；样本级字节等价（信号数 + Discord payload 前后 diff）；3K 归档行为变更单列评估 |
| R4 ⚠️ 旧测试守护名实不符（评审修正 v1 误判） | `test_weekly_and_noai_flow.py` 仅 `assert isinstance(list)`（冒烟级，mock 无真实信号/评级列）；`test_p1_p2_regression.py` 仅验列名且暴露「未知策略静默回退 MTR」隐患(`:514-519`)。**Phase 1 必须自带真单测**（信号/评级/pending/生命周期），不得依赖旧测试当安全网 |
| R5 串行化性能回退 | `scan_engine` 保留 `ThreadPoolExecutor(max_workers=4)` 并发（SQLite 读取瓶颈，scanner_weekly_gap.py:288-297） |
| R6 `ScanHit` 契约漏字段导致运行期炸 | 周线脚本现有 mock 测试扩展为新引擎单测；等价验证断言 `ScanHit` 逐字段相等 |

---

## 6. 测试与验收

- 新增 `tests/test_scan_engine.py`：覆盖 `ScanHit` 构造、`scan_strategies` 在 mock 数据下产出（**含多信号 + 生命周期过滤 + pending + compute_rating 集成**，弥补 R4）。
- 扩展 `test_weekly_and_noai_flow.py`：用 mock `fetch_market_data` 验证周线脚本委托 scan_engine 后产出一致（升级为信号级断言，非 `isinstance`）。
- **等价验证（防改坏）**：同一小样本代码集 + mock 数据，跑「旧周线脚本」vs「新 scan_engine」→ 断言 `ScanHit` 列表逐字段相等（同 P11 交叉评审手法）。
- golden-file：`weekly_struct_gap_plan.md` / `weekly_gap_watchlist.json`（`{'signals_gap':[...]}`）/ `weekly_ambush_plan.md` / `weekly_watchlist.json`（`{'signals_3k':...,'signals_gap_test':...}`）重渲染后与旧版内容 diff 为零。
- 质量门禁：无新增红线（sys.path.insert/basicConfig/裸except/import*）；测试数 ≥ 基线。

---

## 7. 推荐路径

**先交付 Phase 1+2**（提取共享核心 + 周线两脚本去重写薄封装），不动日线热路径；Phase 2 须先解决 R-A（3K 接入评级）才能薄封装。**Phase 3 单独评估**（含 3K 行为变更影响），不建议与 Phase 1+2 捆绑。

---

## 8. v2 独立评审修订（据 subagent 架构评审）

> 2026-07-25 聘请独立 subagent（general-purpose, reasoning）作为评审专家，只读代码 + 方案文档批判性评价。评审可信度=**需修改后动手**。以下为评审命中项 + 我亲自核实结果 + 文档修订。

### 评审命中（已核实属实，已修订方案）
- **R-A「3K 无评级」**：评审指出 scanner_weekly_3k.py:67-101 直读原始列手算 RR，未调 `compute_rating`/`get_signal_info` → 核实属实。v1「评级已统一」主张对 3K 不成立。已新增 R-A + Phase 2 前置步骤。
- **「Phase 1 会丢信号」**：评审指出周线扫 `df.tail(60)` 所有存活信号(`:128-129`)+ 生命周期(`:180-192`)+ pending 派生(`:132-169`)，v1「取最新信号行」模型会静默漏信号 → 核实属实。Phase 1 引擎已重写为多信号 + 生命周期 + pending + 并发。
- **「并发被忽略」**：评审指出 gap 用 `ThreadPoolExecutor(4)` 因 SQLite(`:288-297`)，v1 串行会性能回退 → 核实属实。scan_engine 已保留并发。
- **「Phase 3 边界不实」**：评审指出 hunter.py:902-924 已有 weekly 委托（只接 gap 未接 3K）→ 核实属实。Phase 3 描述已修正（gap 低风险替换 / 3K 行为变更）。
- **「外部消费方漏列」**：评审指出 web_viewer.py:71 / deploy_dashboard.py:15 强依赖 `{'signals_gap':[...]}` 形状，3K 的 json 形状不同 → 核实属实。D3 已升级为锁定 JSON 形状。
- **「测试守护名实不符」**：评审指出 test_weekly_and_noai_flow 仅 `isinstance(list)`、test_p1_p2_regression 仅验列名且暴露未知策略静默回退 MTR → 核实合理。R4 已降级，Phase 1 须自带真单测。

### 评审误判（已澄清，非漏洞）
- **「后缀 hack 仍在」**：评审指 scanner_weekly_gap.py:71-86 后缀 hack 未消除。核实：P2-light(3f074dd) 已将主路径改为元数据优先(`:71-75`)，后缀推导仅剩**兜底**(`:76-86`)，三个周线策略已声明字段、主路径永不走兜底。非阻塞，可标注逐步删除。

### 评审结论
方向对（策略层确已元数据化统一），但 v1 **低估「薄封装」真实工作量**（周线特有扫描语义须搬进引擎，3K 须先接入评级）；Phase 1 引擎草图会丢信号（已修）；D1 仅 Phase 1+2、D2 补 weekly、D3 锁 JSON 形状——与修订后方案一致。

---

## 9. Phase 3 执行记录（2026-07-25，入口/编排层单引擎；3K 例外刻意保留）

### 9.1 决策 (3a)
- **3K 保持不归档 signal_tracker**：保留与旧 `scanner_weekly_3k.py` 一致的行为差异（gap 家族才在 `format_push_weekly_gap` 内归档；3K 路径 `format_push_weekly_3k` 刻意不调 `archive_signal`）。
- **3K 产物保持独立形状**：`weekly_watchlist.json` = `{'signals_3k':..., 'signals_gap_test':...}`、`weekly_ambush_plan.md`、专属 Discord 分组（按 phase 缺口确认/新雏形）——不与 gap 的 `weekly_gap_watchlist.json`（`{'signals_gap':[...]}`）混用。
- **不补 3K weekly 元数据**：`ThreeKStrategy.supported_timeframes` 仍为 `['daily']`，故 `get_strategies_by_timeframe('weekly')[:1]` 默认仍是 STRUCTURAL_GAP，未被 3K 污染。3K 经 `run_weekly_scan` 显式路由（CLI `--strategy STRATEGY_3K` 或交互菜单新增项）抵达。
- **日线 `_scan_market` 热路径不动**：本次仅收敛周线/3K 入口，日线引擎未触碰（零行为变化）。

### 9.2 改动 (3b / 3c)
- `core/scan_engine.py` 新增：`scan_weekly_3k_signals`（原 `scanner_weekly_3k.scan_weekly_3k` 原样搬入）、`format_push_weekly_gap`（原 `scanner_weekly_gap._format_and_push_results`）、`format_push_weekly_3k`（原 `scanner_weekly_3k.main` 格式化段）、`run_weekly_scan(active_strategies, weeks, limit, all_codes)` 统一编排（按家族路由 + 周线表存在性 UX 守护）。
- `hunter.py` 两处周线委托（CLI `--timeframe weekly` 902-924 + 交互菜单 980-1020）统一改为调用 `scan_engine.run_weekly_scan`；交互菜单新增 `STRATEGY_3K` 选项，使 3K 也可经 hunter 单一入口触发。
- 删除 `tools/scanner_weekly_gap.py` + `tools/scanner_weekly_3k.py`（零外部 import：仅 2 个测试文件 + hunter 曾引用，已全部迁移）。
- 测试迁移：`test_weekly_and_noai_flow.py`（`_scan_single_code`→`scan_single_code_weekly`、`scan_weekly_gap`→`scan_weekly_gap_signals`、patch 目标改 `core.scan_engine`）、`test_p1_p2_regression.py`（`_get_strategy_cols` import 改 `core.scan_engine`）。
- `tools/web_viewer.py:281` 提示文案改为 `python hunter.py --timeframe weekly`。

### 9.3 验证
- **等价 diff**：gap 扫描 `_scan_single_code`↔`scan_single_code_weekly` 177 行仅函数名/docstring 差异；3K `scan_weekly_3k`↔`scan_weekly_3k_signals` 仅函数名/注释/import 位置差异（ThreeKStrategy 由模块顶层改为函数内惰性 import，行为一致）→ 零逻辑漂移。
- **质量门禁**：0 红线 / 156 测试守卫通过（扫描 .py 由 90→88）。
- **全流程 pytest**：156 passed。
- **安全冒烟**（真实数据 40 样本，monkeypatch Discord 发送+归档为 no-op，备份还原产物）：gap 与 3K 两路径经 `run_weekly_scan` 全链路无崩溃，真实 Discord 发送 0 次，纯扫描函数返回结构正确。

### 9.4 结论
周线/3K 入口已收敛到 `hunter.py` 单一 CLI + `core.scan_engine` 共享编排，**彻底单引擎（入口/编排层）达成**；3K 行为差异（不归档、独立产物）刻意保留；日线热路径零变化。

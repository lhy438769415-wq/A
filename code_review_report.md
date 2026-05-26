# Brooks-AI Quant System — 全面代码评审报告

> **评审日期**：2026-05-25
> **项目版本**：V7.1（迭代至 V9.15）
> **评审范围**：全部核心源代码（26 个 .py 文件 + 配置/文档）
> **评审原则**：只读分析，不修改任何代码或程序文件

---

## 一、总体评价

| 维度 | 评分（/10） | 一句话评价 |
|------|:-----------:|-----------|
| 架构设计 | **7.5** | 分层清晰、插件化策略注册表设计优秀，但信号追踪双系统共存是架构层面的历史债务 |
| 代码质量 | **6.5** | 核心策略逻辑扎实，但存在裸异常、死代码、函数过长等治理问题 |
| 策略逻辑 | **8.0** | Al Brooks 价格行为理论落地深度罕见，MTR V35/V36 七维度评分体系尤其出色 |
| 性能 | **7.0** | 向量化程度高（3K/Gap策略），并发设计合理，但 `find_swing_points()` 和 `StrategyRegistry` 有拖累 |
| 安全性 | **5.0** | API Key 通过 .env 注入合格，但 Discord Channel ID 硬编码、f-string SQL 拼接是隐患 |
| 可维护性 | **5.5** | DRY 违反严重（三 Gap 策略重复代码）、测试覆盖为零、注释/文档质量参差不齐 |
| 测试覆盖 | **1.0** | tests/ 目录无实际测试文件，是整个项目最大的风险敞口 |

**综合评分：5.8 / 10** — 一个策略深度突出但工程成熟度有待补强的量化系统。

---

## 二、架构设计评审（7.5/10）

### 2.1 优秀的架构决策

#### （1）插件化策略注册表

```python
# core/strategy_registry.py
_STRATEGY_MAP = {
    "MTR_MASTER": MTRStrategy,
    "STRATEGY_3K": ThreeKStrategy,
    "STRATEGY_STRUCTURAL_GAP": StructuralGapStrategy,
    "STRATEGY_GAP_PINBAR": GapPinbarStrategy,
    "STRATEGY_GAP_H2": GapH2Strategy,
}
```

- **抽象基类设计精准**：`BaseStrategy` 仅定义 4 个接口（`name`、`description`、`format_prompt()`、`parse_result()`），职责边界清晰
- **模糊名称匹配**：`get_strategy("3K")` → `ThreeKStrategy`，用户体验友好
- **扩展性强**：新增策略只需实现基类 + 注册映射，无需修改扫描器逻辑

#### （2）四阶段流水线架构

```
_scan_market() → _classify_signals() → _compose_report() → _dispatch_charts()
   数据层             策略层             AI审计层            推送层
```

- 数据获取、信号识别、AI 审核、图表推送四阶段解耦
- 生产者-消费者模式（6 个 AI Worker 线程）有效利用 I/O 等待时间
- 每阶段可独立调试和优化

#### （3）数据层 WAL + 批量写入

```python
# core/data_provider.py
self.conn.execute("PRAGMA journal_mode=WAL")
```

- SQLite WAL 模式 + 独立 `DatabaseWriter` 线程批量 commit，显著提升并发写入性能
- `_fast_local_weekly_aggregation()` 日线→周线本地聚合（<5秒），避免网络请求

### 2.2 架构层面的问题

#### （P0）双套信号追踪系统共存

| 系统 | 文件 | 存储方式 | 功能 |
|------|------|---------|------|
| Signal Tracker | `core/signal_tracker.py` (1188行) | SQLite | 归档、追踪、报表、仪表盘、Discord 推送 |
| Watchlist Manager | `tools/watchlist.py` (109行) | JSON 文件 | NEW→WATCHING→TRIGGERED 状态管理 |

**问题**：两套系统功能高度重叠，维护成本翻倍，且状态不同步风险高。

**建议**：统一到 `signal_tracker.py`（SQLite 方案更成熟），`watchlist.py` 降级为轻量级 CLI 包装器或直接废弃。

#### （P1）StrategyRegistry 无实例缓存

```python
# core/strategy_registry.py
def get_strategy(strategy_name="MTR_MASTER"):
    # 每次调用都创建新实例！
    return MTRStrategy()
```

- 全市场 5000+ 股票扫描时，同一策略被实例化 5000+ 次
- `GapH2Strategy.__init__()` 每次都从 JSON 文件加载规则配置，5000 次 I/O 开销

**建议**：增加 `@lru_cache` 或模块级单例缓存。

#### （P2）周线扫描器独立于主系统

- `tools/scanner_weekly_gap.py`（521行）完全独立实现了一套扫描逻辑
- 与 `hunter.py` 的主流水线无代码共享
- 策略逻辑、EV 评级、生命周期管理全部重新实现

**建议**：将周线扫描纳入主流水线，作为 `timeframe="weekly"` 参数传入。

---

## 三、代码质量评审（6.5/10）

### 3.1 优秀的代码实践

#### （1）向量化计算贯彻彻底

```python
# core/strategies/three_k_strategy.py — 完全向量化，无 for 循环
close = df['close'].values
high = df['high'].values
low = df['low'].values
three_bullish = (close > open_) & (close > close.shift(1)) & ...
```

- `calculator.py` 全部使用 pandas/numpy 向量化运算
- 3K Strategy、Gap+Pinbar、Gap+H2 三个策略均为完全向量化
- 项目规范"禁止 for 循环遍历 K 线"得到较好执行

#### （2）信号生命周期状态机设计清晰

```
PENDING → ACTIVE → WIN / LOSS / EXPIRED / INVALIDATED
```

- `_track_pending()`：向量化 `argmax()` 查找首次入场/失效
- `_track_active()`：向量化查找 SL/TP 触达，**止损优先于止盈**（保守原则）

#### （3）AI 审计 Prompt 设计专业

- XML 格式结构化输入/输出，确保解析稳定性
- 策略特定的交易论据描述（而非通用模板）
- `--no-ai` 旁路模式，支持纯量化运行

### 3.2 需要改进的问题

#### （P0）裸异常捕获泛滥

至少 **6 处**裸异常捕获会吞掉所有错误，导致问题难以定位：

| 文件 | 行号 | 代码 | 风险 |
|------|------|------|------|
| `core/formatter.py` | L81 | `except:` | XML 解析失败时静默返回 None |
| `core/strategies/three_k_strategy.py` | L299 | `except:` | 上下文构建失败时无任何日志 |
| `core/strategies/structural_gap_strategy.py` | L259 | `except:` | 同上 |
| `core/strategies/gap_h2_strategy.py` | L222 | `except:` | 同上 |
| `core/strategies/structural_gap_strategy.py` | L225 | `except AttributeError: pass` | 静默忽略属性错误 |

**建议**：全部替换为 `except Exception as e: logger.warning(f"...: {e}")`，确保问题可追溯。

#### （P0）死代码未清理

| 文件 | 位置 | 描述 |
|------|------|------|
| `core/strategies/mtr_structural_v35.py` | `_check_trendline_break()` | 标注 `DEPRECATED`，当前无调用者 |
| `core/strategies/structural_gap_strategy.py` | L210-214 | `get_idxmin()` / `get_idxmax()` 定义但未使用 |
| `core/strategies/geometric_engine.py` | `identify_swing_objects()` | 注释说明 `idx` 类型混乱（label vs int），潜在 bug |
| `core/strategies/geometric_engine.py` | `_filter_swing_distance()` | 包含 `pass` 语句，逻辑不完整 |

#### （P1）函数过长，职责混杂

| 函数 | 文件 | 行数 | 混合职责 |
|------|------|------|---------|
| `run_tracker_dashboard()` | `core/signal_tracker.py` | ~240行 | 数据获取 + 计算 + 控制台输出 + Discord 推送 |
| `_scan_single_code()` | `tools/scanner_weekly_gap.py` | ~170行 | 数据获取 + 策略计算 + 生命周期过滤 + EV 评级 |
| `generate_chart_bytes()` | `tools/notifier.py` | ~200行 | 三种策略的绘图逻辑 + 注释标注 |

**建议**：每个函数控制在 50 行以内，按单一职责拆分。

#### （P2）SQL 拼接存在理论风险

```python
# core/signal_tracker.py, L399
set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
sql = f"UPDATE signal_archive SET {set_clause} WHERE signal_id = ?"
```

- 列名通过 f-string 拼接，虽然实际来源为内部字典（安全），但违反了参数化查询的最佳实践

---

## 四、策略逻辑评审（8.0/10）

### 4.1 理论落地深度

这是本项目最突出的优势。Al Brooks 价格行为理论的落地程度远超普通量化项目：

#### （1）MTR V35/V36 — 五点结构序列

```
H0（极值高点）→ L1（回调低点）→ H1（反弹高点）→ TL（回调低点）→ H2（信号K线）
```

- **七维度评分体系**（满分 100 分）：
  - 结构精度 15 分 + 趋势线突破 10 分 + 反弹通道 20 分
  - **回调通道质量 35 分**（权重最高，符合 Brooks 理论核心）
  - 极值位置 5 分 + 信号K 质量 10 分 + 趋势深度 5 分
- **几何趋势线引擎**：基于摆动点组合评分选最优下降趋势线
- **斐波那契校验**：H1 反弹 0.382~0.786、TL 回调 0.500~0.786 严格约束

#### （2）3K Strategy — 动量微通道突破

- 八步信号流水线设计严谨：动量确认 → 缺口紧迫感 → 微通道递增 → 高潮规避 → K线形态 → EMA 距离 → 陷阱检测 → 整合
- **Breakout Gap → Measured Gap 后验确认**：不等市场验证不入场
- **高潮规避器**：MM 目标在缺口测试前已达到则过滤

#### （3）Gap+H2 — 经典两腿回调状态机

```
Phase 0（突破）→ Phase 1（首次LHLL）→ Phase 2（HH = High 1）→ Phase 3（二次LHLL = Signal）
```

- 使用 `groupby().cumsum()` 追踪阶段状态，实现优雅
- 状态机设计忠实于 Brooks 的 High 2 定义

#### （4）四因子 EV 评级

回调速度 + 缺口宽度 + 信号K 质量 + 连续阴线惩罚 → A+/A/B/C/D 五级
- 量化了 Brooks 理论中"优质信号"和"劣质信号"的区别

### 4.2 策略逻辑的改进建议

#### （P1）三个 Gap 策略代码大量重复

`structural_gap_strategy.py`、`gap_pinbar_strategy.py`、`gap_h2_strategy.py` 三个策略共享以下逻辑：
- 突破识别（60-bar 高点跨越）
- 锚定历史极值
- 缺口存活监控（Gap 是否被回补）
- 高潮规避器

**代码重复估算**：约 150-200 行相同逻辑在三个文件中重复出现。

**建议**：提取 `GapBaseStrategy` 中间基类，封装共享的突破识别、缺口存活、高潮规避逻辑。

#### （P2）MTR 回测数据缺失

- Gap+Pinbar 有回测数据支撑（周线 EV = +0.5077 R/单，15.2 年，3308 只标的）
- MTR Strategy 有七维度评分体系，但**未见系统级回测验证报告**
- 3K Strategy 未见 EV 数据

**建议**：补充 MTR 和 3K 的历史回测，验证评分体系与实际收益的相关性。

---

## 五、性能评审（7.0/10）

### 5.1 性能亮点

#### （1）向量化计算覆盖率高

- `calculator.py`：336 行，100% 向量化
- 3K Strategy / Gap+Pinbar / Gap+H2：核心信号逻辑全部向量化
- `_track_pending()` / `_track_active()`：`argmax()` 替代循环查找

#### （2）并发设计合理

```python
# hunter.py
MAX_WORKERS = 6  # AI Worker 线程池
# 日线扫描: ThreadPoolExecutor(max_workers=6)
# 周线扫描: ThreadPoolExecutor(max_workers=4)
# 数据同步: ProcessPoolExecutor
```

- 扫描阶段 I/O 密集型任务使用线程池
- 数据同步阶段 CPU 密集型任务使用进程池
- AI 审计使用生产者-消费者模式，6 个消费者并行处理

#### （3）数据库优化到位

- WAL 模式 + 批量 commit（独立写入线程）
- 本地日线→周线聚合（避免网络请求）

### 5.2 性能瓶颈

#### （P1）`find_swing_points()` for 循环

```python
# core/strategies/mtr_structural_v35.py
def find_swing_points(self, df, window=5):
    swings = []
    for i in range(window, len(df) - window):
        # ... 逐行判断
```

- 两个文件独立实现了 for 循环版本（`mtr_structural_v35.py` 和 `geometric_engine.py`）
- 5000+ 股票 × 每股 ~1000 bars = 500 万次循环迭代
- 与项目"禁止 for 循环"规范冲突

**建议**：实现向量化版本：
```python
# 思路：scipy.signal.argrelextrema 或 numpy 滑动窗口比较
from scipy.signal import argrelextrema
swing_highs = argrelextrema(df['high'].values, np.greater, order=window)[0]
```

#### （P1）StrategyRegistry 重复实例化

- `get_strategy()` 在 5000+ 次扫描中每次创建新实例
- `GapH2Strategy.__init__()` 每次读取 JSON 文件（5000 次 I/O）

#### （P2）信号追踪串行处理

```python
# core/signal_tracker.py
for sig in pending_signals + active_signals:
    self._track_single(sig)  # 逐个串行
```

- 当信号数量增长后，串行追踪成为瓶颈

---

## 六、安全性评审（5.0/10）

### 6.1 安全合格项

- **API Key 管理**：通过 `.env` 文件 + `python-dotenv` 注入，未硬编码在代码中
- **Discord Token**：通过环境变量 `DISCORD_BOT_TOKEN` 注入
- **数据库操作**：值通过参数化查询传入（虽然列名有 f-string）

### 6.2 安全隐患

#### （P0）Discord Channel ID 硬编码

```python
# config/settings.py
DISCORD_CHANNEL_ID: str = os.getenv("DISCORD_CHANNEL_ID", "1478561953038336093")
```

- 默认值直接暴露了 Channel ID
- 如果代码仓库泄露，任何人可以向该频道发送消息

**建议**：移除默认值，改为必须通过环境变量提供，缺失时抛出明确错误。

#### （P1）`.env` 文件未在 `.gitignore` 中确认

- 需确认 `.env` 文件已被 `.gitignore` 排除
- `DISCORD_BOT_TOKEN`、`DEEPSEEK_API_KEY` 等敏感凭据不能进入版本控制

#### （P2）SQL 列名拼接

```python
# core/signal_tracker.py
set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
```

- 实际安全（列名来源可控），但不符合防御性编程最佳实践

---

## 七、可维护性评审（5.5/10）

### 7.1 可维护性亮点

- **README.md 详尽**：记录了系统架构、策略说明、版本迭代历史（V7.1 → V9.15）
- **project_diagnosis_overview.md**：记录系统痛点和开发者规范
- **配置外部化**：`gap_optimized_rules.json` 策略阈值外部化，支持自演进
- **版本迭代记录完整**：每次版本修复都有记录

### 7.2 可维护性问题

#### （P0）测试覆盖为零

```
tests/
├── __pycache__/
│   └── *.pyc
└── test_summary.md    # 仅有一个 markdown 文件，无 .py 测试
```

- **全项目零单元测试**
- 核心策略逻辑（MTR 结构匹配、信号状态机、EV 评级）无任何测试覆盖
- 重构时无法验证正确性，是最大的技术风险

**建议优先级**：
1. 为 `calculator.py`（纯函数库）编写单元测试（最容易开始）
2. 为 `BaseStrategy.parse_result()` 编写输入/输出测试
3. 为 `signal_tracker` 状态机编写测试
4. 为关键策略的信号识别逻辑编写快照测试

#### （P1）DRY 违反严重

| 重复代码 | 涉及文件 | 估算重复行数 |
|---------|---------|:-----------:|
| Gap 突破识别 + 锚定 + 缺口存活 + 高潮规避 | structural_gap / gap_pinbar / gap_h2 | ~200行 |
| 信号追踪（Tracker vs Watchlist） | signal_tracker / watchlist | ~100行 |
| `find_swing_points()` | mtr_structural / geometric_engine | ~40行 |

#### （P2）依赖版本不一致

```txt
# requirements.txt
baostock==0.8.9

# README.md 提到
Baostock 需升级至 0.9.1（修复死锁问题）
```

- `requirements.txt` 仍锁定 0.8.9，与 README 记录的修复版本矛盾
- 新开发者 `pip install` 后会安装有死锁风险的版本

#### （P2）注释质量参差不齐

- **好的注释**：`mtr_structural_v35.py` 中 Al Brooks 理论引用详细
- **差的注释**：`geometric_engine.py` 中 "idx 类型混乱" 的 TODO 注释未解决
- **缺失注释**：`formatter.py` 的 `_extract_tag()` 无注释说明 XML 解析逻辑

---

## 八、改进路线图建议

### 第一优先级（立即处理，风险最高）

| 编号 | 问题 | 影响 | 预估工作量 |
|------|------|------|:---------:|
| F-01 | 消除裸异常捕获（6+处） | 线上问题无法定位 | 2h |
| F-02 | 移除 Discord Channel ID 硬编码默认值 | 安全风险 | 15min |
| F-03 | 统一 `requirements.txt` 与 README 版本 | 新开发者环境不一致 | 15min |
| F-04 | 为 `calculator.py` 编写单元测试 | 启动测试体系 | 4h |

### 第二优先级（短期改进，1-2 周）

| 编号 | 问题 | 影响 | 预估工作量 |
|------|------|------|:---------:|
| S-01 | 提取 `GapBaseStrategy` 中间基类 | DRY 违反 | 8h |
| S-02 | StrategyRegistry 增加实例缓存 | 性能 | 1h |
| S-03 | `find_swing_points()` 向量化改造 | 性能 + 规范 | 4h |
| S-04 | 清理死代码（DEPRECATED 函数、未使用函数） | 可维护性 | 2h |
| S-05 | 统一信号追踪系统（废弃 watchlist.py） | 架构简化 | 8h |

### 第三优先级（中期优化，1-2 月）

| 编号 | 问题 | 影响 | 预估工作量 |
|------|------|------|:---------:|
| M-01 | 周线扫描纳入主流水线 | 架构统一 | 16h |
| M-02 | 信号追踪并行化 | 性能 | 4h |
| M-03 | 补充 MTR/3K 策略回测验证 | 策略可靠性 | 24h |
| M-04 | 长函数拆分（dashboard/scan_single） | 可维护性 | 8h |
| M-05 | 策略信号逻辑单元测试覆盖 > 80% | 质量保障 | 40h |

---

## 九、结论

Brooks-AI Quant System 是一个**策略深度突出、工程成熟度有待补强**的量化交易系统。

**核心优势**：
- Al Brooks 价格行为理论的落地深度在同类项目中极为罕见
- MTR V35/V36 七维度评分体系设计精密
- 插件化策略架构扩展性强
- 向量化计算覆盖率高，并发设计合理

**核心风险**：
- **零测试覆盖**是最大风险敞口，任何重构都可能引入隐蔽 bug
- 双套信号追踪系统增加了状态不同步的风险
- 三个 Gap 策略的代码重复使维护成本倍增
- 裸异常捕获会掩盖线上问题

**建议**：优先启动测试体系建设（F-04），然后逐步推进 DRY 重构（S-01）和死代码清理（S-04）。策略逻辑本身的质量很高，工程治理的投入将直接转化为系统的长期可靠性。

---

> *本报告基于 2026-05-25 的代码快照生成，仅做只读分析，未修改任何代码或程序文件。*

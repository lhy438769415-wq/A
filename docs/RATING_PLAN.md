# 信号评级体系统一实施计划 (RATING_PLAN)

> 创建日期: 2026-07-24 | 修订: 2026-07-24 (rev.2 经 PA 专家按 16-Step SOP 审查)
> 参考: `gap_structural_rating_briefing.md` (Gap Structural 评级体系演进简报)
> **PA 理论裁决源**: `config/sop_rules.md` —— 16-Step SOP（Al Brooks 价格行为学交易全周期，唯一合规依据）

---

## 0. 需求理解（对齐用户原话 + PA 全周期约束）

用户原话：*"我只基于历史数据回测和历史扫描数据统计的情况识别了几个高概率的正向因子，基于此才建立了评级体系，但是，其他策略并没有基于数据制定评价体系。"*

拆解：
1. **唯一有数据评级的是 Gap Structural（缺口家族）**：基于 4988 样本回测 + 1055 深度标注 + EV 研究识别出"高概率正向因子"（回调速度、缺口宽度、K线质量、连阴数、时间衰减），做成四因子积分制。
2. **其他 5 个策略没有数据驱动评级**：MTR 是手调七维启发式；pinbar/h2 只有 `sig_bar_quality` 裸质量分（永远判 C）；3K/AWIL 全程无。
3. **目标**：让每个策略都基于**自己的**历史回测建立数据评级，且跨策略有一致的输出与推送呈现。

**PA 全周期硬约束（本次新增，最高优先级）**：本系统理论基础是纯 Al Brooks 价格行为学。所有评级因子**必须能在 `config/sop_rules.md` 的 16-Step SOP 中找到 PA 理论依据**（标注 Step 编号），不得引入任何非 PA 因子（详见 §2.5 因子白名单与黑名单）。

---

## 1. 现状诊断（代码取证，非臆断）

| 策略 | 周期 | 当前评级来源 | 是否数据驱动 | 问题 |
|:---|:---|:---|:---|:---|
| **STRATEGY_STRUCTURAL_GAP** | 日+周 | 日线 `get_signal_info` 极品/毒性 2因子；周线 `scanner_weekly_gap.py` 完整四因子+时间衰减 | ✅ 是（V9.0 四因子） | **碎片化**：日线只 2 因子、周线 4 因子，两套逻辑不统一 |
| STRATEGY_GAP_PINBAR | 周 | 仅 `metadata.score_column='sig_bar_quality'`(0-1) | ❌ 否 | 无 `ev_rating` → hunter 回退用质量分 0-1 永远判 C |
| STRATEGY_GAP_H2 | 周 | 仅 `sig_bar_quality`(0-1) | ❌ 否 | 同上（有 V9.15-17 周线回测但无单信号评级） |
| MTR_MASTER | 日 | `mtr_score` 七维(结构15+趋势10+反弹20+回调35+极值5+信号K10+深度5) | ❌ 手调启发式 | 已是 0-100，但权重非数据驱动；维度2 EMA 权重偏高 |
| STRATEGY_3K | 日 | 无 | ❌ 无 | 无评级字段 |
| STRATEGY_AWIL | 日 | 无 | ❌ 无 | 无评级字段 |

**调用链断点**（根因）：
- `BaseStrategy.get_signal_info` 把 `result['score']` 绑定到 `metadata.score_column`（= `sig_bar_quality`，0~1 肉眼质量分）。
- `hunter` 用 `info['score']` 做 `≥80/65/50` 分档 → 0~1 的分永远 <50 → **非缺口策略恒判 C**。
- 缺口家族的"真评级"走 `extra_info['ev_rating']` 文本，与 `score` 字段**并行存在、口径不一**，hunter 两条路择一，易错乱。

---

## 2. 设计原则（已与用户逐条确认 + PA 审查修订）

1. **不强行统一评分公式**：6 策略 PA 签名不同（MTR=五点反转、缺口家族=Measuring Gap、3K=动能中继、AWIL=空头失败），通用公式会抹掉各自预测性。
2. **统一输出契约**：每个策略输出 `score(0-100)` + `letter(A+/A/B/C/D)` + `factor 证据(名称/数值/历史胜率)`。日线/周线主流程、Discord 卡片读同一套字段。
3. **框架先用现有手调权重跑通**（Phase 0），**Phase 2 再接回测数据表** `config/rating_factors.json` 校准权重。
4. **覆盖范围**：全部 6 策略。
5. **分档**：每策略 `score_map` 把原生评分单调映射到 0-100；**Phase 0 暂用全局阈值 `≥80 A+ / ≥65 A / ≥50 B / <50 C`，预留 `<30 D`（毒性由策略 `toxic` 标记）；Phase 2 改为逐策略切点**（见 §4 说明 + 验收 §7）。
6. **Discord 推送流**：简报（全信号、按评级分组、无图）→ A 级理由（单独消息，A+/A 富卡片+图+因子证据+人工复核建议）；**非 A（B/C）默认不出图**。
7. **Phase 0 因子范围**：仅复用各策略**现有已算**因子，不新造因子（新因子与权重留待 Phase 2 回测）。
8. **PA 铁律（用户硬约束）**：`volume` **绝不**进入任何策略的评级/信号因子。原始 `volume` 列虽存于 DB，`calculator.py` 亦算 `vol_ma20`/`relative_vol`，但**无任何策略将其用于信号或评级**（`three_k_strategy.py:398` AI prompt 明令 "do not mention Volume"）。EMA20 仅可作为**磁力位/背景上下文**或 **Always-In 翻转的 MA_Gap 证据（SOP Step 2/7）**，**不可**作为独立动量/趋势强度打分因子（详见 §2.5 黑名单）。
9. **因子可溯源 PA（本次新增，硬约束）**：每个评级因子必须在 `config/sop_rules.md` 16-Step SOP 中可定位理论依据（标注 Step 编号）；无法溯源的因子一律不得进入评级。本计划的每个策略因子表均附「PA 依据(SOP步)」列。
10. **统一 PA 因子库（本次新增）**：跨策略通用 PA 核心（信号K质量、RR、陷阱过滤、缺口完整性、Always-In、磁力空间）沉淀到 `core/rating_core.py` 共享；各策略专属 PA 签名因子留在策略内（见 §4.x）。

---

## 2.5 PA 因子白名单 / 黑名单（因子选取的合规边界）

> 依据 `config/sop_rules.md` 16-Step SOP。评级因子只能从白名单取，黑名单因子永不进入评级。

**✅ 白名单（纯 PA，可溯源 SOP）**
- 信号K质量（Step 4）：`close_loc`(收盘位置) / `body_pct`(实体占比) / `tail_ratio`(下影/上影占比) / `overlap`(与前K实体重叠度)
- 身体缺口 Body Gap（Step 6 Ind.2）：缺口是否保持开放、缺口宽度%
- 回调速度/深度（Step 3 真空 / Step 8）：`pb_bars` / 回调 vs 前腿幅度
- 连阴/连阳数（Step 7/8）：`pb_consec_bear` / `three_bulls`
- Always-In 方向（Step 7）：`always_in` / 翻转证据（MA_Gap、连续K、突破）
- 陷阱过滤（Step 8）：失败突破 FBO / 高潮陷阱 Climax / 第二腿陷阱 Second-Leg
- 跟随K Follow-through（Step 6 Ind.1）
- 微型形态（Step 5）：ii / ioi / oo / 微型双顶底
- 交易者方程 RR≥2（Step 9）
- 到目标空间 Distance_To_Target（Step 12，≥1×Risk 非强趋势）
- 极值/结构位置（Step 2 MM / Step 11）：Fib 回撤、磁力位、EMA20 作为背景/翻转证据
- 两腿对称（Step 6 H2）：L2 深度 < L1

**❌ 黑名单（禁用）**
- `volume` / `vol_ma20` / `relative_vol` / `amount` / `成交额` —— 系统铁律，永不用于评级
- `ADX` / `trend_strength` / `linreg_res` / **EMA 斜率 / 均线多头排列** —— 计算器里存在但属非纯 PA 动量指标，禁止作为评级因子（EMA20 仅作磁力/背景/MA_Gap 翻转证据，见原则 8）
- 任何"放量确认/量能配合"类表述

---

## 3. 输出契约规范 — `core/rating.py`（Phase 0 新建）

```python
# 硬约束(顶部注释): 评级因子严禁 import relative_vol/vol_ma20/volume/amount/
# ADX/trend_strength/linreg_res，且不得将 EMA 斜率/均线多头排列作为打分因子。
# 所有因子必须能在 config/sop_rules.md 16-Step SOP 找到 PA 依据。

@dataclass
class RatingFactor:
    name: str            # 因子中文名, 如 "回调速度"
    value: float         # 实际数值, 如 pb_bars=3
    hit: bool            # 是否命中加分条件
    weight: float        # 贡献分 (如 +2)
    win_rate: Optional[float]   # 该因子的历史胜率(Phase 2 填充, Phase 0 为 None)
    sop_ref: str         # PA 理论依据, 如 "SOP Step 8"
    note: str            # PA 理据(可进卡片)

@dataclass
class RatingResult:
    raw_score: float     # 策略原生分 (structural: ev_score -5~+9; mtr: 0-100)
    score: int           # 归一化 0-100 (经 score_map)
    letter: str          # 'A+' / 'A' / 'B' / 'C' / 'D'
    factors: List[RatingFactor]
    toxic: bool = False  # D 级标记

def band(score: int, toxic: bool = False) -> str:
    """Phase 0 全局分档: >=80 A+, >=65 A, >=50 B, else C; toxic或<30 -> D。
       Phase 2 起由各策略 rating_factors.json 的逐策略切点替代。"""
    if toxic or score < 30: return 'D'
    if score >= 80: return 'A+'
    if score >= 65: return 'A'
    if score >= 50: return 'B'
    return 'C'

def map_native(native: float, score_map) -> int:
    """各策略单调映射原生分 -> 0-100 (clamp)。"""
```

`BaseStrategy` 新增抽象/默认方法：
```python
@classmethod
def compute_rating(cls, df: pd.DataFrame) -> RatingResult:
    """默认实现: 从 score_column 取质量分做退化评级; 各策略覆写。"""
```

`scanner.py` / `scanner_weekly_gap.py` / `hunter.py` 统一从 `res['info']['rating']`(RatingResult 序列化) 读取，删除对 `score`/`ev_rating` 双字段的歧义读取。

---

## 4. 各策略评级设计

> 每策略因子表含「PA 依据(SOP步)」「极性」「可校准」「权重方向」。

### 4.1 STRATEGY_STRUCTURAL_GAP（日+周）— 已有数据评级，需**统一两套逻辑**

**四因子（简报 V9.0，数据驱动，全部纯 PA）**：

| # | 因子 | 变量 | 加分 | 扣分 | PA 依据(SOP) | 数据证据 |
|:-:|:---|:---|:---|:---|:---|:---|
| 1 | 回调速度 | `pb_bars` | ≤4 → +2 | >7 → -2 | Step 8 陷阱 / Step 3 真空（急跌急复=空头被套） | ✅ 4988+1055样本 |
| 2 | 缺口宽度 | `gap_size_pct` | >7% → +2 | <3% → -1 | Step 6 Ind.2（缺口=失衡/紧迫） | ✅ |
| 3 | K线质量 | `sig_bar_quality` | >0.8 → +1 | <0.5 → -1 | Step 4 Signal Bar Quality | ✅ |
| 4 | 连阴扣分 | `pb_consec_bear` | — | ≥3 → -1 | Step 7 连续K / Step 8 竭尽 | ✅ |
| 附 | 时间衰减 | `bars_passed` | — | >5 → -1, >10 → -2 | Step 12 空间 / Step 16 持仓（生命周期，低权） | ⚠️ 生命周期 |

**原生分 `ev_score` ∈ [-5, +9]**。**归一映射**：`score = clamp(50 + 10 * ev_score, 0, 100)`
**数据证据（简报 4.2）**：极品组合(质量>0.8且连阴<2) 胜率 62.5–68%、EV +0.45~0.6R；毒性组合(质量≤0.5且连阴≥2) 胜率 28–33%、EV -0.25R。
**Phase 0 动作**：把 `scanner_weekly_gap.py` L211-233 的四因子逻辑**上提**到 `StructuralGapStrategy.compute_rating`（日线/周线共用），删除 `scanner_weekly_gap.py` 内联重复；日线 `get_signal_info` 现 2 因子改为调用同一 `compute_rating`，消除碎片化。

### 4.2 STRATEGY_GAP_PINBAR（周）— 待建（缺口家族共享四因子骨架 + Pinbar 签名）

Phase 0 复用 4.1 四因子骨架（缺口家族同根），差异在 `pb_bars` 用 pinbar 回调窗口、`gap_size_pct` 用缺口测试位。V9.12 EV 研究证明"周线 缺口+Pinbar+Gap Floor 止损 → +0.456R/笔"。

| 因子 | 变量 | PA 依据(SOP) | 极性 | 可校准 | 权重 |
|:---|:---|:---|:---|:---|:---|
| 四因子骨架 | `pb_bars`/`gap_size_pct`/`sig_bar_quality`/`pb_consec_bear`/`bars_passed` | 同 4.1 | — | ✅ | 高/高/中/中/低 |
| **Pinbar 下影比** | `pinbar_tail_ratio`(≥0.40) | Step 4 Tail Logic（长下影=拒绝/空头陷阱） | 正 | ⚠️ V9.12校准 | 中-高 |
| **EMA20 刺破回收** | `ema20_pierce_reclaim` | Step 8 Trap 2(FBO) + Step 2 磁力位 | 正 | ✅ V9.12 +0.456R | 中 |

归一：同 4.1 `50 + 10*ev_score`。

### 4.3 STRATEGY_GAP_H2（周）— 待建（四因子骨架 + H2 签名）

V9.15-17 回测：周线 ~1212 信号、56% 胜率。Phase 0 复用四因子骨架；**Phase 2 用该回测 + 两腿对称性因子**校准。

| 因子 | 变量 | PA 依据(SOP) | 极性 | 可校准 | 权重 |
|:---|:---|:---|:---|:---|:---|
| 四因子骨架 | 同 4.1 | 同 4.1 | — | ✅ | — |
| **两腿对称性** | `leg2_shallower`(L2<L1深度) | Step 6 / 经典 H2（二腿更浅=被困空止损燃料） | 正 | ⚠️ V9.15-17 | 中 |
| **缺口全程存活** | `gap_h2_open` | Step 6 Gap Integrity | 正 | ✅ 周线56%胜率 | 中 |

### 4.4 MTR_MASTER（日）— 已有七维手调，标记待校准 + EMA 维度约束

七维(0-100)已是归一尺度，Phase 0 直接包进 `compute_rating`，`score = round(mtr_score)`，`letter = band(score)`，`calibrated=False`。

| 维度 | 变量 | PA 依据(SOP) | 极性 | 可校准 | 权重方向 |
|:---|:---|:---|:---|:---|:---|
| 结构精度 | Fib 回撤 | Step 2 MM / Step 1 | 正 | ✅ | 中 |
| 趋势线突破 | EMA20突破+均线缺口 | Step 7 Method 3 **MA_Gap 翻转证据**（非动量因子） | 正 | ⚠️ | **低（约束！原10→建议5~8，避免退化为均线顺势过滤器）** |
| 反弹通道 | gap/overlap/连阳 | Step 5/6/7 | 正 | ✅ | 高 |
| 回调通道质量 | gap/overlap/连阴/高潮 | Step 8 Climax Trap | 正 | ✅ | 最高 |
| 极值位置 | TL vs L1 | Step 2 磁力 / Step 11 | 正 | ✅ | 低 |
| 信号K质量 | — | Step 4 | 正 | ✅ | 中 |
| 趋势深度 | ATR跌幅 | Step 2/12 幅度 | 正 | ✅ | 低 |

**Phase 2** 用 `backtest_*` + 全历史回测把七维权重从手调改为数据驱动（维度2 EMA 权重下调）。

### 4.5 STRATEGY_3K（日）— 待建（最小手调集，标"待校准"）⚠️ 已移除违规因子

> **PA 审查修订**：原草案 `trend_align`(均线多头) 违反 SOP Step 2/7（EMA 仅作磁力/背景，不可作动量打分因子），且 3K 代码本身只用 `env_ok`（距 EMA≥0.2ATR=突破空间），从无"均线多头"逻辑 → **删除**。原 `(1-loc)*100` 对动能中继方向错误 → **从评级剔除**。

Phase 0 因子（复用现有已算列，全部纯 PA）：

| 因子 | 变量 | PA 依据(SOP) | 极性 | 可校准 | 权重 |
|:---|:---|:---|:---|:---|:---|
| 信号K质量 | `body_pct`/`morph_ok` | Step 4 | 正 | ⚠️ 补回测 | 中 |
| **缺口保持开放** | `breakout_gap_open` | Step 6 Body Gap（Measured Gap 完整） | 正 | ✅ 策略已算 | 高 |
| **陷阱过滤通过** | `trap_check_ok`/`climax_ok` | Step 8(FBO/Climax) | 正 | ✅ 策略已算 | 高 |
| 三连阳强度 | `three_bulls`+递增高低 | Step 6 Ind.3 / Step 7 | 正 | ⚠️ 补回测 | 中 |
| RR≥2 | `rr` | Step 9 Traders Equation | 正 | ✅ | 中 |

原生分 = 手调加权（如 缺口开放:+2, 陷阱通过:+2, 信号K质量>0.8:+1, 三连阳:+1, RR≥2:+1）；归一 `score = clamp(40 + 12*raw, 0, 100)`（占位，Phase 2 改逐策略切点）。
**Phase 2 跑 3K 全历史回测**定权重与字母切点（当前提示词自认 40–50%，基础率偏低，切点须放宽）。

### 4.6 STRATEGY_AWIL（日）— 待建（最小手调集，标"待校准"，纯 PA）

| 因子 | 变量 | PA 依据(SOP) | 极性 | 可校准 | 权重 |
|:---|:---|:---|:---|:---|:---|
| 收盘位置 | `close_loc`(≥0.98) | Step 4 | 正 | ⚠️ 补回测 | 高 |
| **空头失败次数** | `failed_bear_count`(L1/L2 次数) | Step 7 Always-In / H2 被困空 | 正 | ⚠️ 补回测 | 高 |
| **全程站上EMA20** | `awil_ema_above` | Step 7 Method 3（**Always-In Long 证明，非动量因子**） | 正 | ✅ 策略已算 | 中 |
| 下影支撑回收 | `tail_below_support` | Step 8 Trap 2 + Step 4 Tail（空头陷阱） | 正 | ⚠️ 补回测 | 中 |
| 两腿对称性 | `leg2_shallower` | Step 6 H2 | 正 | ⚠️ 补回测 | 中 |
| RR≥2 | `rr` | Step 9 | 正 | ✅(TP=2R) | 中 |

归一同 4.5。`awil_ema_above` 须明确标注为 Always-In Long 证明（Step 7 Method 3），**不是**趋势对齐动量因子。
**Phase 2 跑 AWIL 全历史回测**定权重（当前无回测数据，必须补）。

### 4.x 统一因子原则（跨策略通用 PA 核心 vs 各策略专属签名）

**跨策略通用 PA 核心（建议沉淀到 `core/rating_core.py` 共享库）**
- 信号K质量（Step 4）：`close_loc` / `body_pct` / `tail_ratio`
- 交易者方程 RR≥2（Step 9）
- 陷阱过滤（Step 8）：FBO / Climax / 第二腿陷阱
- 缺口完整性与身体缺口（Step 6）
- Always-In / 连续K 方向（Step 7）
- 测距空间/磁力位（Step 2 / Step 12）

**各策略专属 PA 签名（不可挪用）**
- 缺口宽度% / 回调速度 `pb_bars` → 仅缺口家族（4.1–4.3）
- Pinbar 下影比 → 仅 GAP_PINBAR
- 两腿对称性 / 空头失败次数 → 仅 H2 / AWIL
- 三连阳 → 仅 3K
- MTR 七维结构（Fib/趋势线/反弹/回调/极值/信号K/深度）→ 仅 MTR

---

## 5. 实现阶段

### Phase 0 — 输出契约 + 框架（手调权重，先跑通）
1. 新建 `core/rating.py`：`RatingFactor`(含 `sop_ref`) / `RatingResult` / `band()` / `map_native()`；**顶部硬约束注释禁止 volume/ADX/EMA斜率类因子**。
2. 新建 `core/rating_core.py`：沉淀 §4.x 跨策略通用 PA 核心因子（信号K质量、RR、陷阱过滤、缺口完整性、Always-In、磁力空间）。
3. `BaseStrategy` 加 `compute_rating()` 默认实现 + `get_signal_info` 注入 `rating`（序列化 RatingResult 进 `extra_info`）。
4. 各策略覆写 `compute_rating`：
   - structural：上提四因子（删 `scanner_weekly_gap.py` 内联）。**✅ 因子纯 PA，径直接入。**
   - pinbar/h2：复用四因子骨架 + 各自专属签名（pinbar 下影比 / 两腿对称+缺口存活）。
   - MTR：包七维，**维度2 EMA 权重约束为低档**（Step 7 MA_Gap 证据）。
   - 3K：最小手调集（**已删 `trend_align`**，改 `breakout_gap_open`+`trap_check_ok`+`climax_ok`+`three_bulls`+`rr`）。
   - AWIL：最小手调集（纯 PA，无 volume）。
5. `scanner.py` / `scanner_weekly_gap.py` / `hunter.py` 统一读 `info['rating']`，删除 `score`/`ev_rating` 双字段歧义；`metadata.score_column` 改为由 `compute_rating` 驱动。
6. 测试：`tests/test_rating.py` 锁四因子映射 + 各策略 `compute_rating` 烟雾测试 + **PA 合规断言**（无 volume/EMA斜率因子被引用）。

### Phase 1 — Discord 推送重构
1. 拆"简报(全信号文本, 按 A+/A/B/C/D 分组, 无图)" + "A 级理由(单独消息, 富卡片+图+因子证据+人工复核建议)"。
2. 卡片加"因子证据"区（每因子 名称/数值/历史胜率/PA依据/贡献），便于一眼复核。
3. 非 A（B/C）默认不出图（仅在简报文占一行）；图限量沿用 `MAX_CHARTS_PER_RUN`。

### Phase 2 — 数据校准（依赖回测）
1. 跑 6 策略全历史回测 → `config/rating_factors.json`（按 `策略×周期` 分节，每因子含 命中胜率/EV/权重）。
2. 各策略 `compute_rating` 权重从手调切到读表；**字母切点由全局统一改为逐策略切点**（以该档历史胜率反推：A+ = 该策略历史胜率 ≥55% 的得分段，避免低基础率策略恒判 C/D，如 3K 放宽 A+ 切点）。
3. 缺口家族(pinbar/h2) 接 V9.12/V9.15-17 研究；MTR 七维重标定（维度2 下调）；3K/AWIL **必须先补全历史回测**（当前无），否则不能声称"数据驱动"。

### Phase 2 回测结果（v1, 2026-07-24 — 已被审计推翻）
- 工具：`tools/rating_calibration_backtest.py` v1 + `core/backtest_engine.py` v1。
- 结果：9 批中 4 批 A+≥55%（structural日/周、mtr、3K边际），5 批失败（pinbar日/周、h2日/周 倒挂；awil 退化）。
- **v1 方法学硬伤（用户质疑后审计确认）**：①零交易成本（毛口径高估）；②退出模型不一致（缺口家族用各自tp_col、MTR用30根内极值判定，不可比且MTR被高估）；③R:R未受控；④**样本内过拟合**（权重符号从全样本推又同样本验）；⑤3K「PASS」放水（A+ 55.6% < 整体56.5%，不区分）；⑥无统计显著性（无Wilson CI）；⑦无年度/regime分层。v1 结论仅作线索，**不可作为校准依据**。

### Phase 2 回测 v2（专业修订版，2026-07-25 进行中）
- 工具：`tools/rating_calibration_backtest.py` v2 + `core/backtest_engine.py` v2（`simulate_trade_unified`，新增 `wilson_ci`）。
- 方法学修正：①统一成本敏感模型（全策略同 Buy-Stop 入场 + 初始止损 + 各策略真实目标位 tp_col 作 R:R + A股成本 印花税0.05%卖/佣0.03%双边/滑点0，对齐 `backtest_gap_h2.py` 已验证基线）；②**训练/测试切分**（前60%时间推因子权重、后40%验档位，杜绝样本内过拟合）；③每档附 **Wilson 95% 置信区间**；④**年度/regime 分层**净胜率；⑤逐信号 dump `calibration_signals.jsonl` 可复用免重跑；⑥净胜率/净EV 为首要指标。
- 验收标准修正：原「A+ 毛胜率≥55%」在扣费+统一模型下多数策略难达，故 v2 主判据改为**「评级是否区分」（A+ 净胜率 > 整体净胜率 且档位单调）+ 样本外(test)显著**」，绝对55%作为参考而非硬门槛。
- 状态：全量后台运行中（task 7dAaAH），产出 `config/rating_factors.json`(v2) + `calibration_report_v2.txt` + `calibration_signals.jsonl`。

### Phase 3 — 验证
1. 历史信号回放：检查 A+/A 占比合理、且"胜率随评级递增"（A+ > A > B > C > D）。
2. 回归测试：评级契约 + 各策略 `compute_rating` + 推送渲染。

---

## 6. 风险与迁移

- **结构性碎片化修复是最高价值低风险项**：structural 日线 2 因子 → 统一四因子，不改因子定义，仅搬代码。
- **`score_column` 语义变更**：原绑定 `sig_bar_quality`(0-1)，改为 `compute_rating` 输出的 0-100；需同步改 `hunter` 分档读取点，避免回归（参考 P1④b 教训：改评分路径要全链路测）。
- **MTR 七维保留但标 `calibrated=False`** + 维度2 EMA 权重约束；不破坏现有日线推送，仅切换读取字段。
- **DB schema 冻结**：评级为推送层计算，不新增库表列（除非后续要落 `signal_archive.rating`，那走 AGENTS.md 规定的 DDL 单源流程）。
- **PA 合规冲突清单（本次审查新增）**：
  1. 🔴 3K `trend_align`(均线多头) 违反 SOP Step 2/7 → **已删**，改纯 PA 因子。
  2. 🟠 MTR 维度2「EMA20突破」权重偏高会退化为均线过滤器 → 约束为 MA_Gap 翻转证据，权重降至低档。
  3. 🟠 3K 原 `(1-loc)*100` 对动能策略方向错误 → 从评级剔除。
  4. 🟠 全局统一切点 80/65/50 与差异基础率冲突 → Phase 2 改逐策略。
  5. 🟢 volume/relative_vol/vol_ma20 全程零引用 → 合规，保持；`core/rating.py` 顶部加硬约束注释。

---

## 7. 验收标准

- [x] 6 策略全部经 `compute_rating` 输出 `RatingResult`，无策略恒判 C。
- [x] 日线/周线缺口家族评级口径一致（同一四因子）。
- [x] hunter 只从 `info['rating']` 取评级，无 `score`/`ev_rating` 双字段。
- [x] **每个评级因子可溯源到 `config/sop_rules.md` 16-Step SOP（标注 sop_ref）**，且 `core/rating.py` 未 import 任何黑名单指标（volume/ADX/EMA斜率等）。
- [ ] Discord 简报 + A 级理由两步走，非 A 不出图。（Phase 1）
- [x] `tests/test_rating.py` 通过，含 PA 合规断言，全量测试不降。
- [ ] Phase 2 数据闭环：校准数据已生成（`config/rating_factors.json`），**4/9 批通过 A+≥55%**（structural日/周、mtr、3K边际）；**5/9 批失败**（pinbar日/周、h2日/周 倒挂；awil 退化）。字母切点改为逐策略 + 失败策略处置方案待定（见下方决策）。

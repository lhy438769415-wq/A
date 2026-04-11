# 测试文档

本目录包含项目的单元测试和回归测试。

## 测试结构

| 测试文件 | 覆盖模块 | 用例数 | 备注 |
|:---|:---|:---:|:---|
| `test_calculator.py` | `core/calculator.py` | 15 | V9.7 新增，涵盖 ATR/EMA/信号棒指标计算 |
| `test_three_k_strategy.py` | `core/strategies/three_k_strategy.py` | ~8 | 3K 策略信号检测准确性 |
| `test_phase1_regression.py` | 多模块回归 | ~10 | V9.6 Phase1 审计后的回归保护 |
| `test_mtr_flow.py` | MTR 策略流程 | ~5 | MTR 信号端到端验证 |

## 运行测试

### 推荐方式: 使用 pytest
```bash
# 运行所有测试 (失败 2 个即停止)
pytest tests/ --maxfail=2

# 运行单个测试文件
pytest tests/test_calculator.py -v

# 运行带关键词过滤的测试
pytest tests/ -k "atr" -v
```

### 手动运行单个测试
```bash
python -m pytest tests/test_calculator.py
python -m pytest tests/test_three_k_strategy.py
```

## 测试覆盖范围

目前测试覆盖了以下核心模块：
- ✅ 核心指标计算 (`core/calculator`) — 15 个用例
- ✅ 3K 策略信号 (`core/strategies/three_k_strategy`) — 含边界条件
- ✅ Phase1 回归保护 — 确保 V9.6 审计修改不引入 Bug
- ✅ MTR 策略流程 — 端到端信号生成

### 待补充
- 📋 `core/data_provider` — 数据层读写
- 📋 `core/scanner` — 扫描器调度
- 📋 `core/formatter` — 提示词格式化
- 📋 `tools/notifier` — Discord 推送

## 测试策略

1. **单元测试**: 验证单个函数/模块的正确性 (向量化计算精度)
2. **回归测试**: 确保代码重构不破坏已有逻辑 (Phase1/Phase2)
3. **边界测试**: 空值、极端输入、数据不足等边界场景
4. **性能基线**: 核心计算的性能不得明显退化
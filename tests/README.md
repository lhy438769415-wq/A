# 测试文档

本目录包含项目的单元测试和集成测试。

## 测试结构

- `test_calculator.py` - 测试指标计算模块
- `test_api_client.py` - 测试API客户端模块
- `test_data_manager.py` - 测试数据管理模块
- `test_scanner.py` - 测试扫描器模块
- `test_formatter.py` - 测试格式化模块
- `test_integration.py` - 集成测试
- `run_tests.py` - 运行所有测试的脚本
- `pytest_runner.py` - 使用pytest运行测试的脚本

## 运行测试

### 方法1: 使用自定义运行脚本
```bash
python tests/run_tests.py
```

### 方法2: 使用pytest
```bash
python tests/pytest_runner.py
```

### 方法3: 手动运行单个测试
```bash
python tests/test_calculator.py
python tests/test_api_client.py
# ... 其他测试文件
```

## 测试覆盖

目前测试覆盖了以下主要模块：
- 核心指标计算 (`core/calculator`)
- API调用 (`core/api_client`)
- 数据管理 (`tools/data_manager`)
- 扫描逻辑 (`core/scanner`)
- 提示词格式化 (`core/formatter`)

## 测试策略

1. **单元测试**: 验证单个函数/模块的正确性
2. **集成测试**: 验证模块间的协作和数据流
3. **边界测试**: 测试空值、异常输入等边界情况
4. **性能测试**: 简单的性能监控和资源使用检查

所有测试都使用Python标准库，不依赖额外的测试框架（除了pytest），确保测试环境的简洁性和可移植性。
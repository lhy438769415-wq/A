# 存量红线违规清理：方案选择（待拍板）

> 前置：8 个共识死代码文件已 `git commit`（检查点，可回退）。本文件只解决"剩余存量违规"如何清。

## 一、真实地形（实测，已排除门禁脚本自身文档提及）

| 违规类型 | 真实数量 | 说明 |
|:--|:--:|:--|
| `sys.path.insert` | ≈ 35 | 多为 `tools/*.py` 直接运行时的"项目根引导"，非乱写 |
| `logging.basicConfig` | ≈ 40 | 散落的日志初始化，无统一入口 |
| `裸 except:` | ≈ 32 | 含 tests 内可能合法的少数 |
| 分布文件数 | ≈ 50 | 与红线指定的"替代物" `core/paths.py`/`core/log_config.py` 已就位 |

**关键事实修正**：`core/__init__.py` **存在**，`core` 是合法包；只要项目根在 `sys.path` 上，`import core` 即可解析。

## 二、根因（为什么不能机械替换）

项目同时存在两种执行方式：
- **作为包导入**：`hunter.py` 等被调用时，`core` 能 import（因为根在 path 上）。
- **脚本直接运行**：`python tools/foo.py` 时，解释器只认脚本所在目录，`tools/foo.py` 为了 `import core` 必须自己 `sys.path.insert(项目根)`。

所以每个 `tools/*.py` 的 `sys.path.insert` 是**引导到包的必要步骤**。直接删掉 → `import core` 失败 → 脚本崩。这正是"看似违规、实则必要"的陷阱。

另外：测试里 `sys.path.insert(..., tmpdir)` 是 pytest 隔离机制，合法。当前门禁的 `sys.path.insert` 规则**过严**，会把测试也算违规，需修正规则（仅禁"项目根引导"且非经 `ensure_importable()`）。

## 三、三种方案

### 方案 A（治本·推荐度中）项目可安装化
- 加 `pyproject.toml`（`packages=[core]`），`pip install -e .`
- 之后所有 `import core` 自动解析 → **删除全部 `sys.path.insert` 引导**
- `basicConfig` → `get_logger`；`裸 except` → `except Exception` + 日志
- 门禁保持严格，最终全绿
- **代价**：需验证 `hunter.py` 及 `tools/*.py` 的"直接运行"入口仍正常（执行方式可能需改为 `python -m` 或 console_scripts）
- **风险**：中（改了执行方式）

### 方案 B（折中·低风险）集中引导 + 放宽门禁
- 保留"引导"但集中到唯一函数 `ensure_importable()`（已在 `core/paths.py`）
- `tools/*.py` 首行改为 `from core.paths import ensure_importable; ensure_importable()`
  - ⚠️ 鸡生蛋：导入 `core.paths` 前需项目根已在 path。故 `tools` 脚本仍需**一行极简引导**到 `core.paths`，门禁豁免该特定模式
- `basicConfig`/`裸 except` 照常修
- 门禁放宽：仅禁"非 `ensure_importable` 的项目根引导" + 豁免测试
- **代价**：`sys.path.insert` 未彻底消除，只是规范化、收敛到一处
- **风险**：低

### 方案 C（最小·低风险）先修两块低风险违规
- `裸 except`（≈32）→ `except Exception` + 业务上下文日志：纯收益、零引导依赖
- `basicConfig`（≈40）→ `get_logger`：`core/*.py` 内直接用（已是包）；`tools/*.py` 内若紧邻已有 `sys.path.insert` 则可用
- `sys.path.insert` 留作后续专项（走 A 或 B）
- **风险**：低；但门禁**仍不全绿**（`sys.path.insert` 残留）

## 四、建议顺序

1. **先做方案 C**：低风险、立刻见效、不碰执行方式，且能立刻消掉约 72 处中的大部分。
2. **再视情况做方案 A**：若你接受改造执行方式，A 是根治，门禁最终全绿。
3. 方案 B 作为不想装包、又想门禁变绿的折中。

## 五、待你裁决

- 选 A / B / C？还是先跳到 **item2（AI 对齐）** 或 **双引擎收敛**（功能项）？
- 若选 C，是否同意我把"门禁的 `sys.path.insert` 规则"同步修正为"仅禁非 `ensure_importable` 的项目根引导 + 豁免测试"（否则 C 做完门禁仍报测试违规）？

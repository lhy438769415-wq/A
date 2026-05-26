# 🔧 Brooks-AI V9.8 优化修复计划（修订版）

> **基于**: `docs/system_review_report.md` 评审报告  
> **修订日期**: 2026-05-26  
> **修订原因**: 用户反馈产品定位为"人机协作"——系统负责数据获取→标的筛选→机会推送→统计回测，**实际交易由人工操作**。原 P1（裸 except）在人机协作场景下风险大幅降低，重新排列优先级。  
> **原则**: 按对**交易决策的影响**排序 · 每项含测试验证与回退保护 · 不做无保护的修改

---

## 📋 优先级排序总览（修订版）

| 排名 | 编号 | 问题 | 对交易决策的影响 | 修订优先级 | 原优先级 | 变化 |
|:---:|:---|:---|:---|:---:|:---:|:---:|
| **P1** | E1/A2/R3 | 新策略接入 7 处修改 + 策略名判断 4 处重复 | 新策略漏配→信号遗漏→错失机会 | ★★★★★ | ★★★★☆ | ⬆️ |
| **P2** | A1/R1 | Watchlist ↔ SignalTracker 功能重叠 | 状态不同步→推送与实际不一致→误判 | ★★★★☆ | ★★★★☆ | — |
| **P3** | H5 | 无优雅退出机制（WAL 锁卡重启） | 每次强杀→重启等待→浪费时间 | ★★★★☆ | ★★★☆☆ | ⬆️ |
| **P4** | A3/R2 | DDL 双重定义 | 建表不一致→索引缺失→查询慢 | ★★★☆☆ | ★★★☆☆ | — |
| **P5** | H1 | 裸 `except: pass`（23 处） | 人机协作下~17处合理、~4处烦人、0处危险 | ★★☆☆☆ | ★★★★★ | ⬇️⬇️ |
| **P6** | H2 | 连接池无健康检查 | 使用频率低、单用户场景几乎不触发 | ★★☆☆☆ | ★★★★★ | ⬇️⬇️ |
| P7 | A7 | Signal Tracker God Function | 可读性差，不影响功能 | ★★★☆☆ | ★★★☆☆ | — |
| P8 | E2 | 映射逻辑硬编码在 scanner | 含在 P1 修复中 | ★★★☆☆ | ★★★☆☆ | — |
| P9 | E4 | 绘图逻辑与策略耦合 | 含在 P1 修复中 | ★★☆☆☆ | ★★☆☆☆ | — |
| P10 | A6 | formatter.py 引用不存在策略名 | 边缘 bug，不阻塞主流程 | ★★☆☆☆ | ★★☆☆☆ | — |
| P11 | H4/R4 | logging.basicConfig 35 处重复 | 不影响功能，日志可能覆盖 | ★★☆☆☆ | ★★☆☆☆ | — |
| P12 | R5 | sys.path.insert 38 处散布 | 不影响功能 | ★★☆☆☆ | ★★☆☆☆ | — |
| P13 | H3 | Watchlist JSON 无并发保护 | 单用户场景几乎不触发 | ★☆☆☆☆ | ★☆☆☆☆ | — |
| P14 | R7 | 死代码/废弃文件 9 个 | 不影响功能 | ★☆☆☆☆ | ★☆☆☆☆ | — |
| P15 | A4 | StrategyRegistry 与 PatternRegistry 不一致 | 不阻塞主流程 | ★☆☆☆☆ | ★☆☆☆☆ | — |
| P16 | A5 | scanner.py 承担过多映射逻辑 | 含在 P1 修复中 | ★☆☆☆☆ | ★☆☆☆☆ | — |
| P17 | E3 | 周线策略列表硬编码 | 含在 P1 修复中 | ★☆☆☆☆ | ★☆☆☆☆ | — |
| P18 | R6/R8/R9 | 遗留别名/R倍数重复/导入重复 | 不影响功能 | ★☆☆☆☆ | ★☆☆☆☆ | — |
| P19 | E5 | __init__.py 未充分利用 | 不影响功能 | ★☆☆☆☆ | ★☆☆☆☆ | — |
| P20 | H6 | GUI 仪表盘引用过期模块 | 非核心功能 | ★☆☆☆☆ | ★☆☆☆☆ | — |

### 优先级变化说明

| 变化 | 问题 | 原因 |
|:---:|:---|:---|
| ⬆️ 最高 | **P1 策略扩展性** | 人机协作下，系统最核心的价值就是**帮你筛选机会**。新策略接入 7 处修改，极易漏配→信号遗漏→你不知道有个机会被跳过了。这直接影响决策。 |
| ⬆️ | **P3 优雅退出** | 每次强杀后 WAL 锁住→重启要等→你的日常操作体验被反复打断。人机协作意味着你频繁启停系统，这个问题每天都会遇到。 |
| ⬇️⬇️ | **P5 裸 except** | 重新审查 23 处后发现：~17 处是资源清理/字体回退/AI 解析/GUI 刷新等**合理忽略**场景，~4 处是烦人但无害的日志丢失，**0 处会导致你在人机协作中做出错误决策**。从生产安全降为代码卫生。 |
| ⬇️⬇️ | **P6 连接池** | 单用户、低频使用场景下，连接池问题几乎不触发。原评估按"7×24 服务"标准衡量，实际你是**按需启动→扫完即关**。 |

---

## 🔴 Tier 1 — 决策影响级（直接影响你看到什么信号）

### P1: 新策略接入 7 处修改 + 策略名判断 4 处重复 (E1/A2/R3)

**影响范围**: 策略扩展体系，每次新增策略需改 7 处  
**对交易决策的影响**: 极易漏配→信号遗漏→错失交易机会  
**通俗解释**: 你加一个新策略，要改 7 个地方。漏改一个，那个策略的信号就**悄悄消失了**——你不会报错，只是看不到。

#### 修复步骤

```
Step 1: StrategyRegistry 增加元数据
  每个策略注册时声明:
    - display_name: str           # "MTR 突破"
    - supported_timeframes: list  # ['daily', 'weekly']
    - sl_column: str              # 'sl_price'
    - entry_column: str           # 'entry_price'
    - tp_columns: list            # ['tp1_price', 'tp2_price']
    - score_column: str           # 'mtr_score'
    - chart_annotate: callable    # 策略绘图标注方法

  修改 StrategyRegistry.register():
    _strategies[name] = {
        'class': cls,
        'metadata': cls.get_metadata()  # 每个策略类实现此方法
    }

Step 2: BaseStrategy 添加元数据接口
  class BaseStrategy(ABC):
      @classmethod
      def get_metadata(cls) -> dict:
          """每个策略类必须声明自己的元数据"""
          raise NotImplementedError

      @classmethod
      def get_signal_info(cls, df) -> dict:
          """从计算结果中提取标准化信号信息"""
          raise NotImplementedError

      @classmethod
      def annotate_chart(cls, ax, df):
          """在图表上标注策略特征"""
          pass  # 默认无标注

Step 3: 消灭 scanner.py 中的映射硬编码
  将 sl_col_map / entry / tp 映射替换为:
    strategy = StrategyRegistry.get_strategy(name)
    info = strategy.get_signal_info(df)

Step 4: 消灭 notifier.py 中的策略名判断
  将 if "MTR" in name 替换为:
    strategy = StrategyRegistry.get_strategy(name)
    strategy.annotate_chart(ax, df)

Step 5: 消灭 hunter.py 的 weekly_supported 硬编码
  替换为:
    weekly_supported = StrategyRegistry.get_strategies_by_timeframe('weekly')

Step 6: 消灭 scanner_weekly_gap.py 的 STRATEGY_COLS 硬编码
  替换为动态从 StrategyRegistry 获取

Step 7: 验证新策略接入只需 3 处修改
  ① strategies/ 新文件
  ② StrategyRegistry.register() 注册
  ③ 策略类实现 get_metadata() + get_signal_info()
```

#### 测试验证

```python
# test_strategy_registry_metadata.py

class TestStrategyRegistryMetadata:
    """验证策略注册表元数据机制"""

    def test_all_registered_strategies_have_metadata(self):
        """所有已注册策略必须提供完整元数据"""
        from core.strategy_registry import StrategyRegistry
        registry = StrategyRegistry()
        for name in registry.list_strategies():
            meta = registry.get_metadata(name)
            required_keys = ['display_name', 'supported_timeframes',
                           'sl_column', 'entry_column', 'tp_columns',
                           'score_column']
            for key in required_keys:
                assert key in meta, f"{name} 缺少元数据: {key}"

    def test_get_strategies_by_timeframe(self):
        """按时间框架筛选策略"""
        from core.strategy_registry import StrategyRegistry
        registry = StrategyRegistry()
        weekly = registry.get_strategies_by_timeframe('weekly')
        assert len(weekly) >= 0  # 根据实际注册情况

    def test_new_strategy_registration_flow(self):
        """模拟新策略接入，验证只需3步"""
        # 1. 创建策略类  2. 注册  3. 验证 metadata/get_signal_info 可用
        # 不需要修改 scanner/notifier/hunter
        pass

class TestScannerMappingRemoval:
    """验证 scanner.py 映射逻辑已下沉到策略类"""

    def test_scanner_uses_strategy_get_signal_info(self):
        """scanner 应调用 strategy.get_signal_info() 而非硬编码映射"""
        pass

class TestNotifierAnnotationRemoval:
    """验证 notifier.py 策略名判断已替换"""

    def test_notifier_uses_strategy_annotate_chart(self):
        """notifier 应调用 strategy.annotate_chart() 而非 if-elif"""
        pass
```

#### 回退保护

- **Git 策略**: `refactor/strategy-self-describing` 分支，每步一个提交
- **Step 1-2 低风险**: 只是增加接口，不改现有逻辑，几乎零风险
- **Step 3-6 逐个替换**: 每步完成后运行全量回归测试
- **兼容层**: 替换期间保留旧映射作为 fallback，新路径优先，旧路径兜底
- **最坏情况**: 逐 commit revert，从最后一个有问题的步骤开始

---

### P2: Watchlist ↔ SignalTracker 功能重叠 (A1/R1)

**影响范围**: 核心信号追踪链路，两处同步维护  
**对交易决策的影响**: 状态不同步→推送的信号状态与实际不一致→你基于过时信息操作  
**通俗解释**: 同一个信号的状态在两个地方分别记录，改了这个忘了那个→Discord 推送说"已止盈"，但系统里还显示"进行中"→你不知道该不该继续跟踪。

#### 修复步骤

```
Step 1: 依赖分析（只读，不改代码）
  - 绘制 WatchlistManager 所有调用点
  - 绘制 SignalTracker 所有调用点
  - 列出两者重叠方法清单

Step 2: 确定保留方
  - SignalTracker 是核心模块，且已有 signal_archive 表
  - WatchlistManager 的去重拦截逻辑应迁移到 SignalTracker
  - WatchlistManager 保留为 SignalTracker 的轻量外观（Facade）

Step 3: 迁移逻辑
  - WatchlistManager.update_status() → SignalTracker._update_signal_status()
  - WatchlistManager.check_duplicate() → SignalTracker._check_duplicate()
  - 保留 WatchlistManager 类但内部委托给 SignalTracker

Step 4: 删除重复代码
  - WatchlistManager 中不再有任何状态追踪逻辑
  - 所有状态由 SignalTracker 单一维护
```

#### 测试验证

```python
# test_watchlist_signal_tracker_merge.py

class TestWatchlistSignalTrackerMerge:
    """验证合并后行为一致"""

    def test_watchlist_delegates_to_signal_tracker(self):
        """WatchlistManager 的方法应委托给 SignalTracker"""
        pass

    def test_status_tracking_consistency(self):
        """通过 WatchlistManager 和 SignalTracker 更新状态，结果应一致"""
        # 先通过 WatchlistManager 更新
        # 再通过 SignalTracker 查询
        # 两者结果应完全一致
        pass

    def test_no_duplicate_state_machines(self):
        """不应存在两套独立的状态机"""
        # 验证 WatchlistManager 不再维护自己的状态字典
        pass
```

#### 回退保护

- **Git 策略**: `refactor/watchlist-merge` 分支
- **委托模式**: 保留 WatchlistManager 类作为外观，出问题时只需让 WatchlistManager 恢复自己的逻辑（单文件 revert）
- **渐进式**: 先改为委托（Step 3），运行一周验证无问题后再删重复代码（Step 4）
- **最坏情况**: revert WatchlistManager 文件即可恢复

---

## 🟠 Tier 2 — 使用效率级（影响你的日常操作效率）

### P3: 无优雅退出机制 (H5)

**影响范围**: 运行时数据安全  
**对日常效率的影响**: 每次强杀→WAL 锁未释放→重启等锁超时→反复浪费时间  
**通俗解释**: 你按 Ctrl+C 强制退出，数据库的"门锁"没解开。下次启动系统时，得等锁超时才能进门。你每天可能启停多次，每次浪费几十秒。

#### 修复步骤

```
Step 1: 定义全局 shutdown 标志
  _shutdown_requested = threading.Event()

Step 2: 注册信号处理器
  import signal
  def _graceful_shutdown(signum, frame):
      logger.info(f"收到信号 {signum}，开始优雅退出...")
      _shutdown_requested.set()

  signal.signal(signal.SIGINT, _graceful_shutdown)
  signal.signal(signal.SIGTERM, _graceful_shutdown)

Step 3: 关键循环中检查 shutdown 标志
  for stock_code in stock_list:
      if _shutdown_requested.is_set():
          logger.info("优雅退出中，跳过剩余股票")
          break
      # ... 正常扫描逻辑

Step 4: 退出前资源清理
  def _cleanup():
      # 1. 等待 DB Writer 线程处理完队列（发送 Poison Pill）
      # 2. 关闭所有数据库连接
      # 3. 刷新 WAL 日志: PRAGMA wal_checkpoint(TRUNCATE)
      # 4. 保存扫描进度（断点续扫用）
      logger.info("资源清理完成，退出")
```

#### 测试验证

```python
# test_graceful_shutdown.py

import signal
import threading

class TestGracefulShutdown:
    """验证优雅退出机制"""

    def test_shutdown_flag_set_on_sigint(self):
        """SIGINT 应设置 shutdown 标志"""
        from hunter import _shutdown_requested, _graceful_shutdown
        _shutdown_requested.clear()
        _graceful_shutdown(signal.SIGINT, None)
        assert _shutdown_requested.is_set()

    def test_scan_loop_respects_shutdown(self):
        """扫描循环应在 shutdown 标志设置后退出"""
        pass

    def test_db_wal_released_on_shutdown(self):
        """退出后 SQLite WAL 应被释放"""
        pass

    def test_cleanup_called_on_exit(self):
        """退出时应调用 _cleanup()"""
        pass
```

#### 回退保护

- **Git 策略**: `feat/graceful-shutdown` 分支
- **超时兜底**: 优雅退出等待最多 30 秒，超时则强制退出（os._exit(1)）
- **特性开关**: `GRACEFUL_SHUTDOWN=True` 在 settings.py 中，设为 False 则不注册信号处理器
- **最坏情况**: 行为与当前一致（Ctrl+C 强制退出），不会更差

---

### P4: DDL 双重定义 (A3/R2)

**影响范围**: 数据库层  
**对日常效率的影响**: 建表不一致→索引可能缺失→查询慢（尤其信号追踪面板）  
**通俗解释**: 同一张表的定义写了两次，一次比另一次多了一个索引。如果改了一个忘了另一个，查询速度可能变慢。

#### 修复步骤

```
Step 1: 对比两份 DDL
  - database.py 的 signal_archive DDL（权威版本）
  - signal_tracker.py 的 signal_archive DDL（含额外索引）
  - 确认差异仅为 idx_sa_strategy 索引

Step 2: 将缺失索引添加到 database.py 的 DDL
  - 将 idx_sa_strategy 索引补充到 database.py
  - 使 database.py 成为唯一权威来源

Step 3: 删除 signal_tracker.py 中的重复 DDL
  - 替换为 from core.database import ensure_tables 或类似函数调用
  - 确保 signal_tracker 启动时调用 database.py 的建表函数

Step 4: 添加迁移脚本
  - 对已存在的数据库，执行 ALTER TABLE 添加缺失索引
  - 放在 database.py 的 ensure_tables() 中，幂等执行
```

#### 测试验证

```python
# test_ddl_single_source.py

class TestDDLSingleSource:
    """验证 DDL 单一来源"""

    def test_signal_archive_ddl_only_in_database(self):
        """signal_archive 建表 DDL 只存在于 database.py"""
        import inspect
        from core import database, signal_tracker
        db_source = inspect.getsource(database)
        st_source = inspect.getsource(signal_tracker)
        assert 'CREATE TABLE signal_archive' not in st_source

    def test_all_indexes_created(self):
        """所有索引（包括 idx_sa_strategy）都应在 database.py 中定义"""
        from core.database import DatabaseManager
        db = DatabaseManager()
        with db.get_connection() as conn:
            indexes = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='signal_archive'"
            ).fetchall()
            index_names = [i[0] for i in indexes]
            assert 'idx_sa_strategy' in index_names

    def test_idempotent_ensure_tables(self):
        """ensure_tables() 多次调用不应出错"""
        from core.database import DatabaseManager
        db = DatabaseManager()
        db.ensure_tables()
        db.ensure_tables()
```

#### 回退保护

- **Git 策略**: `fix/ddl-single-source` 分支
- **幂等迁移**: ensure_tables() 使用 `CREATE INDEX IF NOT EXISTS`，多次执行安全
- **最坏情况**: signal_tracker.py 中的 DDL 只是冗余不是错误，删除后如有问题可立即恢复

---

## 🟡 Tier 3 — 代码卫生级（不影响决策和效率，但应该清理）

### P5: 裸 `except: pass` 23 处 (H1) — 降级说明

**原评估**: ★★★★★ 生产安全级  
**修订评估**: ★★☆☆☆ 代码卫生级  

**降级理由**: 逐处审查 23 处裸 except 后，按人机协作场景分类：

| 类别 | 数量 | 典型场景 | 人机协作下实际风险 |
|:---|:---:|:---|:---|
| 合理忽略 | ~17 | 资源清理(font.close)、字体回退、AI 响应解析容错、GUI 刷新 | ✅ 无风险，这些异常确实不需要你感知 |
| 烦人但不危险 | ~4 | 价格获取失败返回 None、网络超时静默跳过 | ⚠️ 可能遗漏个别信号，但系统其他策略仍在推送 |
| 危险 | **0** | — | ❌ 没有会导致错误决策的裸 except |

**修复建议**: 改为"**精准标注**"而非"**全面替换**"：
- ~17 处：改为 `except (ValueError, KeyError):` + 注释说明为什么忽略
- ~4 处：改为 `except Exception as e: logger.warning(f"...")`  
- 不再单独设立 Tier，随其他修复一起顺手改

#### 修复步骤

```
Step 1: 全量扫描定位
  grep -rn "except\s*:" --include="*.py" | grep -v ".venv"
  建立清单: 文件 → 行号 → 当前逻辑 → 修复方案

Step 2: 逐处分类替换
  类型A — 合理忽略（~17处）:
    except:           →  except (ValueError, KeyError):  # [合理忽略] 字体回退/资源清理

  类型B — 需要感知（~4处）:
    except:           →  except Exception as e:
                           logger.warning(f"非致命异常: {e}")

Step 3: 无需类型C/D（无人机协作下无危险场景）
```

#### 测试验证

```python
# test_exception_handling.py

class TestBareExceptReplacement:
    """验证裸 except 替换后行为正确"""

    def test_no_bare_except_remains(self):
        """项目中不应再存在裸 except:"""
        import subprocess
        result = subprocess.run(
            ['grep', '-rn', r'except\s*:', '--include=*.py', '.'],
            capture_output=True, text=True
        )
        # 仅允许测试文件中出现裸 except
        lines = [l for l in result.stdout.splitlines() if 'test_' not in l]
        assert len(lines) == 0, f"仍有裸 except: {lines}"

    def test_business_exception_still_handled(self):
        """业务级异常仍被正确捕获和处理"""
        pass
```

#### 回退保护

- **Git 策略**: 在其他修复分支中顺带提交，不单独建分支
- **最坏情况**: 裸 except 虽然不好但不会比现在更差

---

### P6: 连接池无健康检查与泄漏检测 (H2) — 降级说明

**原评估**: ★★★★★ 生产安全级  
**修订评估**: ★★☆☆☆ 代码卫生级  

**降级理由**: 
- 单用户、按需启动→扫完即关的使用模式，连接池几乎不存在泄漏场景
- 当前系统使用频率远低于 7×24 服务标准
- 如未来升级为定时自动扫描，此优先级应重新上调

**修复建议**: 添加基本健康检查即可，无需复杂的泄漏检测后台线程

#### 修复步骤

```
Step 1: 添加连接健康检查（轻量版）
  - get_connection() 取出连接时先执行 SELECT 1
  - 若失败则丢弃该连接，创建新连接
  - 添加 _validate_conn(conn) → bool 私有方法

Step 2: 上下文管理器增强
  - 确保所有 get_connection() 调用都通过 with 语句
  - __exit__ 中添加异常感知: 出异常时连接标记为不健康
```

#### 测试验证

```python
# test_connection_pool.py

class TestConnectionPool:
    """验证连接池基本健康检查"""

    def test_invalid_connection_replaced(self):
        """取出坏连接时应自动替换"""
        from core.database import DatabaseManager
        db = DatabaseManager()
        bad_conn = MagicMock()
        bad_conn.execute.side_effect = sqlite3.OperationalError("closed")
        db._pool.put(bad_conn)
        conn = db.get_connection()
        assert conn is not bad_conn

    def test_exception_marks_connection_unhealthy(self):
        """使用中出异常的连接归还时应标记不健康"""
        pass
```

#### 回退保护

- **Git 策略**: 在其他修复分支中顺带提交
- **特性开关**: `DB_POOL_HEALTHCHECK=True`，出问题时设为 False
- **最坏情况**: 回退到当前行为，单用户场景下风险极低

---

## 🟢 Tier 4 — 建议清理级（有空再做）

### P7: Signal Tracker God Function (A7)

**影响范围**: signal_tracker.py 两个 200+ 行函数  
**修复策略**: 拆分为 查询/计算/格式化/推送 四个独立函数

```
run_tracker_dashboard()  →  _query_dashboard_data()
                          + _compute_dashboard_metrics()
                          + _format_dashboard_report()
                          + run_tracker_dashboard()  # 编排层

_push_dashboard_discord() → _format_discord_message()
                           + _prepare_discord_attachments()
                           + _send_discord_message()
                           + _push_dashboard_discord()  # 编排层
```

**测试**: 每个拆分函数独立测试，编排层只验证调用顺序  
**回退**: 保留原函数作为编排入口，拆分是纯内部重构

### P8: 映射逻辑硬编码在 scanner (E2)

**影响范围**: scanner.py 130 行映射逻辑  
**修复策略**: 下沉到策略类的 `get_signal_info(df)` 方法（已包含在 P1 Step 3 中）  
**依赖**: P1（StrategyRegistry 元数据机制）

### P9: 绘图逻辑与策略耦合 (E4)

**影响范围**: notifier.py 200 行绘图逻辑  
**修复策略**: 下沉到策略类的 `annotate_chart(ax, df)` 方法（已包含在 P1 Step 4 中）  
**依赖**: P1（StrategyRegistry 元数据机制）

### P10: formatter.py 引用不存在策略名 (A6)

**影响范围**: formatter.py 第 114 行  
**修复策略**: `StrategyRegistry.get_strategy("HUNTER_V1")` → 通过 StrategyRegistry 获取正确策略名  
**测试**: 添加断言验证 StrategyRegistry.get_strategy() 返回有效策略  
**回退**: 单行修改，即刻 revert

### P11: logging.basicConfig 35 处重复 (H4/R4)

**影响范围**: 全局日志系统  
**修复策略**:
1. 删除所有模块级 `logging.basicConfig()`
2. 仅在 hunter.py 主入口配置一次
3. 其他模块使用 `logger = logging.getLogger(__name__)` 获取 logger
4. 添加 `logging_helpers.py` 统一配置函数

**测试**: 验证删除后日志仍然正常输出到文件和控制台  
**回退**: 逐文件删除，每删一个文件运行一次测试

### P12: sys.path.insert 38 处散布 (R5)

**影响范围**: 全项目导入体系  
**修复策略**: 创建 `pyproject.toml`:
```toml
[tool.pytest.ini_options]
pythonpath = ["."]

[tool.setuptools]
# 或使用 [tool.ruff] 等配置
```
删除所有 `sys.path.insert(0, ...)` 行  
**测试**: 删除后所有导入仍正常工作  
**回退**: pyproject.toml 删除即可，sys.path.insert 虽丑但功能正常

### P13: Watchlist JSON 无并发保护 (H3)

**影响范围**: watchlist.py  
**修复策略**: 使用 `filelock` 库或 `fcntl/msvcrt` 文件锁  
**优先级低**: 单用户场景下几乎不会触发

### P14: 死代码/废弃文件 9 个 (R7)

**影响范围**: ~500 行  
**修复策略**: 删除以下文件:
- `core/signal_db.py` — 旧版 JSON 信号去重
- `core/backtest.py` — 49 行 Placeholder
- `tools/data_manager.py` — 薄代理层
- `tools/scan_three_k.py` — 旧版 3K 扫描器
- `tools/scan_v34.py` — V34 版本扫描器
- `tools/test_signals.py` — 简易测试脚本
- `tools/test_weekly_history.py` — 手动测试脚本
- `tools/read_stats.py` — 一次性脚本

**测试**: 删除后运行全量回归测试，确认无导入依赖  
**回退**: git revert 即可恢复

---

## 📊 执行时间线建议（修订版）

```
Week 1:  P1(策略自描述 Step 1-4) + P4(DDL单一来源) + P14(删废弃文件)
         ↓ 全量回归测试 ↓
Week 2:  P1(策略自描述 Step 5-7) + P2(Watchlist合并 Step 1-2)
         ↓ 全量回归测试 ↓
Week 3:  P2(Watchlist合并 Step 3-4) + P3(优雅退出)
         ↓ 全量回归测试 ↓
Week 4:  P5(裸except精准标注) + P6(连接池轻量健康检查) + P7(God Function拆分)
         ↓ 全量回归测试 ↓
Week 5:  P8/P9(随P1已完成) + P11(日志) + P12(sys.path) + P10(formatter bug)
         ↓ 全量回归测试 ↓
```

**关键变化**:
- 原 Week 1 的 P1(裸except)+P2(连接池) 推迟到 Week 4，且简化为轻量修复
- 策略扩展性(P1)提到 Week 1-2 优先解决，因为这是**你能否看到完整信号**的关键
- 优雅退出(P3)提到 Week 3，改善日常启停体验

---

## 🛡️ 通用回退保护策略

### Git 分支管理

```
main (稳定版)
  ├── refactor/strategy-self-describing (P1) ← 最高优先
  ├── refactor/watchlist-merge        (P2)
  ├── feat/graceful-shutdown          (P3)
  ├── fix/ddl-single-source           (P4)
  └── refactor/quality-improvements   (P5-P14)
```

- 每个修复项在独立分支开发
- 每个分支内的每一步操作独立提交（原子提交）
- 合并前必须通过全量回归测试
- 出问题时 `git revert <commit>` 或 `git checkout main`

### 全量回归测试命令

```bash
# 每次修改后执行
cd "D:/life/Trading view/_Project_A/Data_from_Akshare/debugV7.1_for_antigravity"
python -m pytest tests/ -v --tb=short

# 冒烟测试（核心扫描流程）
python hunter.py --no-ai  # 确保扫描流程正常运行

# 数据库完整性检查
python -c "from core.database import DatabaseManager; db = DatabaseManager(); db.validate_integrity()"
```

### 安全网清单

| 安全网 | 实施方式 |
|:---|:---|
| 原子提交 | 每步修改一个独立提交，方便精准 revert |
| 特性开关 | 关键改动在 settings.py 添加开关，一键回退 |
| 兼容层 | 新旧路径并存期间，新路径优先、旧路径兜底 |
| 增量部署 | 每完成一个 Tier 的所有项，合并一次到 main |
| 冒烟测试 | 每次合并前运行 `hunter.py --no-ai` 验证核心流程 |
| 数据库备份 | 修改 DDL 前备份 SQLite 数据库文件 |

---

## 📝 修订记录

| 日期 | 修订内容 |
|:---|:---|
| 2026-05-26 | 初始版本（按技术风险排序） |
| 2026-05-26 | 修订版：基于人机协作定位重新排列优先级。P1(策略扩展性)⬆️最高，P5(裸except)⬇️⬇️降为代码卫生，P6(连接池)⬇️⬇️降为代码卫生，P3(优雅退出)⬆️提升 |

---

*本计划为只读评审的后续行动指南，实际执行前请在对应分支验证。*

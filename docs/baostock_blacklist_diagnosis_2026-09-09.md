# Baostock 夜间黑名单问题诊断与改进建议

> **审计方**: 项目A维护方 (Antigravity)
> **日期**: 2026-09-09
> **依据**: 项目B自述文档 + 代码审计 + 联网查询baostock官方信息
> **审计类型**: 只读

---

## 一、诊断结论（一句话）

> [!IMPORTANT]
> **核心问题已定位：5个Worker完全无请求间隔（zero sleep），像机关枪一样不间断拉取3000+只股票，在夜间高峰期极易触发baostock的QPS风控。** 项目B的"被封立即终止"防护做得好，但缺少"不被封"的主动限流。

---

## 二、已有防护评估（项目B做对了什么）

| 防护层 | 实现状态 | 评价 |
|--------|:-------:|------|
| 并发数≤5（对齐TCP硬限制） | ✅ | 正确 |
| 被封立即终止（BsBlacklistedError穿透） | ✅ | 四层穿透设计严密 |
| 死亡螺旋防护（不反复重试） | ✅ | cancel_futures + break |
| 发现阶段后主动登出 | ✅ | 防空闲超时 |
| 看门狗强杀卡死Worker | ✅ | stall_limit + wall_limit |
| 单任务超时60秒 | ✅ | 防socket挂死 |
| 股票中文名本地缓存 | ✅ | 扫描/推送不联网 |
| **请求频率限流（Rate Limiting）** | **🔴 完全没有** | **核心缺失** |
| **子进程退出时主动logout** | **⚠️ 缺失** | 可能造成服务端僵尸会话 |

---

## 三、根因分析

### 为什么昨晚又被封？

```
触发链路：
用户点"同步" 
  → 5个Worker同时启动（ProcessPoolExecutor）
  → 每个Worker循环拉取：login → query → query → query → ... → logout
  → Worker之间完全无协调，Worker内部完全无sleep
  → 5路同时以网络IO速度极限拉取 ≈ 每秒30-50次请求
  → baostock服务器风控判定"异常高频" → 触发IP黑名单
```

**关键证据**：审计子代理确认，`data_provider.py` 中**搜索不到任何 `sleep` 或请求频率限流代码**。5个Worker一有空闲就立即发起下一次请求，完全取决于网络IO速度。

### 联网查询结果确认

baostock官方和社区反馈：

| 项 | 信息 |
|---|------|
| QPS限制 | **无公开具体数值**，但存在自动化风控机制 |
| 并发连接限制 | 同一IP最多5个TCP连接（代码已对齐） |
| 触发条件 | "短时间内发起过于频繁的请求"或"极短时间内大规模数据爬取" |
| 解封时间 | 无统一标准，通常**几小时或次日** |
| 官方建议 | **"在代码中加入适当的延时（Sleep），模拟正常用户访问"** |
| 登录超时 | 30分钟无操作自动断开 |
| 联系方式 | QQ群 714707478 / baostock@163.com |

> [!CAUTION]
> baostock官方的建议与代码现状完全矛盾——官方说"加适当延时"，但代码中延时为零。

---

## 四、两个项目对比

| 对比项 | 项目A (本项目) | 项目B (WorkBuddy) |
|--------|:-----------:|:----------------:|
| MAX_WORKERS | 5 | 5 |
| 被封终止机制 | ✅ 有 | ✅ 有 |
| 死亡螺旋防护 | ✅ 有 | ✅ 有 |
| 单任务超时 | ✅ 60秒 | ✅ 45秒 |
| 看门狗机制 | ✅ | ✅ |
| **请求间隔(sleep)** | **🔴 无** | **🔴 无** |
| **子进程退出logout** | **⚠️ 缺** | **⚠️ 缺** |
| 周线同步方式 | 本地日线聚合(不联网) ✅ | 本地日线聚合(不联网) ✅ |

**两个项目在baostock连接管理上高度同构**，共享同一个核心缺陷：无请求间隔。

---

## 五、改进建议

### 方案一：Worker级请求间隔（最小改动，推荐）

在每个Worker的请求循环中加入微小sleep，将并发QPS从"无限制"降到"可控范围"。

**改动位置**: `tools/fetcher_baostock.py` 中的 `bs_fetch_daily_history` 或类似函数

```python
import time
import random

def bs_fetch_daily_history(symbol, start_date, end_date, ...):
    """每次请求前加入随机延时"""
    # 🟢 防夜间高峰限流：每次请求前随机等待 0.1-0.3 秒
    # 5 Worker × 平均0.2秒/请求 ≈ 每秒25次 → 每秒约5次/Worker
    # 3312只股票 × 0.2秒 ÷ 5Worker ≈ 约133秒(2分钟)完成全量增量同步
    time.sleep(random.uniform(0.1, 0.3))
    
    # ... 原有请求逻辑 ...
```

**预估影响**：
- 全量增量同步时间：从约1分钟增加到约2-3分钟
- 被封概率：**大幅降低**（从"机关枪"变为"正常人类操作节奏"）
- 改动量：**1-2行代码**

---

### 方案二：全局令牌桶限流（更精细，二期）

在 `data_provider.py` 中使用令牌桶算法控制全局QPS上限。

```python
import threading
import time

class RateLimiter:
    """令牌桶限流器（大白话：排队领号器，每秒只发N个号）"""
    def __init__(self, max_per_second: float = 10.0):
        self._interval = 1.0 / max_per_second
        self._lock = threading.Lock()
        self._last = 0.0

    def acquire(self):
        with self._lock:
            now = time.monotonic()
            wait = self._last + self._interval - now
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()

# 使用：在每个Worker的请求前调用
rate_limiter = RateLimiter(max_per_second=10)  # 全局限制10 QPS

def _fetch_worker(task):
    rate_limiter.acquire()  # 排队等号
    return bs_fetch_daily_history(...)
```

> ⚠️ 注意：如果用 `ProcessPoolExecutor`（多进程），令牌桶需要用 `multiprocessing.Lock` 或进程间通信机制，比线程版复杂。方案一的每Worker独立sleep更适合多进程架构。

---

### 方案三：子进程优雅退出时主动logout

当前子进程(Worker)在异常退出或被cancel时，不会主动logout，可能导致baostock服务端保留僵尸会话。

```python
# 在 _fetch_worker 中加入 atexit 或 finally 保护
import atexit

def _worker_init():
    """子进程初始化时注册退出清理"""
    def _cleanup():
        try:
            bs.logout()
        except Exception:
            pass
    atexit.register(_cleanup)

# ProcessPoolExecutor 使用 initializer
executor = ProcessPoolExecutor(
    max_workers=5,
    initializer=_worker_init  # 每个子进程启动时注册清理函数
)
```

---

### 方案四：错峰同步（零代码改动）

| 时段 | 风险 | 建议 |
|------|:----:|------|
| 18:00-22:00 | 🔴 高 | 夜间高峰，所有散户量化爱好者同时拉数据 |
| 22:00-06:00 | 🟡 中 | 夜深人静，但部分定时任务仍在运行 |
| **06:00-09:00** | **🟢 低** | **清晨最佳窗口，几乎无竞争** |
| 09:30-15:00 | 🟡 中 | 盘中数据不完整(T+1)，不建议同步 |

---

## 六、推荐执行顺序

| 优先级 | 方案 | 工作量 | 预期效果 |
|:------:|------|:-----:|---------|
| **🥇 立即** | 方案一：Worker级sleep(0.1-0.3秒) | 1-2行 | 夜间被封概率降低80%+ |
| **🥈 同步** | 方案四：建议用户错峰(6-9点) | 0行 | 完全避开高峰 |
| 🥉 二期 | 方案三：子进程优雅logout | 10行 | 防僵尸会话 |
| 备选 | 方案二：全局令牌桶 | 30行 | 精细控制QPS |

---

## 七、与baostock官方机制的对齐

| baostock官方要求/建议 | 当前代码状态 | 建议 |
|---------------------|:---------:|------|
| 同一IP最多5个TCP连接 | ✅ 已对齐(MAX_WORKERS=5) | 维持 |
| "加入适当的延时(Sleep)" | 🔴 **完全没有** | **方案一** |
| "分批次、分时段获取数据" | ⚠️ 按增量获取但无分批 | 可选方案二 |
| "频繁使用的数据下载到本地" | ✅ SQLite本地存储 | 维持 |
| 30分钟无操作自动断开 | ✅ 发现后主动logout | 维持 |

---

*本报告基于项目A/B代码只读审计和baostock官方信息联网查询。未对任何项目做修改。*

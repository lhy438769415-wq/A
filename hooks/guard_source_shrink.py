#!/usr/bin/env python3
"""源码收缩守卫 — 防意外截断的最后一道门。

在每次 git commit 时运行（由 hooks/pre-commit 调用）：
- 若某个已暂存的 .py 源码文件为空，或相对 HEAD 行数跌到 < 50%，
  阻断提交。这样「被清空/截断的文件」在写入 git 对象库之前就会被拦下，
  永远不会污染历史、也不会因 gc 而丢失。

首次提交（无 HEAD）时自动跳过（没有基线可比）。
仅用标准库，无第三方依赖。
"""
import subprocess
import sys

MIN_RATIO = 0.5


def _git(*args):
    return subprocess.run(["git"] + list(args), capture_output=True, text=True)


def main():
    # 无 HEAD -> 首次提交，无基线可比对，跳过
    head = _git("rev-parse", "--verify", "HEAD")
    if head.returncode != 0:
        return 0

    # 已暂存（新增/修改/重命名）的 .py 文件，排除已删除
    out = _git("diff", "--cached", "--name-only", "--diff-filter=ACM", "--", "*.py")
    if out.returncode != 0:
        return 0
    files = [f for f in out.stdout.splitlines() if f.endswith(".py")]
    if not files:
        return 0

    blocked = []
    for f in files:
        staged = _git("show", ":{}".format(f))          # 暂存区版本
        if staged.returncode != 0:
            continue
        new_lines = staged.stdout.count("\n")

        head_out = _git("show", "HEAD:{}".format(f))     # HEAD 版本
        if head_out.returncode != 0:
            continue  # 全新文件，无基线
        old_lines = head_out.stdout.count("\n")
        if old_lines == 0:
            continue

        if new_lines == 0 or new_lines < old_lines * MIN_RATIO:
            pct = (new_lines / old_lines * 100) if old_lines else 0
            blocked.append((f, old_lines, new_lines, pct))

    if blocked:
        print("🚫 提交被「源码收缩守卫」阻断：检测到文件相对 HEAD 异常缩小（疑似被截断/清空）")
        for f, old, new, pct in blocked:
            print("   - {} : HEAD={} 行 -> 暂存={} 行 ({:.0f}%)".format(f, old, new, pct))
        print("   请先确认该文件是否真的需要如此大幅改动；若确属正常重构，请人工复核后：git commit --no-verify")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

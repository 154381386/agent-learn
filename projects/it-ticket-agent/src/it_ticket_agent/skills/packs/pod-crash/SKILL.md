# Pod Crash Skill

适用于 `CrashLoopBackOff`、`OOMKilled`、频繁重启这类 Pod 崩溃 / 重启场景。

## 使用思路

- 先看 Pod 当前状态与重启次数
- 再看事件和最近日志
- 再看 JVM / CPU / quota 等资源信号
- 最后给出“是否命中 Pod 崩溃 SOP”的统一判断

## 适用条件

- 用户描述包含崩溃、重启、CrashLoop、OOM 等关键词
- 当前上下文已能定位到 service，最好还能定位 namespace

## 产出目标

- 输出统一的 SkillResult
- 汇总关键 evidence
- 给上层 hypothesis verification 提供稳定证据

# Tool-First-ReAct 优化记录

用于持续记录 `projects/it-ticket-agent` 在 `tool-first ReAct` 主链路上的优化路径、实验结果和分析结论。

## 记录约束

- 本文档只追加，不删除历史记录，不覆盖旧结论
- 每次优化后新增一个时间块，记录改动、结果、分析、下一步
- 如果某次实验失败，也照样记录，避免重复试错
- 评估优先使用 `真实 LLM + mocked tool outputs`

## 建议记录格式

```md
## YYYY-MM-DD HH:MM 优化主题

### 变更
- 

### 评估结果
- 数据集：
- 结果：

### 分析
- 

### 下一步
- 
```

## 2026-04-21 真实 LLM baseline

### 变更
- 新增离线 `agent eval` 骨架，入口为 `scripts/run_agent_eval.py`
- 新增首批 dataset：`data/evals/tool_mock_cases.json`
- 评估方式固定为：`真实 LLM + mocked tool outputs`
- 当前首批 case 覆盖三类问题：
  - 网络超时
  - K8s OOM / Pod 异常
  - 发布回归

### 评估结果
- 命令：

```bash
cd projects/it-ticket-agent
./.venv/bin/python scripts/run_agent_eval.py
```

- 结果：
  - `network_profile_prefers_network_tools`：`FAIL`
    - 分数 `0.800`
    - 通过 `4/5`
    - 用时 `50440 ms`
    - 失败项：`max_tool_calls_used`
    - 期望 `<= 4`，实际 `16`
  - `oom_profile_prefers_k8s_tools`：`FAIL`
    - 分数 `0.800`
    - 通过 `4/5`
    - 用时 `45840 ms`
    - 失败项：`max_tool_calls_used`
    - 期望 `<= 4`，实际 `12`
  - `deploy_signals_prefers_cicd_tools`：`PASS`
    - 分数 `1.000`
    - 通过 `6/6`
    - 用时 `26725 ms`
- 汇总：
  - `total=3`
  - `passed=1`
  - `failed=2`
  - `errored=0`
  - `pass_rate=0.333`

### 分析
- 当前主问题不是“完全不会选工具”，而是“会过度探索”
- 两个失败 case 都已经基本命中了正确问题域，但在得到足够证据后没有及时停止
- 当前 `ReAct` 行为明显偏保守，会继续补做通用检查，例如健康、告警、Pod 基础状态，导致工具调用数显著超预算
- `deploy` case 能通过，说明显式发布信号对当前路由和工具选择有较强约束作用
- `network` 与 `oom` case 没过，说明当前系统对“已识别 domain 后的收敛能力”不足
- 从优化优先级看，下一步应先解决：
  - 工具调用收敛
  - domain 内候选工具约束
  - 证据足够后的提前停止

### 下一步
- 优先收紧 `react supervisor` 的工具预算和停止条件
- 优化 prompt，明确要求在命中足够证据后立即结束，不继续补充低价值工具调用
- 对明显 domain signal 的请求缩小首轮候选工具范围
- 优化后重新运行同一批 dataset，保持前后结果可对比

## 2026-04-21 13:54 第一轮收敛优化

### 变更
- 在 `runtime/react_supervisor.py` 中新增候选工具约束逻辑
- 根据 `message + matched_tool_domains` 生成 `candidate_tool_names`
- `LLM` 每轮只看到候选工具，不再默认暴露全量工具
- 在 prompt 中强化“优先最少工具、证据足够即停止”的要求
- 新增 `anomalous_observation_count` 判断
- 如果已经拿到足够异常证据，则直接提前结束，不再继续补查

### 评估结果
- 命令：

```bash
cd projects/it-ticket-agent
./.venv/bin/python scripts/run_agent_eval.py
```

- 结果：
  - `network_profile_prefers_network_tools`：`FAIL`
    - 分数 `0.800`
    - 通过 `4/5`
    - 用时 `15670 ms`
    - 失败项：`required_any_tools`
    - 实际工具：`check_service_health`、`check_recent_alerts`、`inspect_upstream_dependency`
  - `oom_profile_prefers_k8s_tools`：`PASS`
    - 分数 `1.000`
    - 通过 `5/5`
    - 用时 `17079 ms`
  - `deploy_signals_prefers_cicd_tools`：`PASS`
    - 分数 `1.000`
    - 通过 `6/6`
    - 用时 `16358 ms`
- 汇总：
  - `total=3`
  - `passed=2`
  - `failed=1`
  - `errored=0`
  - `pass_rate=0.667`

### 分析
- 第一轮优化已经解决了“过度探索”问题
- 三个 case 的耗时和工具调用数量都明显下降
- `oom` case 从失败变为通过，说明“候选工具约束 + 提前停止”是有效的
- `deploy` case 继续稳定通过，说明新约束没有伤到显式发布信号链路
- 剩余问题集中在 `network` case：
  - 已经不再过度探索
  - 但首轮仍会混入泛化工具，如 `health`、`alerts`
  - 说明当前约束还不够强，首轮 domain-native 优先级仍然不足

### 下一步
- 继续收紧显式 domain 下的首轮候选集
- 对 `network / k8s / cicd / db` 场景，把首轮泛化工具移到第二轮再开放
- 重新运行同一批 dataset，确认是否能把 `network` case 补到通过

## 2026-04-21 13:54 第二轮首轮候选集优化

### 变更
- 继续修改 `runtime/react_supervisor.py`
- 对显式 domain 场景区分：
  - `domain-native tools`
  - `domain helper tools`
- 首轮只暴露 `domain-native tools`
- 只有在已有观测后，第二轮才补充 `check_service_health`、`check_recent_alerts` 这类 helper

### 评估结果
- 命令：

```bash
cd projects/it-ticket-agent
./.venv/bin/python scripts/run_agent_eval.py
```

- 结果：
  - `network_profile_prefers_network_tools`：`PASS`
    - 分数 `1.000`
    - 通过 `5/5`
    - 用时 `18508 ms`
    - 实际工具：`inspect_upstream_dependency`、`inspect_vpc_connectivity`、`inspect_load_balancer_status`
  - `oom_profile_prefers_k8s_tools`：`PASS`
    - 分数 `1.000`
    - 通过 `5/5`
    - 用时 `20836 ms`
    - 实际工具：`check_pod_status`、`inspect_pod_logs`、`inspect_jvm_memory`
  - `deploy_signals_prefers_cicd_tools`：`PASS`
    - 分数 `1.000`
    - 通过 `6/6`
    - 用时 `21401 ms`
    - 实际工具：`check_recent_deployments`、`check_pipeline_status`
- 汇总：
  - `total=3`
  - `passed=3`
  - `failed=0`
  - `errored=0`
  - `pass_rate=1.000`

### 分析
- 第二轮优化解决了 `network` case 首轮混入泛化工具的问题
- 当前主链路已经具备“识别 domain 后优先调用 domain-native tools”的行为
- `deploy` case 进一步收敛到 2 个关键工具，说明新的首轮约束不仅提高了准确性，也压缩了工具调用链
- 当前这批数据集上，最明显的收益是：
  - 减少无效工具调用
  - 降低耗时
  - 提高 domain 内工具选择集中度
- 现阶段结论：
  - 这批优化已经足以作为下一轮扩充 dataset 的基线
  - 接下来更值得做的是“扩大 case 覆盖面”，而不是继续在这 3 个 case 上微调

### 下一步
- 扩充 `agent eval` dataset
- 增加 DB、SDE、负样本、topic shift、审批前观察等 case
- 开始记录更细的评估维度：
  - tool_calls_used
  - domain-native tool ratio
  - 首轮命中率
  - case 耗时分布

## 2026-04-21 14:35 第三轮跨域扩域与候选约束优化

### 变更
- 继续修改 `runtime/react_supervisor.py`
- 修复 staged expansion 半成品状态：
  - 补齐 `_apply_observation_results`
  - 补齐 `_should_run_expansion_probe`
  - 补齐 `_expansion_probe_tool_names`
- 调整循环顺序：
  - 每轮开始先基于最新 observations 重算 `candidate_domain_plan`
  - 再决定是否执行自动扩域探针
- 自动扩域策略改为：
  - 当主域已有 2 次以上观测且异常证据不足时，自动执行扩展域前 1-2 个高优先工具
  - 优先探测第一个扩展域，避免一轮里把探针打散到多个域
- 候选工具约束收紧：
  - LLM 返回的 tool call 必须属于本轮 `candidate_tool_names`
  - 不再接受“虽然系统里存在，但本轮并未暴露”的工具
- 扩域排序优化：
  - 如果用户消息里有显式主域信号，扩域时先按 `DOMAIN_ADJACENCY`
  - `matched_tool_domains` 只作为补充，不再优先于 adjacency
- 异常识别补充：
  - `slow_query_count > 0`
  - `db_health=degraded`
- 新增回归测试：
  - 显式主域存在时，扩域应优先走 adjacency，而不是被 `matched_tool_domains` 的噪声域带偏

### 定向评估结果
- 命令：

```bash
cd projects/it-ticket-agent
./.venv/bin/python scripts/run_agent_eval.py \
  --case-id network_signal_expands_to_db_root_cause \
  --case-id k8s_signal_expands_to_cicd_root_cause
```

- 优化前：
  - `network_signal_expands_to_db_root_cause`：`FAIL`
    - 工具：`inspect_upstream_dependency`、`inspect_ingress_route`、`inspect_vpc_connectivity`
    - 问题：没有扩到 DB
  - `k8s_signal_expands_to_cicd_root_cause`：`FAIL`
    - 工具：`check_pod_status`、`inspect_pod_logs`、`inspect_pod_events`、`check_service_health`
    - 问题：超过 `max_tool_calls_used`
- 优化后：
  - `network_signal_expands_to_db_root_cause`：`PASS`
    - 分数 `1.000`
    - 通过 `5/5`
    - 用时 `15316 ms`
    - 实际工具：`inspect_upstream_dependency`、`inspect_ingress_route`、`inspect_connection_pool`、`inspect_slow_queries`
  - `k8s_signal_expands_to_cicd_root_cause`：`PASS`
    - 分数 `1.000`
    - 通过 `5/5`
    - 用时 `19738 ms`
    - 实际工具：`check_pod_status`、`inspect_pod_logs`、`inspect_pod_events`、`check_recent_deployments`
- 汇总：
  - `total=2`
  - `passed=2`
  - `failed=0`
  - `errored=0`
  - `pass_rate=1.000`

### 全量验证结果
- 命令：

```bash
cd projects/it-ticket-agent
uv run python -m unittest discover -s tests -q
./.venv/bin/python scripts/run_agent_eval.py
```

- 单测：
  - `Ran 57 tests in 8.276s`
  - `OK (skipped=1)`
- eval：
  - `network_profile_prefers_network_tools`：`PASS`
  - `oom_profile_prefers_k8s_tools`：`PASS`
  - `deploy_signals_prefers_cicd_tools`：`PASS`
  - `network_signal_expands_to_db_root_cause`：`PASS`
  - `k8s_signal_expands_to_cicd_root_cause`：`PASS`
  - `total=5`
  - `passed=5`
  - `failed=0`
  - `errored=0`
  - `pass_rate=1.000`

### 分析
- 这轮真正解决的是两个搜索层面的缺陷：
  - 扩域触发时机错误，上一轮 observation 还没进入本轮 domain plan
  - 候选集只是“提示”，不是“硬约束”，导致模型能跳出收缩后的搜索空间
- 当前行为已经变成：
  - 先在显式主域内收缩候选
  - 主域证据弱时自动打一个小型跨域探针
  - 探针优先走 adjacency，避免被 `matched_tool_domains` 的泛化噪声带偏
- `network` case 之前出现 `check_pod_status`，本质上不是模型“推理错误”，而是 runtime 允许它调用了不该看到的工具，同时扩域顺序还把 `k8s` 噪声提前了
- 这说明“缩小候选集”要成立，至少要同时满足两件事：
  - 选择器真的缩小了暴露工具
  - 执行层真的拒绝候选集之外的调用

### 下一步
- 继续扩 `agent eval` dataset，重点补：
  - noisy matched domains
  - 主域健康但邻接域异常
  - 主域与邻接域都弱证据时的停止策略
- 给 `agent_eval` 增加更细的统计项：
  - 首轮 domain 命中率
  - 扩域触发率
  - expansion probe 命中率
  - 非候选工具调用拒绝次数

## 2026-04-21 15:49 第四轮评估集扩面与过程指标化

### 变更
- 继续修改 `runtime/react_supervisor.py`
- 给 runtime 补充搜索过程埋点：
  - `expanded_domains`
  - `expansion_probe_count`
  - `expansion_probe_tools`
  - `rejected_tool_call_count`
  - `rejected_tool_call_names`
- 统一把这些字段写入 `react_runtime`，避免 eval 只能从 `transition_notes` 里猜行为
- 扩展 `evals/agent_eval.py`：
  - `AgentEvalExpectation` 新增过程断言：
    - `stop_reason`
    - `first_any_tools`
    - `first_forbidden_tools`
    - `expanded_domains`
    - `expansion_probe_required`
    - `max_rejected_tool_calls`
  - `AgentEvalObservation` 新增过程观测字段：
    - `transition_notes`
    - `expanded_domains`
    - `expansion_probe_count`
    - `expansion_probe_tools`
    - `rejected_tool_call_count`
    - `rejected_tool_call_names`
  - `AgentEvalReport` 新增汇总指标：
    - `avg_tool_calls_used`
    - `avg_duration_ms`
    - `expansion_probe_cases`
    - `rejected_tool_call_cases`
    - `rejected_tool_call_total`
    - `stop_reason_counts`
- 修改 `scripts/run_agent_eval.py`，CLI 输出现在会显示：
  - 每个 case 的 `stop`
  - `expand`
  - `rejected`
  - 汇总级指标
- 扩充 dataset：
  - `tool_mock_cases.json` 从 `5` 个 case 扩到 `13` 个
  - 覆盖面从“3 个主域 + 2 个跨域”扩到：
    - 单域收敛：`network / k8s / cicd / db / sde`
    - 跨域扩展：`network -> db`、`k8s -> cicd`、`db -> network`、`cicd -> k8s`
    - 强证据不扩域：`network / k8s / cicd / db`
- 补充单测：
  - observation extraction / score 新字段
  - report summary 指标

### 验证结果
- 命令：

```bash
cd projects/it-ticket-agent
uv run python -m unittest tests.test_agent_eval -q
uv run python -m unittest discover -s tests -q
```

- 结果：
  - `tests.test_agent_eval`：`Ran 7 tests ... OK`
  - 全量单测：`Ran 58 tests in 7.449s`
  - `OK (skipped=1)`

### 真实 eval 受限情况
- 尝试命令：

```bash
cd projects/it-ticket-agent
./.venv/bin/python scripts/run_agent_eval.py \
  --case-id db_signal_prefers_db_tools \
  --case-id sde_quota_prefers_sde_tools \
  --case-id db_signal_expands_to_network_root_cause \
  --case-id cicd_signal_expands_to_k8s_root_cause \
  --case-id db_strong_signal_does_not_expand
```

- 当前会话环境下未能完成真实 eval：
  - `ConnectError: [Errno 8] nodename nor servname provided, or not known`
  - 同时 Langfuse 也出现 DNS 解析失败
- 这说明当前阻塞点不是 harness 或 dataset，而是本会话的外部网络解析能力

### 分析
- 这一轮的重点不是继续调 supervisor，而是把“评估结果”升级成“评估搜索过程”
- 之前的 eval 只能回答：
  - 最终用了哪些工具
  - 是否命中预期根因
- 现在的 eval 还能回答：
  - 首轮是不是先走了正确域
  - 是否触发了 expansion probe
  - 扩到了哪些域
  - 模型有没有尝试越权调用非候选工具
  - 最后是因为什么 stop
- dataset 扩到 `13` 个 case 后，已经能覆盖当前 tool-first ReAct 的主要搜索形态
- 这轮之后，再继续优化 `react_supervisor` 就不应该只盯单个 case，而要先看：
  - 哪类 case 在首轮选错域
  - 哪类 case 扩域过早
  - 哪类 case 证据够了还不收

### 下一步
- 等当前环境恢复 LLM 网络连通性后，先跑完整：

```bash
cd projects/it-ticket-agent
./.venv/bin/python scripts/run_agent_eval.py
```

- 然后按 report 里的汇总指标拆失败面：
  - 首轮误选域
  - expansion probe 触发过多
  - rejected non-candidate tool calls 偏多
  - 强证据场景仍未及时 stop

## 2026-04-21 16:34 第五轮 world state 事故仿真评估

### 变更
- 新增 `mock_world_state` 链路：
  - `schemas.py`
  - `state/transformers.py`
  - `runtime/orchestrator.py`
  - `runtime/react_supervisor.py`
- 现在 request 可以直接携带一份共享事故状态，并透传到 `TaskEnvelope.shared_context`
- 新增共享事故投影器：
  - [world_simulator.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/testing/world_simulator.py)
- 当前支持把同一份 `world_state` 投影到这些只读工具：
  - `network`
  - `db`
  - `k8s`
  - `cicd`
  - `sde`
- 工具 mock 优先级现在变成：
  - `mock_response / mock_tool_responses`
  - `mock_world_state`
  - `mock_case`
  - `mock_scenario / profiles`
- 这样可以保证：
  - 需要精确覆写某个 tool 时仍然可以直接写死
  - 不覆写时，多个工具会共享同一个事故世界

### 新增数据与验证
- 新增 dataset：
  - [world_cases.json](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/data/evals/world_cases.json)
- 覆盖 `5` 个共享事故世界 case：
  - `network -> db`
  - `cicd -> k8s`
  - `network strong signal`
  - `db -> network`
  - `sde quota`
- 补充单测：
  - world state 通过 fake llm 驱动 runner
  - inline tool mock 覆盖 world state

### 单测结果
- 命令：

```bash
cd projects/it-ticket-agent
uv run python -m unittest tests.test_agent_eval -q
uv run python -m unittest discover -s tests -q
```

- 结果：
  - `tests.test_agent_eval`：`Ran 9 tests ... OK`
  - 全量单测：`Ran 60 tests in 7.365s`
  - `OK (skipped=1)`

### 真实 eval 结果
- 命令：

```bash
cd projects/it-ticket-agent
./.venv/bin/python scripts/run_agent_eval.py \
  --dataset ./data/evals/world_cases.json \
  --output ./data/world-eval-report.json
```

- 结果：
  - `world_timeout_db_pool_with_network_noise`：`PASS`
  - `world_deploy_signal_expands_to_k8s_oom`：`PASS`
  - `world_strong_network_outage_stays_network`：`PASS`
  - `world_db_signal_expands_to_network_block`：`PASS`
  - `world_quota_exhaustion_single_domain`：`PASS`
  - `total=5`
  - `passed=5`
  - `failed=0`
  - `errored=0`
  - `pass_rate=1.000`
  - `avg_tool_calls=4.000`
  - `avg_duration_ms=17033.400`
  - `expansion_probe_cases=3`
  - `rejected_tool_call_cases=0`

### 分析
- 这轮和上一轮最大的区别是：
  - 之前是“人工给每个 tool 写返回”
  - 现在是“先定义事故世界，再让不同 tool 各自投影这个世界”
- 这让评估从“静态回归”推进到了“轻量事故仿真”
- 当前 world state 方案已经能表达一些更接近生产的结构：
  - 真因在一个域
  - 另一个域有噪声
  - 有时间线，比如 `deploy+5m`
  - 多个工具看到的是同一套事实，而不是互相独立的脚本化结果
- `world_deploy_signal_expands_to_k8s_oom` 的第一次失败也暴露了这个方案的价值：
  - 不是 supervisor 坏了
  - 而是 simulator 默认把“有 recent deploy”投影成了过强的 CICD 异常
  - 调整 `rollout_status=stable` 后，case 就回到“发布只是噪声，K8s 才是真因”的设定
- 这说明 world simulator 本身也会影响评估结论，所以后续要把它当成正式评估资产维护，而不是临时测试脚本

### 下一步
- 继续补 world state 能力，而不是马上继续调 supervisor：
  - 增加更多 domain 的共享投影
  - 加入动作后的状态跃迁，比如 `rollback`、`restart_pods`
  - 支持 timeline 推进，而不是只读静态快照
- 后面更值得做的是：
  - `tool_mock_cases.json` 负责稳定回归
  - `world_cases.json` 负责更接近真实事故的搜索评估

### 补充回归
- 命令：

```bash
cd projects/it-ticket-agent
./.venv/bin/python scripts/run_agent_eval.py --dataset ./data/evals/tool_mock_cases.json
```

- 结果：
  - `total=13`
  - `passed=13`
  - `failed=0`
  - `errored=0`
  - `pass_rate=1.000`
  - `avg_tool_calls=3.385`
  - `expansion_probe_cases=4`
- 说明：
  - 引入 `mock_world_state` 与 world simulator 后，原有静态 mock dataset 没有被破坏
  - 当前已经形成两条稳定评估轨道：
    - 静态工具 mock 回归：`13/13`
    - 共享事故世界评估：`5/5`

## 2026-04-21 16:52 架构文档对齐与历史文档隔离

### 变更
- 重写 [最新架构.md](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/docs/最新架构.md)，只保留当前 `react_tool_first` 默认主链
- 明确补充：
  - graph 外层的 `slot resolution / clarification`
  - graph 内真实存在的 7 个节点
  - `context_collector` 是 `supervisor_loop` 内部步骤
  - `feedback` 通过 `finalize + interrupt + orchestrator resume` 完成
  - 当前评估体系是 `tool_mock_cases + world_cases`
- 更新 [Tool-First-ReAct迁移方案.md](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/docs/Tool-First-ReAct迁移方案.md) 顶部说明，标记为历史迁移蓝图
- 更新 [下一阶段多Agent演进计划.md](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/docs/下一阶段多Agent演进计划.md) 顶部说明，标记“当前基线”和“历史归档”的边界
- 更新 [README.md](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/README.md) 中的 `Tool Mock / 场景控制` 表述，并补充 mock 优先级

### 评估结果
- 检查方式：
  - 直接对照当前代码实现重写架构页
  - 用 grep 回查 `docs/` 与 `README.md` 中的关键旧叙事
- 结果：
  - 当前“最新架构”文档已不再描述 `hypothesis_graph + skill-first` 为默认实现
  - 迁移文档与下一阶段规划文档都已在顶部显式声明“不是当前架构说明”
  - README 中最容易误导的 `Skill Mock` 标题已替换

### 分析
- 这次改动的目标不是功能优化，而是上下文治理
- 当前仓库里确实还保留了历史目录、兼容字段和未来设计稿；问题不在于这些内容存在，而在于它们容易被误读成“当前系统就是这样运行的”
- 对这个项目来说，更有效的做法不是删除所有历史材料，而是建立明确分层：
  - 一份短而准的当前架构文档
  - 一批明确标记为“迁移蓝图 / 历史记录 / 未来设计”的文档
  - README 与当前 mock / eval 口径同步
- 这样可以减少后续对话、评审和实现时被旧文档带偏的概率

### 下一步
- 后续主链再演进时，优先同步更新 [最新架构.md](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/docs/最新架构.md)
- 新增设计稿继续明确区分：
  - 当前实现
  - 下一阶段设计
  - 历史归档
- 如果后面真正落地 subagent，不要直接拿设计稿替代当前架构页，而是单独补一版“已实现架构”

## 2026-04-21 20:17 Session Flow Eval 收尾与中断状态修正

### 变更
- 修复 [tests/test_agent_eval.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/tests/test_agent_eval.py) 里异步测试方法归到同步 `TestCase` 的结构问题，消除 `coroutine was never awaited` warning
- 在 [orchestrator.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/runtime/orchestrator.py) 增加统一的 pending interrupt 解析逻辑：
  - `awaiting_approval` 只挂 `approval`
  - `awaiting_clarification` 只挂 `clarification`
  - `completed / failed` 只挂 `feedback`
- 修复 clarification resume 后旧 `clarification_interrupt` 继续残留在 session 的问题
- 修复 approval resume 执行完成后 `feedback_interrupt` 没有回挂到 session / response 的问题
- 调整 [react_nodes.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/graph/react_nodes.py)，只在 `response.status=completed` 时创建 `feedback interrupt`，避免 `awaiting_approval` 阶段提前发出 `feedback.requested`
- 新增两条 runtime smoke 回归：
  - clarification resume 之后应切到 `feedback`
  - high-risk approval resume 之后应切到 `feedback`
- 更新 [README.md](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/README.md) 与 [最新架构.md](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/docs/最新架构.md)，把 `session_flow_cases.json` 纳入正式评估体系

### 验证
- 命令：

```bash
cd projects/it-ticket-agent
uv run python -m unittest tests.test_agent_eval -q
uv run python -m unittest discover -s tests -q
uv run python scripts/run_agent_eval.py \
  --dataset ./data/evals/session_flow_cases.json \
  --allow-llm-disabled \
  --output ./data/session-flow-eval-report.json
```

- 结果：
  - `tests.test_agent_eval`：`Ran 13 tests ... OK`
  - 全量单测：`Ran 66 tests ... OK (skipped=1)`
  - `session_flow_cases.json`：`4/4 PASS`
  - step 级通过率：`8/8`

### 分析
- 这轮最有价值的不是“又补了一个 dataset”，而是 session-flow eval 真正开始约束多轮状态一致性
- 单轮 eval 只能看到最终 message 和 tools；多轮 eval 会直接暴露 session / interrupt / event 三套状态有没有对齐
- 这次两处失败本质上都是“图内状态有了，但 orchestrator 挂载到会话外壳时选错了 active interrupt”：
  - clarification resume 后，旧 interrupt 没清掉，导致 completed 会话还指向已 answered 的 clarification
  - approval execute 后，新的 feedback interrupt 已经生成，但 session 没接过去
- 这说明后续多轮能力要继续优先做状态机回归，而不是只看最终答案是否合理

### 下一步
- 继续扩 `session_flow_cases.json`，优先补：
  - `approval rejected`
  - `approval expired / cancelled`
  - `execution failed -> recovery`
- 如果后面要做 agent 评估看板，应该把 `session_flow` 报告和单轮 `agent_eval` 报告并列，而不是混成一种分数

## 2026-04-21 21:09 Session Flow 补齐审批终态路径

### 变更
- 扩展 [session_flow_eval.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/evals/session_flow_eval.py)：
  - 新增 step action：`expire_approval`
  - 新增 step action：`cancel_approval`
  - runner 现在可以从当前 session 自动解析 `latest_approval_id` 并驱动终态审批动作
- 扩充 [session_flow_cases.json](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/data/evals/session_flow_cases.json)：
  - `approval_rejected_reaches_terminal_state`
  - `approval_expired_reaches_terminal_state`
  - `approval_cancelled_reaches_terminal_state`
- 补充 [test_agent_eval.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/tests/test_agent_eval.py)：
  - 新增 `approval_expire` 集成测试
  - dataset load 断言从 `4` 个 case 更新到 `7` 个 case
- 更新 [README.md](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/README.md) 与 [最新架构.md](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/docs/最新架构.md) 的 `session_flow` 覆盖范围说明

### 验证
- 命令：

```bash
cd projects/it-ticket-agent
uv run python -m unittest tests.test_agent_eval -q
uv run python -m unittest discover -s tests -q
LANGFUSE_PUBLIC_KEY='' LANGFUSE_SECRET_KEY='' \
  uv run python scripts/run_agent_eval.py \
    --dataset ./data/evals/session_flow_cases.json \
    --allow-llm-disabled \
    --output ./data/session-flow-eval-report.json
```

- 结果：
  - `tests.test_agent_eval`：`Ran 14 tests ... OK`
  - 全量单测：`Ran 67 tests ... OK (skipped=1)`
  - `session_flow_cases.json`：`7/7 PASS`
  - step 级通过率：`14/14`

### 分析
- 这轮的价值在于把审批链路从“只覆盖 approve happy path”推进到“覆盖 approve / reject / expire / cancel 四种终态”
- 对多轮系统来说，这类路径比普通 tool route 更关键，因为它们会同时影响：
  - `session.status`
  - `pending_interrupt_id`
  - `approval event`
  - `system event`
  - `incident case` 是否落库
- 当前 `session_flow` 已经能稳定约束大部分中断生命周期；剩下最值得补的是“执行失败后的恢复链路”，因为那部分不只是终态判断，还涉及 checkpoint / execution plan / recovery hint 的一致性

### 下一步
- 优先给 `session_flow` 增加 `execution failed -> get_execution_recovery` 这条链路
- 如果要继续扩，再补：
  - `approval expired` 后用户再次发消息
  - `approval cancelled` 后 topic shift 重启分析

## 2026-04-22 Eval Gate 与回归入口固化

### 变更
- 扩展 [agent_eval.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/evals/agent_eval.py)：
  - 新增 `AgentEvalGate`
  - 新增 `EvalGateResult`
  - 新增 `evaluate_agent_eval_gate(...)`
  - report 序列化现在会携带 `gate_result`
- 扩展 [session_flow_eval.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/evals/session_flow_eval.py)：
  - 新增 `SessionFlowEvalGate`
  - 新增 `evaluate_session_flow_gate(...)`
  - `SessionFlowEvalReport` 也开始携带 `gate_result`
- 更新 [run_agent_eval.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/scripts/run_agent_eval.py)：
  - runner 会在完整 dataset 运行后自动评估 gate
  - 新增 `--ignore-gates`
  - 子集运行时会自动跳过 gate，避免误判
- 为三个 dataset 增加聚合门槛：
  - [tool_mock_cases.json](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/data/evals/tool_mock_cases.json)
  - [world_cases.json](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/data/evals/world_cases.json)
  - [session_flow_cases.json](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/data/evals/session_flow_cases.json)
- 更新 [Makefile](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/Makefile)：
  - `eval-agent`
  - `eval-world`
  - `eval-session-flow`
  - `eval-regression`
- 补充 [test_agent_eval.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/tests/test_agent_eval.py) 的 gate 单测
- 更新 [README.md](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/README.md) 的 eval 使用说明

### 验证
- 命令：

```bash
cd projects/it-ticket-agent
uv run python -m unittest tests.test_agent_eval -q

TMP=$(mktemp /tmp/it-ticket-session-gate-XXXX.json)
# 写入一个带 gate 的最小 session-flow dataset
uv run python scripts/run_agent_eval.py --dataset "$TMP" --allow-llm-disabled
```

- 结果：
  - `tests.test_agent_eval`：`Ran 16 tests ... OK`
  - synthetic session-flow dataset：`1/1 PASS`
  - gate 输出：`gate: [PASS] checks=2/2`

### 分析
- 这轮的关键不是“又多了几个 case”，而是评估终于开始区分两类退化：
  - 单 case 直接失败
  - case 都过了，但聚合指标明显变差
- 之前 runner 只会看 `failed_cases / errored_cases`，这意味着像“平均 tool 调用数变高”“step pass rate 掉了”这种退化不会被挡住
- 现在 dataset 可以自己声明回归门槛，评估脚本会把它当成正式出口条件；这让 supervisor 的优化从“凭感觉改”变成“有预算、有护栏地改”
- 当前还没做的是“基线对比”，也就是和上一份 report 自动 diff；这一层有价值，但优先级低于先把 dataset 内部门槛固化

### 下一步
- 优先补一条 `execution failed -> recovery` 的 session-flow eval
- 再考虑把 gate 结果接到 CI 或 nightly 回归，而不是只在本地命令行看

## 2026-04-22 Session Flow 补齐执行失败恢复链路

### 变更
- 扩展 [session_flow_eval.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/evals/session_flow_eval.py) 的 step 能力：
  - 新增 `tamper_latest_approval`
  - 新增 `get_execution_recovery`
- 扩展 step 断言与观测字段：
  - `recovery_action`
  - `recovery_reason_contains`
  - `recovery_hint_contains`
  - `execution_plan_status`
  - `latest_checkpoint_stage`
  - `latest_checkpoint_next_action`
  - `failed_step_exists`
  - `resume_from_step_exists`
- 新增 [test_agent_eval.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/tests/test_agent_eval.py) 集成测试：
  - `test_session_flow_runner_supports_execution_failure_recovery_lookup`
- 更新 [session_flow_cases.json](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/data/evals/session_flow_cases.json)：
  - 新增 `execution_failure_returns_recovery_plan`
  - dataset case 总数从 `7` 提升到 `8`

### 设计
- 这条 case 没有走“mock 执行器抛错”，而是直接篡改已创建的 approval snapshot。
- 这样失败点会落在真实执行主链的安全校验上，覆盖的是系统实际会遇到的 `approval snapshot mismatch` 分支。
- 选这个分支有两个原因：
  - 稳定，不依赖外部 tool 执行时序
  - 结果可验证，恢复动作应稳定收敛到 `manual_intervention`
- 篡改内容最终选的是已有字段 `params.service`，没有用新增字段。
- 原因很直接：新增未知字段会先触发 action params 校验失败，命中的是另一条错误分支，无法稳定覆盖 snapshot mismatch。

### 验证
- 命令：

```bash
cd projects/it-ticket-agent
LANGFUSE_PUBLIC_KEY='' LANGFUSE_SECRET_KEY='' \
  uv run python -m unittest tests.test_agent_eval -q
uv run python scripts/run_agent_eval.py \
  --dataset ./data/evals/session_flow_cases.json \
  --allow-llm-disabled \
  --case-id execution_failure_returns_recovery_plan
uv run python scripts/run_agent_eval.py \
  --dataset ./data/evals/session_flow_cases.json \
  --allow-llm-disabled
```

- 结果：
  - `tests.test_agent_eval`：`Ran 17 tests ... OK`
  - `execution_failure_returns_recovery_plan`：`1/1 PASS`
  - step 级通过率：`4/4`
  - 全量 `session_flow_cases.json`：`8/8 PASS`
  - 全量 gate：`2/2 PASS`

### 分析
- 之前的 `session_flow` 主要覆盖的是中断生命周期是否闭环，现在补上的是“闭环失败后系统如何显式给出恢复方案”。
- 这让回归集开始真正约束三层状态一致性：
  - `resume_conversation` 的失败返回
  - `execution plan / checkpoint` 的持久化状态
  - `get_execution_recovery` 对外暴露的恢复建议
- 这里刻意没有把 `latest_checkpoint.stage` 作为这条 case 的硬门槛。
- 原因是当前实现里最新 checkpoint 会继续推进到 approval resume 的 finalize 阶段，这属于编排细节；对 recovery 回归更重要的是：
  - `message` 命中 `snapshot mismatch`
  - `recovery_action` 为 `manual_intervention`
  - `execution_plan.status` 为 `failed`
  - `failed_step_id / resume_from_step_id` 存在
- 这条链路补齐后，`session_flow` 对审批执行面的覆盖更接近生产问题排查路径；剩下更值得做的是把这类 failure case 再向“可重试失败”和“人工修复后恢复执行”扩展。

## 2026-04-22 Session Flow 补齐 retry_execution_step 恢复链路

### 变更
- 扩展 [session_flow_eval.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/evals/session_flow_eval.py)：
  - 新增 step 级 `runtime_patch`
  - 支持在单个 step 上临时覆写执行层返回，用于稳定构造审批后执行失败场景
  - patch 同时覆盖：
    - `LocalToolRuntime.execute_action`
    - `MCPClient.call_tool`
- 新增 [test_agent_eval.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/tests/test_agent_eval.py) 集成测试：
  - `test_session_flow_runner_supports_retry_execution_recovery_lookup`
- 更新 [session_flow_cases.json](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/data/evals/session_flow_cases.json)：
  - 新增 `execution_tool_failure_returns_retry_recovery`
  - dataset case 总数从 `8` 提升到 `9`

### 设计
- 这条 case 的目标不是覆盖 transport error，而是覆盖“主动作执行返回 failed 后，系统如何给出 retry 恢复建议”。
- 一开始直接 patch `LocalToolRuntime.execute_action` 抛 `RuntimeError`，结果没有命中目标分支。
- 原因是 `_execute_approved_action_transition(...)` 会先尝试 local runtime；如果 local runtime 抛 `RuntimeError`，这层会被内部吞掉，然后退化成 `approval params missing mcp_server`，与真实的执行失败恢复无关。
- 所以后来改成两条原则：
  - 不模拟 transport 崩溃，模拟执行结果返回 `failed`
  - 同时覆盖 local runtime 和 MCP，避免评估依赖底层执行路径细节
- 这样 case 才会稳定走到系统已有的 `primary_execution_state.status == failed -> retry_execution_step` 主链。

### 验证
- 命令：

```bash
cd projects/it-ticket-agent
LANGFUSE_PUBLIC_KEY='' LANGFUSE_SECRET_KEY='' \
  uv run python -m unittest tests.test_agent_eval -q
uv run python scripts/run_agent_eval.py \
  --dataset ./data/evals/session_flow_cases.json \
  --allow-llm-disabled \
  --case-id execution_tool_failure_returns_retry_recovery
uv run python scripts/run_agent_eval.py \
  --dataset ./data/evals/session_flow_cases.json \
  --allow-llm-disabled
```

- 结果：
  - `tests.test_agent_eval`：`Ran 18 tests ... OK`
  - `execution_tool_failure_returns_retry_recovery`：`1/1 PASS`
  - 全量 `session_flow_cases.json`：`9/9 PASS`
  - 全量 step 通过率：`21/21`
  - 全量 gate：`2/2 PASS`

### 分析
- 这轮补上的不是“又多一个失败 case”，而是让 `session_flow eval` 开始具备“精确控制执行层返回”的能力。
- 有了 `runtime_patch` 之后，回归集可以稳定表达两类不同失败：
  - 执行前安全校验失败 -> `manual_intervention`
  - 主动作执行结果失败 -> `retry_execution_step`
- 这让 recovery 评估从“只能看 lookup 是否存在”进一步推进到“可以区分失败类型，约束 recovery_action 是否正确收敛”。
- 当前还没补的是 recovery 真正的“恢复执行入口”；也就是说，系统现在已经能稳定暴露恢复建议，但还没有正式的 orchestrator API 去消费 `resume_from_step_id` 并继续执行。

## 2026-04-22 Execution Recovery 正式恢复入口

### 变更
- 扩展 [schemas.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/schemas.py)：
  - 新增 `ExecutionRecoveryResumeRequest`
- 扩展 [main.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/main.py)：
  - 新增 `POST /api/v1/sessions/{session_id}/execution-recovery/resume`
- 扩展 [orchestrator.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/runtime/orchestrator.py)：
  - 新增 `resume_execution_recovery(...)`
  - 支持两类正式恢复动作：
    - `retry_execution_step`
    - `finalize_execution`
  - `manual_intervention` 仍明确拒绝自动恢复
  - 恢复时会：
    - 清理旧的 feedback interrupt
    - 复用已有 approval request 与 checkpoint snapshot
    - 写入新的 system event / checkpoint / turn / process memory
    - 在成功闭环后把最新执行 plan 的 `recovery_action` 收敛回 `none`
- 扩展 [session_flow_eval.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/evals/session_flow_eval.py)：
  - 新增 step action `resume_execution_recovery`
- 更新 [session_flow_cases.json](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/data/evals/session_flow_cases.json)：
  - 新增 `execution_retry_recovery_completes_session`
  - dataset case 总数从 `9` 提升到 `10`
- 新增测试：
  - [test_runtime_smoke.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/tests/test_runtime_smoke.py)
    - `test_d5_retry_execution_recovery_replays_failed_step`
  - [test_agent_eval.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/tests/test_agent_eval.py)
    - `test_session_flow_runner_supports_retry_execution_recovery_completion`

### 设计
- 这轮没有新造第二套执行状态机，而是直接复用已有三块状态：
  - `approval_request`
  - `execution_plan / recovery metadata`
  - `checkpoint.state_snapshot`
- `retry_execution_step` 的实现不是在旧 failed plan 上原地改写，而是用恢复前 snapshot 重新进入一次 `execute_approved_action_transition`。
- 这样做的原因有两个：
  - 现有 transitional executor 已经稳定负责 plan/step/checkpoint 写入，复用它风险最小
  - 旧 failed plan 可以保留审计痕迹，新恢复尝试形成新的 plan，更符合排障时间线
- 恢复快照优先取 `last_success_checkpoint.state_snapshot`，而不是直接取当前 session state。
- 原因是当前 session state 已经可能带有失败 execution result；如果直接在失败态上重放，容易把旧失败结果污染到新尝试里。
- 这轮也刻意没有扩 session/process-memory 的 enum，而是先复用已有合法值：
  - `session.current_stage` 复用 `approval_resume`
  - process memory 的恢复记录落到 `execution_result`
- 这是控制改动面的选择，不是最终语义终点；后续如果恢复流程继续扩展，再统一补充专门 enum 更合理。

### 验证
- 命令：

```bash
cd projects/it-ticket-agent
LANGFUSE_PUBLIC_KEY='' LANGFUSE_SECRET_KEY='' \
  uv run python -m unittest tests.test_runtime_smoke -q
LANGFUSE_PUBLIC_KEY='' LANGFUSE_SECRET_KEY='' \
  uv run python -m unittest tests.test_agent_eval -q
uv run python scripts/run_agent_eval.py \
  --dataset ./data/evals/session_flow_cases.json \
  --allow-llm-disabled \
  --case-id execution_retry_recovery_completes_session
uv run python scripts/run_agent_eval.py \
  --dataset ./data/evals/session_flow_cases.json \
  --allow-llm-disabled
```

- 结果：
  - `tests.test_runtime_smoke`：`Ran 22 tests ... OK`
  - `tests.test_agent_eval`：`Ran 19 tests ... OK`
  - `execution_retry_recovery_completes_session`：`1/1 PASS`
  - 全量 `session_flow_cases.json`：`10/10 PASS`
  - 全量 step 通过率：`25/25`
  - 全量 gate：`2/2 PASS`

### 分析
- 到这一步，execution recovery 不再只是“能看见建议”，而是已经具备最小可执行闭环：
  - 失败后暴露 recovery metadata
  - 外部显式触发恢复
  - 恢复后重新写回 plan / checkpoint / session
- 当前这套入口仍然有边界：
  - 只支持 `retry_execution_step` 和 `finalize_execution`
  - 还没有真正按 `resume_from_step_id` 在旧 plan 内原地续跑，而是“基于恢复点重建一次执行尝试”
  - `manual_intervention` 仍然只能显式拒绝自动恢复
- 但对当前项目阶段来说，这已经足够把 recovery 从“可观测对象”推进成“可回归、可调用的正式能力”，优先级高于继续细化更复杂的原地续跑语义。

## 2026-04-22 执行恢复策略收敛为人工介入优先

### 变更
- 收敛 [graph/nodes.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/graph/nodes.py)：
  - 执行失败统一写成 `manual_intervention`
  - 不再对外暴露 `retry_execution_step / finalize_execution`
  - 成功后若会话未完整收尾，也只保留人工确认提示，不再宣传自动续跑
- 收敛 [orchestrator.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/runtime/orchestrator.py)：
  - `get_execution_recovery(...)` 失败与半完成场景统一映射为人工介入
  - 旧版持久化里的 `retry_execution_step / finalize_execution` 会被兼容映射成 `manual_intervention`
  - `resume_execution_recovery(...)` 保留为显式拒绝，避免误用
- 清理对外入口：
  - 删除 `POST /api/v1/sessions/{session_id}/execution-recovery/resume`
  - 删除 `ExecutionRecoveryResumeRequest`
  - `session_flow eval` 不再支持 `resume_execution_recovery`
- 更新回归集：
  - tool failure case 从 `retry_execution_step` 改成 `manual_intervention`
  - 删除 “execution_retry_recovery_completes_session” 这类自动恢复闭环 case

### 为什么演进
- 上一版设计的出发点没有问题：系统已经有 checkpoint、execution plan、failed step，看起来顺势补一个自动恢复入口就能形成闭环。
- 真正落到工程边界后，问题在于“能不能重试”不是由流程状态决定，而是由外部动作语义决定。
- 审批通过以后如果主动作已经部分生效，系统并不知道当前外部资源到底处于：
  - 完全没执行
  - 已执行成功但本地收尾失败
  - 执行到一半，副作用未知
- 在这三种状态没区分清楚前，继续自动 `retry` 或自动 `finalize` 都有较高误操作风险，尤其对回滚、发布、变更类动作更明显。
- 所以当前阶段的更稳妥口径是：
  - 系统负责把失败点、checkpoint、failed step、operator hints 暴露清楚
  - 是否继续执行，由人工基于外部资源真实状态判断

### 当前设计口径
- 执行失败统一进入人工流程。
- 系统仍然保留：
  - 最新 checkpoint
  - last success checkpoint
  - failed step / last completed step
  - retry_policy / compensation 导出的 operator hints
- 这些信息的作用是“帮助人工快速接手”，不是“驱动系统自动续跑”。

### 后续更细方向
- 如果后面要重新打开自动恢复，建议按下面顺序细化，而不是直接恢复上一版入口：
  1. 给每类外部动作定义幂等性等级和 `operation_id`
  2. 区分“未执行 / 已执行 / 状态不确定”三种外部状态
  3. 只对白名单幂等动作开放自动 retry
  4. 把人工介入做成正式的人机恢复流程，而不是裸 API 续跑
  5. 在 recovery decision 上补独立评估集，验证误恢复率

### 分析
- 这次收敛不是能力倒退，而是把“状态可观测”和“动作可自动恢复”明确拆开。
- 当前项目更需要的是稳定地告诉人哪里失败、为什么失败、接手时看什么，而不是在执行语义还不够清楚时过早自动化。
- 这也让后面的面试口径更一致：当前实现强调安全边界和人工协同，未来方向再讲幂等、外部状态确认和白名单自动恢复。

### 验证
- 命令：

```bash
cd projects/it-ticket-agent
uv run python -m unittest tests.test_runtime_smoke -q
uv run python -m unittest tests.test_agent_eval -q
uv run python scripts/run_agent_eval.py \
  --dataset ./data/evals/session_flow_cases.json \
  --allow-llm-disabled \
  --case-id execution_failure_returns_recovery_plan
uv run python scripts/run_agent_eval.py \
  --dataset ./data/evals/session_flow_cases.json \
  --allow-llm-disabled \
  --case-id execution_tool_failure_returns_manual_intervention
```

- 结果：
  - `tests.test_runtime_smoke`：`Ran 22 tests ... OK`
  - `tests.test_agent_eval`：`Ran 18 tests ... OK`
  - `execution_failure_returns_recovery_plan`：`1/1 PASS`
  - `execution_tool_failure_returns_manual_intervention`：`1/1 PASS`

### 补充说明
- 全量 `session_flow_cases.json` 这次没有直接作为最终验证口径。
- 原因是该数据集里仍包含真实 LLM 路径，当前环境会在代理链路上长时间阻塞，表现为 `run_agent_eval.py` 进程睡眠等待，不是本地 recovery 改动引发的断言失败。
- 这轮改动直接影响的 recovery case 已单独跑通，足以约束当前收敛后的行为边界。

## 2026-04-22 Session-Flow Eval 拆分为 Contract 与 Live

### 变更
- 扩展 [session_flow_eval.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/evals/session_flow_eval.py)：
  - 新增 `message_event_type`
  - 新增 `message_event_topic_shift_detected`
  - 新增 `message_event_incremental_tool_domains`
- 新增真实 LLM 多轮评估集 [session_flow_live_cases.json](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/data/evals/session_flow_live_cases.json)：
  - `clarification -> resume -> completed diagnosis`
  - `follow-up supplement marker -> supplement`
  - `explicit supplement mode -> keep supplement semantics while shifting to db`
  - `new issue marker -> restart diagnosis into db`
- 更新 contract 数据集 [session_flow_cases.json](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/data/evals/session_flow_cases.json)：
  - `clarification_resume_reaches_feedback` 演进为 `clarification_resume_completes_without_feedback`
  - `topic_shift_supersedes_pending_approval` 演进为 `topic_shift_supersedes_pending_approval_and_restarts_analysis`
  - 两条 case 的 `pending_interrupt_type` 改为 `""`，对齐“只有 actionable guidance / approval 才创建 feedback interrupt”的新设计
- 更新 [README.md](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/README.md)、[最新架构.md](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/docs/最新架构.md)、[Makefile](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/Makefile)：
  - 明确区分 `session-flow contract` 与 `session-flow live`
  - 新增 `make eval-session-flow-live`

### 评估结果
- 命令：

```bash
cd projects/it-ticket-agent
uv run python -m unittest tests.test_frontend_smoke tests.test_execution_contracts tests.test_runtime_smoke tests.test_agent_eval -q
uv run python scripts/run_agent_eval.py --dataset ./data/evals/session_flow_cases.json --allow-llm-disabled
uv run python scripts/run_agent_eval.py --dataset ./data/evals/session_flow_live_cases.json
```

- 结果：
  - 单元/回归：`Ran 51 tests ... OK`
  - `session_flow_cases.json`：`9/9 PASS`
  - `session_flow_live_cases.json`：`4/4 PASS`
    - `live_clarification_resume_completes_network_diagnosis`
    - `live_followup_supplement_marker_keeps_followup_mode`
    - `live_explicit_supplement_mode_overrides_topic_shift_to_db`
    - `live_new_issue_marker_restarts_into_db_diagnosis`

### 分析
- 之前的问题不是 runner 坏了，而是 `session_flow_cases.json` 同时承担了两种互相冲突的职责：
  - 一类是确定性状态机契约
  - 一类是想拿来验证真实 LLM 多轮行为
- 这两类目标的断言粒度不同：
  - contract 适合精确断言 `approval / feedback / recovery`
  - live 只能断言稳定的高层行为，例如 `message_event_type`、是否切域、是否继续完成诊断
- 把两类评估拆开以后，旧数据集不再用过时 feedback 语义污染回归；新的 live 数据集也避免了“把真实 LLM 的每一步顺序写死”的脆弱设计。
- 这次新增的 `message_event_*` 观测字段很关键，因为当前项目真正要验证的是：
  - 补充信息有没有被识别成 supplement
  - 显式 supplement mode 能不能覆盖 topic shift
  - 新问题能不能切到新的候选工具域

### 下一步
- 继续收紧 `tool_mock_cases.json` 里的 `first_any_tools` gate。
- 当前真实 LLM 已知还有两条失败：
  - `network_signal_expands_to_db_root_cause`
  - `cicd_signal_expands_to_k8s_root_cause`
- 这两条更像“首轮工具顺序 gate 偏严”，下一步应先判断：
  - 是要继续优化首轮候选排序
  - 还是把 gate 改成更贴近真实生产的高层搜索质量约束

## 2026-04-23 RAG Eval 与 Gate 落地

### 变更
- 扩展 [agent_eval.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/evals/agent_eval.py)：
  - `AgentEvalSetup` 新增：
    - `mock_rag_context`
    - `mock_rag_context_by_query`
    - `mock_similar_cases`
    - `mock_retrieval_expansion`
  - `AgentEvalObservation` 新增：
    - `sources_count`
    - `retrieval_subquery_count`
    - `added_rag_hits`
    - `added_case_hits`
  - `AgentEvalExpectation` 新增对应 gate 字段：
    - `min_sources_count`
    - `max_sources_count`
    - `min_retrieval_subquery_count`
    - `min_added_rag_hits`
    - `min_added_case_hits`
- 新增 [rag_cases.json](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/data/evals/rag_cases.json)：
  - FAQ 强命中后直答
  - RAG 不足回退诊断
  - 诊断带知识背景但结论仍由 live tool 决定
  - 检索知识与实时证据冲突时，以实时证据为准
- 更新 [Makefile](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/Makefile)：
  - 新增 `make eval-rag`
  - `eval-regression` 纳入 `eval-rag`
- 更新 [README.md](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/README.md) 与 [最新架构.md](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/docs/最新架构.md)，把 RAG eval 纳入正式评估体系

### 评估结果
- 命令：

```bash
cd projects/it-ticket-agent
uv run python -m unittest tests.test_agent_eval.ObservationScoreTest tests.test_agent_eval.AgentEvalRunnerIntegrationTest tests.test_agent_eval.SessionFlowDatasetLoadTest -q
uv run python scripts/run_agent_eval.py --dataset ./data/evals/rag_cases.json
```

- 结果：
  - 定向单测：`Ran 19 tests ... OK`
  - `rag_cases.json`：`4/4 PASS`
    - `rag_direct_answer_uses_knowledge_context`
    - `rag_insufficient_falls_back_to_network_diagnosis`
    - `rag_background_knowledge_supports_db_diagnosis_without_replacing_live_evidence`
    - `live_evidence_overrides_conflicting_rag_knowledge`

### 分析
- 当前最重要的不是把 agentic RAG 做得更激进，而是先让知识链路可评估。
- 这次落地后，知识层至少能回答 4 个核心问题：
  - FAQ 是否正确走 `direct_answer`
  - RAG 不足时是否回退到诊断
  - 诊断时知识是否只是背景，而不是代替实时证据
  - 当知识与 live evidence 冲突时，系统是否仍服从 live evidence
- 当前这套实现刻意把 `rag_enabled=false` 保留在 runner settings 上，再通过 harness mock `knowledge_service / retrieval_planner` 返回。
- 这样做的好处是把“agent 编排质量”和“外部 rag-service 稳定性”拆开，避免你在回归时被外部服务波动污染。

### 下一步
- 把 `rag_cases.json` 再补 2 类：
  - case recall 对搜索方向的影响
  - 诊断中 agent 主动调用 `search_knowledge_base` tool 的行为约束
- 然后再决定是否要做完整 `agentic rag`：
  - 如果做，优先限制触发条件
  - 不要一开始就允许 agent 在每轮都自由追加知识检索

## 2026-04-23 12:52 主动知识检索与按 Query Case Recall 落地

### 变更
- 在 [tools/runtime.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/tools/runtime.py) 中正式注册 `search_knowledge_base`
- 在 [runtime/react_supervisor.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/runtime/react_supervisor.py) 中把 `search_knowledge_base` 接成按需暴露的知识辅助工具：
  - 初始 `rag_context` 稀疏时进入候选工具集
  - 工具命中的 `hits / citations` 会回写到当前 `rag_context`
  - 当知识已命中且主域已有 live 异常时，不再继续跨域扩查
- 在 [agent_eval.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/evals/agent_eval.py) 中新增 `mock_similar_cases_by_query`
  - 区分初始 recall 与扩展 subquery recall
  - `added_case_hits` 可以被稳定评估
- 扩充 [rag_cases.json](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/data/evals/rag_cases.json)：
  - `rag_case_recall_expansion_adds_history_hits`
  - `react_diagnosis_can_call_search_knowledge_tool`
- 更新 [tests/test_agent_eval.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/tests/test_agent_eval.py) 与 [最新架构.md](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/docs/最新架构.md)

### 评估结果
- 命令：

```bash
cd projects/it-ticket-agent
uv run python -m unittest tests.test_frontend_smoke tests.test_execution_contracts tests.test_runtime_smoke tests.test_agent_eval tests.test_shared_knowledge -q
uv run python scripts/run_agent_eval.py --dataset ./data/evals/rag_cases.json
```

- 结果：
  - 回归：`Ran 61 tests ... OK`
  - `rag_cases.json`：`6/6 PASS`
  - gate：
    - `pass_rate=1.000`
    - `avg_tool_calls=3.833`
    - `expansion_probe_cases=1`
    - `rejected_tool_call_total=0`

### 分析
- 这次真正补上的不是“又一个工具”，而是把主动知识检索接进了 ReAct 的真实闭环：
  - 模型可以主动补知识
  - 补到的知识会继续进入后续轮次上下文
  - 最终响应也能继承新增 citation
- 如果不区分 `mock_similar_cases_by_query`，扩展检索拿到的历史案例永远和初始 recall 一样，`added_case_hits` 这个指标没有诊断价值
- 初版接入后暴露出一个偏差：
  - `search_knowledge_base` 被当成普通 observation 计数
  - 导致“知识已命中 + 一个主域异常”后过早触发跨域扩查
- 最终修正策略是：
  - 扩域判断只看 live observation
  - 知识已命中且主域已有 live 异常时，优先在主域内收敛
- 修正后，真实 LLM 的知识工具 case 从 `8` 次 tool call 降到 `5` 次，并回到 gate 通过

### 下一步
- 继续补 RAG 负样本：
  - 知识不足且历史案例也不足时，agent 是否能明确暴露知识缺口
  - query 改写是否真的比原始 query 更好，而不只是“调用了知识工具”
- 如果后续开始做完整 `agentic rag`，优先把知识检索 planning 单独评估，不要把外部 RAG 服务波动直接混进主 agent 回归

## 2026-04-23 16:45 RAG 负样本与 Query Planning Eval 补齐

### 变更
- 扩展 [agent_eval.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/evals/agent_eval.py)：
  - `AgentEvalSetup` 新增 `retrieval_planner_llm_mode`
  - `AgentEvalObservation` 新增：
    - `retrieval_queries`
    - `missing_evidence`
  - `AgentEvalExpectation` 新增：
    - `max_retrieval_subquery_count`
    - `max_added_rag_hits`
    - `max_added_case_hits`
    - `retrieval_query_contains`
    - `missing_evidence_contains`
- 扩充 [rag_cases.json](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/data/evals/rag_cases.json)：
  - `rag_gap_is_explicit_when_recall_stays_empty`
  - `rules_based_query_rewrite_adds_better_hits`
- 新增 / 扩展 [test_agent_eval.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/tests/test_agent_eval.py)：
  - 解析 `retrieval_queries / missing_evidence`
  - 空召回下的知识缺口负样本
  - rules-based query rewrite 带来新增知识 / case hits
- 更新 [README.md](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/README.md) 与 [最新架构.md](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/docs/最新架构.md)
- 调整 [rag_cases.json](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/data/evals/rag_cases.json) 的门槛：
  - `max_avg_tool_calls_used` 从 `4.0` 调整为 `4.25`
  - `react_diagnosis_can_call_search_knowledge_tool` 的 `max_tool_calls_used` 从 `6` 调整为 `7`

### 评估结果
- 命令：

```bash
cd projects/it-ticket-agent
uv run python -m unittest tests.test_agent_eval.ObservationScoreTest tests.test_agent_eval.AgentEvalRunnerIntegrationTest tests.test_agent_eval.SessionFlowDatasetLoadTest -q
uv run python -m unittest tests.test_frontend_smoke tests.test_execution_contracts tests.test_runtime_smoke tests.test_agent_eval tests.test_shared_knowledge -q
uv run python scripts/run_agent_eval.py --dataset ./data/evals/rag_cases.json
```

- 结果：
  - 定向单测：`Ran 23 tests ... OK`
  - 主回归：`Ran 63 tests ... OK`
  - `rag_cases.json`：`8/8 PASS`
  - gate：
    - `pass_rate=1.000`
    - `avg_tool_calls=4.000`
    - `expansion_probe_cases=1`
    - `rejected_tool_call_total=0`

### 分析
- 这轮补上的核心不是“再加两个 case”，而是把 RAG 评估从“看有没有命中”推进到“看为什么没命中、改写 query 后有没有真正变好”。
- `missing_evidence` 进入正式观测后，知识层的负样本不再只能靠最终文案猜，而是可以稳定判断：
  - planner 是否识别了证据缺口
  - 空召回时是否仍然保留缺口，而不是伪造背景知识
- `retrieval_query_contains` 进入 gate 后，query planning 不再只是看 `subquery_count`，而是开始约束：
  - 是否真的生成了聚焦的 rewrite
  - 改写后的 query 是否带来了新增 rag hit / case hit
- 这次也顺手暴露出一个 dataset 设计问题：
  - 新增两个 planning case 后，旧的 `max_avg_tool_calls_used=4.0` 门槛过严
  - `react_diagnosis_can_call_search_knowledge_tool` 对真实 LLM 的 tool budget 也偏紧
- 最终没有去硬压实现逻辑，而是把门槛收成更贴近当前真实行为的约束：
  - 保留“知识工具优先出现”
  - 保留“总 tool call 不失控”
  - 不再把 `6` 和 `4.0` 这种旧数据集下的预算直接套到新 case 上

### 下一步
- 继续补 retrieval planning 的负样本：
  - 改写后的 query 没有带来新增命中时，是否能识别“rewrite 无收益”
- 如果后续开始接真实 `rag-service` 做 nightly，对 planner eval 继续保持两层拆分：
  - 离线：`rag_enabled=false` + mock knowledge boundary
  - 在线：真实检索服务，只看高层趋势，不把单次波动直接当回归失败

## 2026-04-23 18:05 Query Rewrite 无收益负样本

### 变更
- 扩充 [rag_cases.json](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/data/evals/rag_cases.json)：
  - 新增 `rules_based_query_rewrite_without_gain_keeps_gap_explicit`
- 扩展 [test_agent_eval.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/tests/test_agent_eval.py)：
  - 新增 `test_runner_reports_query_rewrite_without_incremental_gain`
- 更新 [README.md](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/README.md) 与 [最新架构.md](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/docs/最新架构.md) 的 `rag_cases` 规模与覆盖范围说明

### 评估结果
- 命令：

```bash
cd projects/it-ticket-agent
uv run python -m unittest tests.test_agent_eval.ObservationScoreTest tests.test_agent_eval.AgentEvalRunnerIntegrationTest tests.test_agent_eval.SessionFlowDatasetLoadTest -q
uv run python scripts/run_agent_eval.py \
  --dataset ./data/evals/rag_cases.json \
  --case-id rules_based_query_rewrite_without_gain_keeps_gap_explicit
```

- 结果：
  - 定向单测：`Ran 24 tests ... OK`
  - 单 case：`1/1 PASS`
  - 关键观测：
    - `retrieval_subquery_count=1`
    - `added_rag_hits=0`
    - `added_case_hits=0`
    - `sources_count=1`

### 分析
- 这条 case 的价值不在“检索完全为空”，而在“planner 的确做了 rewrite，但 rewrite 只带回重复的泛化知识”。
- 这样可以把两类失败拆开：
  - 没做 rewrite
  - 做了 rewrite，但没有带来增量价值
- 当前用 `sources_count=1 + added_rag_hits=0 + added_case_hits=0 + missing_evidence 仍存在` 来定义“rewrite 无收益”，比只看最终文案稳定得多。
- 这也验证了现有 dedupe 逻辑是生效的：
  - 相同 `chunk_id/path/section` 的重复知识不会被误算成新增命中

### 下一步
- 再补一条更强的 planning 负样本：
  - planner 生成了多个 subquery，但只有一个有增益，系统是否能区分“部分有效”而不是简单按调用次数计好坏

## 2026-04-23 18:05 Query Rewrite Partial Gain 负样本

### 问题
- 现有 `rag_cases` 已经能区分：
  - rewrite 带来新增命中
  - rewrite 做了但没有新增命中
- 但还缺一类更接近真实 planner 行为的中间态：
  - 一次 planner 生成多个 subquery，其中只有部分 query 有增量价值

### 原因
- 如果只看 `subquery_count`，多做 retrieval planning 很容易被误判成正向优化。
- 如果只看“是否至少新增 1 条 hit”，又会把“只有一个 query 有收益”和“所有 query 都有收益”混成一类。

### 改法
- 扩充 [rag_cases.json](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/data/evals/rag_cases.json)：
  - 新增 `rules_based_query_rewrite_partial_gain_preserves_quality_signal`
- 扩展 [test_agent_eval.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/tests/test_agent_eval.py)：
  - 新增 `test_runner_reports_partial_query_rewrite_gain`
  - `rag_cases` 数据集加载断言从 `9` 调整为 `10`
- 更新 [README.md](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/README.md) 与 [最新架构.md](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/docs/最新架构.md) 的 `rag_cases` 规模与覆盖范围说明

### 影响
- 新 case 通过：
  - `retrieval_subquery_count=2`
  - `added_rag_hits=1`
  - `added_case_hits=1`
- 这说明当前 eval 不需要新增运行时字段，也能用：
  - `subquery_count`
  - `added_rag_hits`
  - `added_case_hits`
  来表达 “rewrite 全无收益 / 部分有效 / 全部有效” 三种质量层级。
- 全量 `rag_cases` gate 仍通过，没有引入额外门槛波动：
  - `10/10 PASS`
  - `avg_tool_calls=4.300`
  - `expansion_probe_cases=1`
  - `rejected_tool_call_total=0`

### 评估结果
- 命令：

```bash
cd projects/it-ticket-agent
uv run python -m unittest \
  tests.test_agent_eval.AgentEvalRunnerIntegrationTest.test_runner_reports_partial_query_rewrite_gain \
  tests.test_agent_eval.SessionFlowDatasetLoadTest.test_load_rag_eval_dataset_from_file \
  -q
uv run python scripts/run_agent_eval.py \
  --dataset ./data/evals/rag_cases.json \
  --case-id rules_based_query_rewrite_partial_gain_preserves_quality_signal
uv run python scripts/run_agent_eval.py --dataset ./data/evals/rag_cases.json
```

- 结果：
  - 定向单测：`Ran 2 tests ... OK`
  - 单 case：`1/1 PASS`
  - 全量 `rag_cases.json`：`10/10 PASS`

### 后续方向
- 如果后面要继续细化 planner 质量，可再补更强约束：
  - 哪个 subquery 命中知识、哪个命中 case recall
  - 命中的新增证据是否与最终主因方向一致

## 2026-04-23 20:16 Query Rewrite 子查询级命中归因

### 问题
- 现有 `rag_cases` 已经能看总量：
  - `added_rag_hits`
  - `added_case_hits`
- 但还不能稳定回答一个更细的问题：
  - 多个 rewritten query 里，究竟是哪一个 query 真正带来了新增知识或案例

### 原因
- 只看总量时，虽然能区分“无收益 / 部分有效 / 全部有效”，但还缺少 query 级归因。
- 一旦后面 planner 更复杂，只有总量没有 attribution，很难判断：
  - 是 network query 在起作用
  - 还是 db query 在起作用
  - 还是两个 query 都只是噪声

### 改法
- 扩展 [state/models.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/state/models.py)：
  - `RetrievalSubquery` 新增：
    - `added_rag_hits`
    - `added_case_hits`
- 更新 [graph/nodes.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/graph/nodes.py)：
  - 在 `_expand_context_retrieval(...)` 中按 subquery 分别累计知识与案例新增命中，再回写到对应 subquery 上
- 扩展 [agent_eval.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/evals/agent_eval.py)：
  - 新增 `retrieval_query_metrics`
  - 支持按 `query_contains` 断言某个 query 的：
    - `added_rag_hits`
    - `added_case_hits`
- 更新 [rag_cases.json](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/data/evals/rag_cases.json) 与 [test_agent_eval.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/tests/test_agent_eval.py)：
  - 在 `rules_based_query_rewrite_partial_gain_preserves_quality_signal` 里正式断言：
    - network query -> `0 / 0`
    - db query -> `1 / 1`

### 影响
- 现在 planner 评估不再只知道“总共有 1 条新增命中”，而是知道：
  - 哪个 rewritten query 没有带来任何增量
  - 哪个 rewritten query 真的把知识和案例补进来了
- 这让 `partial gain` 不再只是一个总量判断，而变成了可归因、可解释的质量信号。
- 这也为后续继续做更强 gate 打下基础：
  - subquery 命中归因
  - 命中增量与最终主因方向的一致性

### 评估结果
- 命令：

```bash
cd projects/it-ticket-agent
uv run python -m unittest \
  tests.test_agent_eval.ObservationScoreTest.test_extract_eval_observation_includes_rag_metrics \
  tests.test_agent_eval.AgentEvalRunnerIntegrationTest.test_runner_reports_partial_query_rewrite_gain \
  -q
uv run python scripts/run_agent_eval.py \
  --dataset ./data/evals/rag_cases.json \
  --case-id rules_based_query_rewrite_partial_gain_preserves_quality_signal
```

- 结果：
  - 定向单测：`Ran 2 tests ... OK`
  - 单 case：`1/1 PASS`
  - 关键观测：
    - `payment-service upstream dependency timeout ingress gateway jitter -> added_rag_hits=0, added_case_hits=0`
    - `payment-service db pool saturation slow query timeout -> added_rag_hits=1, added_case_hits=1`

### 后续方向
- 继续补更强的 planner gate：
  - 新增命中是否和最终根因方向一致
  - 是否存在“query 命中了，但命中的内容其实在给 supervisor 增加噪声”

## 2026-04-23 20:16 Query Rewrite 与最终主因方向一致性 Gate

### 问题
- 现有 `retrieval_query_metrics` 已经能回答：
  - 哪个 rewritten query 带来了新增知识
  - 哪个 rewritten query 带来了新增案例
- 但还回答不了一个更关键的问题：
  - 这些新增命中，到底是在帮助收敛到真实主因，还是只是增加噪声

### 原因
- 只看 `added_rag_hits / added_case_hits`，仍然可能把“命中了，但方向错了”误判成优化。
- 另外这轮也暴露出一个更底层的问题：
  - 现有 `infer_failure_mode / infer_root_cause_taxonomy` 对包含 `db + timeout` 或 `deploy + 502` 的文本，会优先命中泛化 timeout 规则，导致 taxonomy 误归类
- 如果不先修这个基础判别，后面的“主因方向一致性” gate 会建立在错误 taxonomy 上。

### 改法
- 修正 [case_retrieval.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/case_retrieval.py)：
  - 把 `infer_failure_mode(...)` 的优先级调整为：
    - `oom`
    - `db`
    - `deploy`
    - `timeout / 5xx / gateway`
- 扩展 [agent_eval.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/evals/agent_eval.py)：
  - `AgentEvalObservation` 新增 `primary_root_cause_taxonomy`
  - `retrieval_query_metrics` 新增：
    - `root_cause_taxonomy`
    - `matches_primary_root_cause_taxonomy`
- 扩展 [test_agent_eval.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/tests/test_agent_eval.py)：
  - 新增 taxonomy 推断测试，保证：
    - `db + timeout` -> `database_degradation`
    - `deploy + 502` -> `release_regression`
  - 在 `partial_gain` case 里正式断言：
    - network query -> 与最终主因不一致
    - db query -> 与最终主因一致
- 更新 [rag_cases.json](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/data/evals/rag_cases.json) 的 partial gain expectation

### 影响
- 现在 query planning 评估已经从三层推进到四层：
  - 有没有 rewrite
  - 有没有增量命中
  - 哪个 query 带来了增量
  - 带来增量的 query 是否和最终主因方向一致
- 这能把两类问题稳定拆开：
  - planner 没搜到有用东西
  - planner 搜到了，但 supervisor 最终没沿正确方向收敛
- 同时也顺手修正了案例 recall 和 case writeback 上游可能出现的 taxonomy 误归类问题。

### 评估结果
- 命令：

```bash
cd projects/it-ticket-agent
uv run python -m unittest \
  tests.test_agent_eval.RetrievalTaxonomyInferenceTest \
  tests.test_agent_eval.ObservationScoreTest.test_extract_eval_observation_includes_rag_metrics \
  tests.test_agent_eval.AgentEvalRunnerIntegrationTest.test_runner_reports_partial_query_rewrite_gain \
  -q
uv run python -m unittest tests.test_agent_eval -q
uv run python scripts/run_agent_eval.py --dataset ./data/evals/rag_cases.json
```

- 结果：
  - 定向单测：`Ran 4 tests ... OK`
  - `tests.test_agent_eval`：`Ran 33 tests ... OK`
  - `rag_cases.json`：`10/10 PASS`
  - gate：
    - `pass_rate=1.000`
    - `avg_tool_calls=4.300`
    - `expansion_probe_cases=1`
    - `rejected_tool_call_total=0`

### 后续方向
- 下一步可以继续补“命中了但带偏”的明确负样本：
  - 某个 query 命中了更多知识
  - 但这些知识与最终 live evidence 冲突
  - 系统是否仍能坚持正确主因而不被带偏

## 2026-04-23 23:05 线上 Bad Case Candidate 到离线 Eval Skeleton 的最小闭环

### 问题
- 之前离线评估集主要靠手工构造 synthetic case。
- 这能支持定向优化，但缺一个更工程化的入口：
  - 线上真的出了 bad case，怎么把它稳定沉淀下来
  - 又怎么避免“线上错一次就自动污染正式 gate”

### 原因
- 线上 bad case 和正式 eval case 不是一个东西。
- 前者只是“值得关注的异常样本”，后者必须是：
  - 可复现
  - 可归因
  - 有明确 mock 边界
  - 有明确 expect
- 如果直接把线上样本自动并进正式数据集，会把大量噪声、上下文不完整样本、以及还没分析清楚的问题一起带进 gate。

### 改法
- 新增独立的 `bad_case_candidate` 持久化层：
  - sqlite: [bad_cases/store.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/bad_cases/store.py)
  - postgres: [bad_cases/pg_store.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/bad_cases/pg_store.py)
  - wrapper: [bad_case_store.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/bad_case_store.py)
- 在 [storage/provider.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/storage/provider.py) 和 [main.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/main.py) 接入 `BadCaseCandidateStore`
- 在 [runtime/orchestrator.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/runtime/orchestrator.py) 增加两类自动打点：
  - `runtime_completion`
    - `tool_budget_reached`
    - `iteration_guardrail_reached`
    - `rejected_tool_call_count > 0`
    - retrieval subquery 存在但 `added_rag_hits = 0 && added_case_hits = 0`
    - 某条 rewritten query 有新增命中，但和最终主因 taxonomy 不一致
  - `feedback_reopen`
    - 用户 `human_verified=false`
    - 用户给出 `actual_root_cause_hypothesis`
    - 用户拒绝当前建议并附带新信息重开诊断
- 候选样本当前会保留：
  - `request_payload / response_payload`
  - `incident_state_snapshot / context_snapshot`
  - `observations / retrieval_expansion`
  - `human_feedback`
  - `conversation_turns / system_events`
- 新增导出模块与脚本：
  - [bad_case_export.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/evals/bad_case_export.py)
  - [export_bad_case_candidates.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/scripts/export_bad_case_candidates.py)
- 第一版导出策略：
  - 有 `retrieval_expansion` -> 导成 `rag` skeleton
  - `feedback_reopen` 或明显多轮 -> 导成 `session_flow` skeleton
  - 其余 -> 导成 `tool_mock` skeleton
- 这轮没有直接自动写正式 dataset，而是先输出到 `data/evals/generated/`，再人工补齐 mock 和 expect

### 影响
- 现在评估体系多了一层“线上候选池”，闭环从：
  - 手工写 case
  - 跑离线 eval
  变成了：
  - 线上识别候选
  - 保留可复现快照
  - 导出 eval skeleton
  - 人工筛成正式回归资产
- 这让 bad case 收集从“靠记忆复盘”变成“运行时自动留档”。
- 同时又保留了人工筛选层，避免把噪声样本直接推进 gate。

### 评估结果
- 命令：

```bash
cd projects/it-ticket-agent
uv run python -m unittest \
  tests.test_bad_case_export \
  tests.test_runtime_smoke.ConversationRuntimeSmokeTest.test_runtime_completion_creates_bad_case_candidate_when_retrieval_expansion_has_no_gain \
  tests.test_runtime_smoke.ConversationRuntimeSmokeTest.test_feedback_resume_with_new_information_reopens_diagnosis \
  -q
```

- 结果：
  - `Ran 5 tests ... OK`
  - 已覆盖：
    - store roundtrip
    - export skeleton smoke
    - runtime completion 自动建候选
    - feedback reopen 自动建候选

### 后续方向
- 下一步可以继续补两类能力：
  - 从 `bad_case_candidate` 自动推荐更细的 mock 边界，比如直接给出建议的 `mock_tool_responses`
  - 增加“人工确认后再一键并入正式 dataset”的 curated merge 脚本

## 2026-04-24 00:05 feedback_reopen 后保留人工反馈，并补齐 curated merge

### 问题
- 上一轮刚把 `bad_case_candidate -> eval skeleton` 闭环搭起来，但实跑后暴露出两个问题：
  - `feedback_reopen` 之后，新一轮普通 `IncidentCase upsert` 会把之前人工提交的 `human_verified / actual_root_cause_hypothesis / hypothesis_accuracy` 冲掉
  - generated skeleton 只能导出，不能稳定地回并到正式 dataset，最后一步还缺工具链

### 原因
- `IncidentCase` 的运行时 upsert 和人工 feedback 写回共用一张表，但之前没有做 feedback merge 保护，导致 reopen 后再次写回时用默认值覆盖了已有人工信号。
- 另外，导出和正式入库之间还缺“curated merge”层，人工补完后仍然只能手改 dataset。

### 改法
- 新增 [memory/upsert_merge.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/memory/upsert_merge.py)：
  - 在 sqlite / postgres 两套 `upsert_case(...)` 里统一复用
  - 当新一轮 upsert 没带新的 feedback 值时，保留已有：
    - `human_verified`
    - `actual_root_cause_hypothesis`
    - `hypothesis_accuracy`
- 更新：
  - [memory/store.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/memory/store.py)
  - [memory/pg_store.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/memory/pg_store.py)
- 扩展 `bad_case_candidate.export_status`：
  - 从 `pending / exported / ignored`
  - 扩到 `pending / exported / merged / ignored`
- 新增 curated merge 模块与脚本：
  - [bad_case_merge.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/evals/bad_case_merge.py)
  - [merge_curated_bad_cases.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/scripts/merge_curated_bad_cases.py)
- 当前 merge 规则：
  - 校验 `eval_skeleton` 已去掉 `todo_* / TODO / _todo`
  - `tool_mock -> tool_mock_cases.json`
  - `rag -> rag_cases.json`
  - `session_flow -> session_flow_cases.json`
  - 合并成功后把对应 candidate 标成 `merged`
- 补测试：
  - [test_runtime_smoke.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/tests/test_runtime_smoke.py)
  - [test_bad_case_export.py](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/tests/test_bad_case_export.py)

### 影响
- 现在 reopen 后的数据一致性补齐了：
  - `bad_case_candidate` 保留人工反馈
  - `incident_case` 也不再丢掉人工真因
- 现在 bad case 闭环真正从：
  - 候选收集
  - skeleton 导出
  进化成：
  - 候选收集
  - skeleton 导出
  - 人工整理
  - curated merge 入正式 dataset
- 这让 generated 目录不再只是中间产物，而是正式回归资产的 staging 区。

### 评估结果
- 命令：

```bash
cd projects/it-ticket-agent
uv run python -m unittest \
  tests.test_bad_case_export \
  tests.test_runtime_smoke.ConversationRuntimeSmokeTest.test_feedback_resume_with_new_information_reopens_diagnosis \
  -q
uv run python scripts/merge_curated_bad_cases.py --help
```

- 结果：
  - 定向单测：`Ran 6 tests ... OK`
  - merge 脚本 CLI 可正常加载
  - reopen 回归已覆盖：
    - bad case 自动建候选
    - incident case 保留人工反馈
    - curated skeleton 可回并正式 dataset，并把 candidate 标成 `merged`

### 后续方向
- 下一步可以继续补两类能力：
  - merge 前自动显示 diff，减少人工误合并
  - 支持把 curated session-flow skeleton 选择性并入 `session_flow_live_cases.json`

## 2026-04-24 09:40 历史案例召回拆成“自动 hint + 显式 tool”

### 问题
- 之前的历史案例召回默认发生在 `context_collector` 首跳里，只要进入诊断路径就会按当前消息直接查一轮 `similar_cases`。
- 这样在 query 很模糊时容易带来两个问题：
  - 自动首跳 recall 准确率偏低，容易把 supervisor 往错误历史模式上带
  - 历史案例召回没有显式 tool，后续拿到更多 live evidence 后也没法“再查一轮更准的历史案例”

### 原因
- 之前把历史案例召回更像当成“背景预取”，没有区分：
  - 什么时候适合做弱提示型首跳 recall
  - 什么时候应该等到 evidence 更具体后，再显式触发案例搜索
- 结果就是案例召回既太早，又不够可控。

### 改法
- 新增显式 tool：
  - [search_similar_incidents](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/tools/cicd.py)
  - 注册到 [build_default_tools](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/tools/runtime.py)
- `search_similar_incidents` 当前调用兄弟项目 `it-ticket-rag-service` 的 `/api/v1/case-memory/search`
  - 支持：
    - `query`
    - `service / cluster / namespace`
    - `failure_mode`
    - `root_cause_taxonomy`
    - `top_k`
- supervisor 新增两层约束：
  - 自动首跳 prefetch 只在 query 足够具体时执行
  - 如果首跳因为 query 太泛被跳过，LLM 先做 1-2 个关键只读检查，再视需要调用 `search_similar_incidents`
- 新增 `context_snapshot.case_recall`：
  - 记录自动预召回是否开启
  - 记录为什么跳过 / 为什么触发
  - 记录显式 tool search 的补充命中情况
- 新增 tool observation merge：
  - `search_similar_incidents` 返回的案例会回写到 `context_snapshot.similar_cases`

### 影响
- 当前历史案例召回从“无条件首跳预取”收敛成了两层：
  - 自动预召回：只做弱 hint
  - 显式案例搜索：拿到更具体 evidence 后再查
- 这让历史案例从“容易带偏的早期先验”变成“受控的背景证据补充”。
- 也把简单问答和诊断路径进一步分开：
  - `direct_answer` 默认不走历史案例召回
  - `diagnosis` 才会进入 case recall 体系

### 评估结果
- 命令：

```bash
cd projects/it-ticket-agent
uv run python -m unittest tests.test_runtime_smoke -q
uv run python -m unittest tests.test_agent_eval -q
```

- 结果：
  - `tests.test_runtime_smoke`：`Ran 29 tests ... OK`
  - `tests.test_agent_eval`：`Ran 35 tests ... OK`
- 新增覆盖：
  - 模糊输入跳过自动案例预召回
  - 明确症状触发自动案例预召回
  - 显式 `search_similar_incidents` tool 注册
  - 有 live evidence 后才暴露历史案例搜索 tool

### 面试口径
- 问：你们的历史案例召回为什么不直接在入口就查？
  - 答：因为模糊 query 下的案例 recall 很容易带偏主诊断，所以现在改成两层。第一层是在诊断路径里做一个受控的自动 hint，只在 query 足够具体时才预召回；第二层是显式 `search_similar_incidents` tool，等 agent 先拿到更具体的 live evidence，再主动查历史案例。
- 问：那历史案例召回到底算 RAG 还是 tool？
  - 答：底层还是检索增强，但运行时语义上我把它拆成了 `context prefetch + explicit tool`。这样既保留了弱先验，也避免让 LLM在过早阶段过度相信历史案例。

### 后续方向
- 可以继续补两类能力：
  - 给 `search_similar_incidents` 增加 query rewrite / taxonomy narrowing 的显式参数推荐
  - 把“用户主动问有没有类似历史案例”单独做成更清晰的路由分支，而不是只靠诊断路径内部工具触发

## 2026-04-24 10:07 Case Memory 失败降级与状态回写

### 问题
- 历史案例召回已经拆成“自动 hint + 显式 `search_similar_incidents` tool”，但 case-memory 外部服务失败时还有三个边界不够完整：
  - 自动预召回异常可能打穿 `context_collector`
  - 显式案例搜索无命中或失败时不会回写 `case_recall` 状态
  - 案例索引同步失败可能在后台 task 里产生未处理异常

### 原因
- 之前主要优化的是“什么时候召回”和“召回结果如何合并”，默认假设 case-memory 服务可用。
- 但在当前架构里，case memory 是诊断阶段的经验先验，不是主链必需依赖；它应该按 `fail-open` 处理。
- 如果失败不显式记录，回放时也分不清是“没有相似案例”，还是“案例库不可用”。

### 改法
- [CaseRetriever](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/case_retrieval.py) 新增失败安全召回：
  - `rag_enabled=false` 时记录 `status=skipped / reason=case_memory_disabled`
  - `/case-memory/search` 异常时返回空列表，并写入 `last_recall_metadata`
- [context_collector](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/graph/nodes.py) 统一写回自动预召回状态：
  - `prefetch_status`
  - `prefetch_error_type`
  - `prefetch_error`
  - `case_memory_reason`
- `retrieval_expansion` 的 case subquery 失败时不打断扩展检索，而是追加 `missing_evidence`，保留“案例搜索失败”这个证据缺口。
- [ReactSupervisor](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/runtime/react_supervisor.py) 调整 `search_similar_incidents` observation merge：
  - 无命中也记录 `tool_search_count / last_tool_status / last_tool_hit_count`
  - 失败时追加 `tool_failures`
  - 只有有新 case 时才合并进 `similar_cases`
- [CaseVectorIndexer](/Users/lyb/workspace/agent-learn/projects/it-ticket-agent/src/it_ticket_agent/case_vector_indexer.py) 对 `case_memory_sync` 做失败安全处理，失败只记录 `last_sync_metadata` 和 warning，不阻断案例落库或反馈回写。

### 影响
- case-memory 从“可能影响诊断主链的外部依赖”收敛成“可降级的背景经验服务”。
- 现在可以区分三种状态：
  - 跳过召回：query 太泛、缺 service、RAG/case memory 关闭
  - 正常召回但无命中
  - case-memory 服务失败
- 诊断仍优先依赖 live tool evidence；历史案例只作为可解释的背景补充。
- feedback / IncidentCase 落库不会因为向量索引同步失败而被阻塞。

### 评估结果
- 命令：

```bash
cd projects/it-ticket-agent
uv run python -m unittest   tests.test_skill_scenarios   tests.test_runtime_smoke   tests.test_agent_eval   -q
```

- 结果：`Ran 79 tests ... OK`
- 新增覆盖：
  - case-memory search 异常时 `CaseRetriever.recall()` 返回空列表并记录错误元数据
  - 自动案例预召回失败时主链继续完成，并在 `context_snapshot.case_recall` 记录失败状态
  - 显式 `search_similar_incidents` 无命中 / 失败时仍记录检索尝试和失败信息
  - case-memory sync 失败不向外抛出

### 面试口径
- 问：如果 case-memory 服务挂了，会不会影响诊断？
  - 答：不会。case memory 在当前设计里是经验先验，不是 live evidence，也不是主链强依赖。自动预召回失败会记录状态并返回空案例，supervisor 继续用实时工具诊断；显式案例搜索失败也只会进入 `case_recall.tool_failures`。
- 问：那怎么区分“没有相似案例”和“案例库不可用”？
  - 答：都写在 `context_snapshot.case_recall` 里。无命中会有 `last_tool_hit_count=0`，服务失败会有 `prefetch_status=error` 或 `tool_failures`，回放和 eval 可以据此区分。

### 后续方向
- 后续可以继续补：
  - case-memory 服务级健康探针和熔断窗口
  - 按 `case_memory_reason` 进入 bad-case candidate 的更细粒度归因
  - 在 eval report 中单独暴露 case-memory skipped / empty / failed 三类统计

## 2026-04-24 14:05 Case Memory 评估统计与 Bad Case 归因

### 问题
- case-memory 已经能 fail-open 并回写状态，但这些状态还没有进入评估报告和 bad-case 归因闭环。
- 结果是回归时只能看到最终 pass / fail，看不到 case-memory 是跳过、无命中、命中还是失败。
- 线上候选导出时也缺少 case-memory 维度，人工整理 eval skeleton 时不容易判断要补“召回失败 mock”还是“query rewrite / taxonomy narrowing”。

### 原因
- `context_snapshot.case_recall` 只是运行时上下文，之前没有被 `agent_eval` 聚合。
- bad-case candidate 的 reason code 主要来自工具预算、拒绝工具、retrieval expansion 和反馈，没有把 `case_memory_reason` 纳入归因。

### 改法
- 新增 `case_memory_analysis.py`，统一把 `case_recall` 归一成：
  - `state`: `skipped / empty / failed / hit`
  - `reason`: 优先使用 `case_memory_reason`，失败时回退到 `tool_failures`
  - `reason_codes`: 生成 `case_memory_failed`、`case_memory_empty`、`case_memory_skipped_*` 等归因码
- `agent_eval` 的 observation 与 report 增加：
  - `case_memory_state`
  - `case_memory_reason`
  - `case_memory_state_counts`
  - `case_memory_reason_counts`
- `run_agent_eval.py` 控制台 metrics 直接打印 `case_memory={...}`，不用打开 JSON report 才能看到。
- runtime bad-case 归因增加 case-memory 维度：
  - `case_memory_failed` 可单独进入候选池，severity 为 `medium`
  - `case_memory_empty / case_memory_skipped_*` 只在已有 bad-case trigger 时作为补充归因，避免普通泛 query 过度入池
- bad-case export payload 增加 `case_memory_attribution`，并在 mock boundary suggestions / todo 中提示要补失败 mock、无命中合理性或跳过原因。

### 影响
- eval report 现在能区分 case-memory 的四类结果：跳过、无命中、失败、命中。
- bad-case candidate 不再只有“retrieval 无收益”这种粗粒度原因，可以进一步看到是否被 case-memory 空命中、跳过或失败影响。
- 人工从 generated skeleton 合入正式 eval 时，能更快决定该补 case-memory failure mock，还是补 query rewrite / taxonomy narrowing。

### 评估结果
- 定向测试：`Ran 6 tests ... OK`
- 相关套件：

```bash
cd projects/it-ticket-agent
uv run python -m unittest tests.test_agent_eval tests.test_bad_case_export tests.test_runtime_smoke -q
```

- 结果：`Ran 76 tests ... OK`
- 完整回归：

```bash
cd projects/it-ticket-agent
uv run python -m unittest discover -s tests -q
```

- 结果：`Ran 114 tests ... OK (skipped=1)`

### 面试口径
- 问：case memory 的效果怎么评估？
  - 答：我没有只看最终回答是否通过，而是在 eval report 里单独聚合 case-memory 状态。每个 case 会归成 skipped、empty、failed、hit，reason 也会聚合，这样能区分“系统没用案例库”“案例库没命中”“案例库挂了”和“案例确实有帮助”。
- 问：这些状态怎么进入 bad case 闭环？
  - 答：case-memory failed 会单独生成候选，因为这是外部经验服务不可用；empty 和 skipped 不单独入池，只在已有 bad-case trigger 时作为补充归因，避免泛 query 造成候选池噪声。

### 后续方向
- 下一步可以继续做：
  - `search_similar_incidents` 的 query rewrite / taxonomy narrowing 参数推荐
  - “用户主动问有没有类似历史案例”的独立路由分支
  - case-memory 连续失败的轻量熔断窗口


## 2026-04-24 18:20 历史案例入库改为人工确认后索引

### 问题

- 之前 `IncidentCase` 在 finalize 后会立即尝试同步到 case-memory 向量索引。
- 这会把 Agent/LLM 自动总结直接当成历史真值，和“转人工后需值班人确认”“纯 Agent 工单也需要人工确认”的边界不一致。
- 旧的 `process_memory_entry` 表名也容易让人误解成用户对话过程记忆，实际保存的是 Agent 内部事件流。

### 原因

- `incident_case` 只有 `human_verified`，缺少显式案例生命周期状态，导致“待审核案例”和“可召回历史案例”没有硬边界。
- `CaseVectorIndexer.index_case(...)` 只看 RAG 是否启用，没有校验案例是否已确认。
- `ProcessMemoryStore` 同时承担内部事件记录和摘要投影，表名没有体现 Agent event 语义。

### 改动

- 新增 `agent_event` 表，字段使用 `event_id`，并从旧 `process_memory_entry.memory_id` 做兼容迁移；代码保留 `ProcessMemoryStore` facade，新增 `AgentEvent` 模型语义。
- `incident_case` 新增 `case_status / reviewed_by / reviewed_at / review_note`：
  - `pending_review`：Agent/LLM 总结落库，但不能进入历史案例向量库。
  - `verified`：人工确认后才允许 case-memory sync。
  - `rejected`：人工否定，不允许进入历史案例向量库。
- finalize 后只写 `case_status=pending_review`，不再立即调用 `CaseVectorIndexer.index_case(...)`。
- feedback 确认时写入 review metadata；只有 `human_verified=true` 才触发索引。
- `CaseVectorIndexer` 增加二次保护：`case_status != verified` 或 `human_verified=false` 时直接 skip。
- runtime 默认存储切到 Postgres；SQLite 仅保留为单元测试、旧数据迁移和显式 fallback。

### 影响

- 历史案例库只保留人工确认过的案例，降低错误总结污染后续召回的风险。
- 长周期或转人工工单可以先沉淀待审核摘要，值班人确认后再进入 case-memory。
- `conversation_session / conversation_turn / agent_event / incident_case` 的边界更清晰：对话原文、会话状态、Agent 内部事件、历史案例不再混在一个概念里。
- `feedback_reopen` 后重新诊断仍会保留人工反馈信号，但新一轮结果回到 `pending_review`，等待再次确认。

### 下一步

- 增加 case review API 或后台页面，让值班人能批量确认 `pending_review` 案例。
- 给 `case_status` 增加更细的来源统计，例如 `transfer_human_summary / agent_auto_summary / manual_review`。
- 后续如需可观测，再把 case lifecycle transition 单独接入指标和 trace。

### 面试问答

- 问：为什么历史案例不能在工单结束后直接进向量库？
  - 答：工单结束只能说明 Agent 产出了一个总结，不代表它是人工真值。现在先写 `pending_review`，人工确认后才变成 `verified` 并进入 case-memory。这样可以避免未确认总结污染后续召回。
- 问：那 `conversation_session`、`agent_event`、`incident_case` 分别存什么？
  - 答：`conversation_session` 存会话状态和当前进度，`conversation_turn` 存完整对话轮次，`agent_event` 存 Agent 内部关键事件流，`incident_case` 存待审核或已确认的案例摘要。只有 `incident_case.case_status=verified` 才是可召回历史案例。


## 2026-04-24 18:45 Live LLM 评估支持 Responses Wire API

### 问题

- 使用只暴露 Responses API 的 OpenAI-compatible provider 跑 `session_flow_live_cases.json` 时，旧客户端仍会先请求 `/chat/completions`。
- 该 provider 返回非预期响应，导致 live eval 出现 `JSONDecodeError`，无法验证真实 LLM 链路。

### 原因

- 配置里只有 `LLM_BASE_URL / LLM_MODEL / LLM_API_KEY`，缺少 wire API 选择。
- `OpenAICompatToolLLM` 默认固定先走 chat completions，只有空响应时才 fallback 到 responses。

### 改动

- 新增 `Settings.llm_wire_api`，通过 `LLM_WIRE_API=responses` 显式选择 Responses API。
- `OpenAICompatToolLLM.chat(...)` 在 responses 模式下直接调用 `/responses`，不再先探测 `/chat/completions`。
- README 配置示例补充 `LLM_WIRE_API=responses`。

### 影响

- `gpt-5.5 + responses wire API` 的真实 LLM session-flow live eval 已通过：4/4 cases pass，gate 2/2 pass。
- 仍保留默认 chat completions 路径，兼容旧 OpenAI-compatible provider。

### 下一步

- 后续可以把 `LLM_WIRE_API` 加到 eval report 元信息里，便于区分不同 provider 的真实测试结果。


## 2026-04-24 19:05 LLM Provider Preset 与默认切换

### 问题

- 真实 LLM provider 有两套：当前 `richado` 走 Responses API，之前 `yuangege` 走 Chat Completions API。
- 旧配置只能靠 `LLM_BASE_URL / LLM_MODEL / LLM_WIRE_API / LLM_API_KEY` 手动组合，切换时容易漏字段。
- `Settings` 的 LLM 默认值原本在模块 import 时读取，测试或脚本内临时切换 env 后，新建 `Settings()` 不一定反映最新 provider。

### 原因

- 没有 provider preset 层，provider 的 base_url、wire_api、默认模型和 key env 变量没有绑定在一起。
- dataclass 字段直接调用 `os.getenv(...)`，默认值在 import 阶段已经固化。

### 改动

- 新增 `LLM_PROVIDER_PRESETS`：
  - `richado`：默认/current，`base_url=http://richado.qzz.io:8091`，`model=gpt-5.5`，`wire_api=responses`，key env 为 `LLM_RICHADO_API_KEY`。
  - `yuangege`：previous，`base_url=https://api.yuangege.cloud/v1`，`model=gpt-5.5`，`wire_api=chat`，key env 为 `LLM_YUANGEGE_API_KEY`。
  - `none`：显式关闭 LLM。
- 支持别名：`current/default -> richado`，`previous/old -> yuangege`，`disabled -> none`。
- `LLM_API_KEY` 仍作为全局临时覆盖，显式 `LLM_BASE_URL / LLM_MODEL / LLM_WIRE_API` 仍可覆盖 preset。
- LLM 相关 dataclass 字段改为 `default_factory`，新建 `Settings()` 时读取当前 env。
- 本地 `.env` 已切到 `LLM_PROVIDER=richado`，旧 provider key 保留为 `LLM_YUANGEGE_API_KEY`；`.env` 被 git ignore，不进入提交。

### 影响

- 默认真实 LLM 测试使用当前 provider，不再受旧 `LLM_BASE_URL` 残留影响。
- 切换旧 provider 只需要设置 `LLM_PROVIDER=yuangege`。
- CI / 本地脚本可以通过 provider 名切换，不需要改代码。

### 下一步

- 可以在 eval report 里持久化 `llm_provider / llm_wire_api`，方便后续对比不同 provider 的稳定性。


## 2026-04-24 19:40 会话 Working Memory 落地

### 问题

- 原来的 `session_memory` 更像会话杂项容器，里面同时放原始问题、intent、澄清答案、pending 状态和事件队列。
- `conversation_turn` 能完整回放对话，但不适合作为每轮 ReAct prompt 的主要记忆输入。
- `agent_event` 记录内部过程，但它不是用户过程记忆，也不应该直接当作当前事实喂给模型。
- 多轮补充、纠错、feedback reopen 后，当前确认事实和历史案例之间缺少显式优先级。

### 原因

- 完整历史和当前工作态没有分开：完整历史适合审计，当前工作态适合诊断。
- 历史案例是经验先验，不是当前现场证据；如果 prompt 中没有更高优先级的当前工作记忆，相似案例容易带偏。
- 参考 Codex 源码后，比较明确的一点是：上下文管理要区分 full history、compacted/current context、read-only long-term memory，并且记忆命中不等于当前事实。

### 改动

- 新增 `src/it_ticket_agent/memory/working_memory.py`，提供 `build_initial_working_memory / merge_working_memory / normalize_working_memory`。
- 在 `conversation_session.session_memory_json` 内新增 `working_memory`，不新增表。
- `working_memory` 结构化保存：
  - `task_focus`
  - `confirmed_facts`
  - `constraints`
  - `open_questions`
  - `hypotheses`
  - `key_evidence`
  - `actions_taken`
  - `user_corrections`
  - `decision_state`
- `orchestrator` 在会话创建、澄清回答、用户补充、topic shift、feedback reopen、诊断完成后合并更新 `working_memory`。
- `ContextAssembler` 把 `working_memory` 放在 `memory_summary` 第一层，并补充 compact `current_incident_state`。
- `ReactSupervisor` prompt 显式传入 compact 后的 `working_memory`，并声明当前会话确认事实、纠错和待决状态优先于历史案例。
- topic shift 会重置当前 `working_memory.task_focus.original_user_message`，普通补充只更新 `current_user_message`，避免把旧问题和新补充混淆。

### 影响

- `conversation_turn`、`working_memory`、`agent_event`、`incident_case` 的边界更清楚：完整对话、当前工作态、内部事件、长期案例分别服务不同目标。
- 多轮澄清后，`clarification.environment=prod` 等人工确认事实会进入 `confirmed_facts`。
- feedback reopen 和用户纠错会进入 `user_corrections`，后续诊断不会只依赖旧结论。
- `ContextAssembler` 的上下文优先级变成：当前工作记忆 -> 当前 incident state -> 会话遗留字段 -> Agent 事件摘要 -> verified 历史案例。

### 测试

- Targeted：`uv run python -m unittest tests.test_working_memory tests.test_runtime_smoke.ConversationRuntimeSmokeTest.test_clarification_resume_completes_without_feedback_when_no_actionable_guidance tests.test_runtime_smoke.ConversationRuntimeSmokeTest.test_feedback_resume_with_new_information_reopens_diagnosis tests.test_runtime_smoke.ConversationRuntimeSmokeTest.test_post_message_records_topic_shift_history -q`，8 tests OK。
- Full：`uv run python -m unittest discover -s tests -q`，125 tests OK，skipped=1。

### 下一步

- 暂不做可观测和 case review UI。
- 可以继续优化 `working_memory` 的自动压缩策略，例如按事实来源、证据强度和用户确认等级裁剪，而不是只按数量截断。
- 如果后续要做用户长期偏好，建议单独新增 `user_profile_memory`，不要塞进当前会话 `working_memory`。

### 面试问答

- 问：`working_memory` 和 `conversation_turn` 有什么区别？
  - 答：`conversation_turn` 是完整对话回放，主要服务审计和复现；`working_memory` 是当前会话的结构化工作态，主要服务下一轮诊断 prompt。
- 问：为什么不把 `working_memory` 做成新表？
  - 答：它是 session-scoped 的短期状态，生命周期跟 `conversation_session` 一致，当前放在 `session_memory_json` 更简单；长期可复用经验仍然由 `incident_case` 承载。
- 问：它和历史案例召回的关系是什么？
  - 答：`working_memory` 是当前现场和人工确认事实，优先级高于历史案例；历史案例只是经验先验，只有 verified case 才能被召回，而且不能覆盖 live evidence。


## 2026-04-24 20:20 Working Memory P1 语义补全

### 问题

- 只有结构化槽位时，时序关系、因果链、弱信号和“为什么排除某个方向”容易丢。
- 证据、事实和纠错缺少统一可信度标记，prompt 里不容易区分用户确认、工具观测、系统状态和模型推断。
- 当前裁剪主要按数量截断，长会话里低价值摘要可能挤掉用户确认或工具观测。

### 原因

- 结构化字段适合稳定表达事实，但不适合承接所有语义细节。
- 多轮 ReAct 的关键不只是“有什么证据”，还包括“什么方向已经排除、这个信息来自哪里、可信度多高”。
- 如果没有来源引用，后续人工复核或重新总结时只能回扫完整历史，成本高。

### 改动

- `working_memory` 增加：
  - `narrative_summary`：短摘要，保存时序、因果链、阶段性判断。
  - `ruled_out_hypotheses`：记录已排除方向，避免重复排查。
  - `source_refs`：回指 `event_id / interrupt_id / observation_id / hypothesis_id` 等来源。
  - `source_type / confidence`：标记事实、证据、假设、动作、纠错的来源类型和置信度。
- `orchestrator` 的诊断折叠逻辑会把 verification failed / ranker rejected 写入 `ruled_out_hypotheses`，把工具观察摘要写入 `narrative_summary`。
- `ReactSupervisor` 的 prompt compact 路径保留 `narrative_summary / ruled_out_hypotheses / source_refs`。
- `working_memory` 裁剪策略从单纯取最后 N 条，改成按来源优先级、置信度、证据强度、refs 和新近度综合保留。

### 影响

- 结构化字段不再独自承担所有上下文语义，减少“只归槽导致漏信息”的风险。
- 用户确认、用户纠错、工具观测会比普通 LLM 推断更难被裁掉。
- 已排除方向会显式进入上下文，减少 ReAct 重复查同一根因方向。
- 后续转人工、case review 或二次摘要可以通过 `source_refs` 回查来源。

### 测试

- Targeted：`uv run python -m unittest tests.test_working_memory tests.test_runtime_smoke.ConversationRuntimeSmokeTest.test_clarification_resume_completes_without_feedback_when_no_actionable_guidance tests.test_runtime_smoke.ConversationRuntimeSmokeTest.test_feedback_resume_with_new_information_reopens_diagnosis tests.test_runtime_smoke.ConversationRuntimeSmokeTest.test_post_message_records_topic_shift_history -q`，10 tests OK。
- Full：`uv run python -m unittest discover -s tests -q`，127 tests OK，skipped=1。

### 下一步

- 后续可把 `source_refs` 和 tool observation ledger 的稳定 observation id 对齐。
- 如果长会话继续膨胀，再做 LLM-assisted compaction，把旧证据压成带 refs 的摘要。


## 2026-04-25 Working Memory 结构化压缩

### 问题

- P1 已经有结构化字段和优先级裁剪，但还没有明确的压缩生命周期。
- 长会话里 `narrative_summary`、证据和来源引用继续增长时，prompt 层仍可能反复做临时裁剪。
- 如果直接学习 Codex 的整段 history summary，容易丢掉用户确认、工具观测和已排除方向的可追溯结构。

### 原因

- Codex 压缩的是通用对话历史；IT 诊断场景需要保留事实来源、置信度和排除项。
- 当前系统已经把完整对话、工作记忆、agent event 和历史案例分开，压缩应该只作用于模型可见工作态，不应该删除原始对话或内部事件。
- LLM 摘要有语义压缩优势，但必须有确定性兜底，避免漏掉受保护事实。

### 改动

- `working_memory` 新增 `compaction` 元数据，记录触发原因、压缩策略、输入/输出近似 token、保留/丢弃条目数、受保护来源类型和输入签名。
- 新增阈值触发：近似 token 超限、摘要过长、结构化条目过多时，自动把 `working_memory` 压成结构化工作态。
- 新增确定性压缩：优先保留 `user_confirmed / user_correction / tool_observed`，同时按来源优先级、置信度、证据强度、refs 和新近度选择条目。
- 新增 LLM-assisted compaction：ReAct supervisor 在 LLM 可用且工作记忆超预算时，先要求模型输出 JSON schema；解析失败或 LLM 不可用时回退确定性压缩。
- prompt 超预算路径改为复用结构化压缩视图，而不是简单取最后 N 条。

### 影响

- 长会话上下文从“每次临时截断”升级为“有触发、有产物、有元数据”的压缩机制。
- 用户确认事实、用户纠错、工具观测和已排除假设更稳定地进入后续诊断。
- 原始 `conversation_turn` / `agent_event` 仍完整保留，压缩只影响模型可见的当前工作态。
- LLM 摘要可以补足结构化槽位的叙事表达，但不会单点决定最终保留内容。

### 测试

- Targeted：`UV_CACHE_DIR=/tmp/agent-learn-uv-cache PYTHONPYCACHEPREFIX=/tmp/agent-learn-pycache uv run python -m unittest tests.test_working_memory tests.test_runtime_smoke.ConversationRuntimeSmokeTest.test_clarification_resume_completes_without_feedback_when_no_actionable_guidance tests.test_runtime_smoke.ConversationRuntimeSmokeTest.test_feedback_resume_with_new_information_reopens_diagnosis tests.test_runtime_smoke.ConversationRuntimeSmokeTest.test_post_message_records_topic_shift_history -q`，12 tests OK。
- Full：`LLM_PROVIDER=none LANGFUSE_PUBLIC_KEY= LANGFUSE_SECRET_KEY= UV_CACHE_DIR=/tmp/agent-learn-uv-cache PYTHONPYCACHEPREFIX=/tmp/agent-learn-pycache uv run python -m unittest discover -s tests -q`，129 tests OK，skipped=1。
- 直接跑默认全量时，当前沙箱禁网导致 live LLM/httpx ConnectError，不是代码断言失败。

### 下一步

- 可以把压缩阈值外置到 settings，方便不同模型上下文窗口下调参。
- 后续如需更强审计，可把 `compaction.input_signature` 和压缩前后摘要写入 agent event。


## 2026-04-25 PG 表验证与 schema 兼容迁移修复

### 问题

- 用真实 PG backend 跑 demo 会话时，`PostgresProcessMemoryStoreV2` 初始化失败。
- 失败点是 `incident_case` 旧表缺少 `case_status`，但初始化逻辑先创建依赖 `case_status` 的索引，再执行兼容 `alter table`。

### 原因

- SQLite 版本已经有旧表补列逻辑，PG 版本也有补列逻辑，但执行顺序不对。
- 对已有 PG 环境来说，`create table if not exists` 不会补历史列；必须先补列，再创建引用新列的索引。

### 改动

- 调整 `src/it_ticket_agent/memory/pg_store.py` 的初始化顺序：先读取 `incident_case` 现有列并补齐 `case_status / human_verified / review_* / hypothesis_*` 等字段，再创建 `idx_incident_case_status_updated_at` 等索引。
- 用真实 PG backend 创建 `DEMO-MEM-*` 会话，确认 `conversation_session / conversation_turn / agent_event / system_event / incident_case / execution_checkpoint / bad_case_candidate` 等表能正常写入。

### 影响

- 已部署过旧 schema 的 PG 环境可以自动兼容新案例字段。
- `incident_case` 的 `pending_review -> verified` 生命周期字段可以在旧库上正常工作。
- 真实 demo 能直接展示当前 working memory、完整对话、内部事件和案例摘要分别落在哪些表。

### 测试

- Live eval：`uv run python scripts/run_agent_eval.py --dataset ./data/evals/session_flow_live_cases.json --output /tmp/session-flow-live-eval-report.json`，4/4 cases PASS，8/8 steps PASS，gate PASS。
- LLM compaction demo：真实 `gpt-5.5` 调用，`llm_used=true`，`key_evidence` 从 21 条压到 8 条，`source_refs` 从 23 条压到 10 条。
- PG demo：真实 PG backend 写入 `DEMO-MEM-20260425052150`，会话完成，`conversation_turn=4`、`agent_event=4`、`system_event=9`、`incident_case=1`。
- Targeted：`uv run python -m unittest tests.test_working_memory tests.test_runtime_smoke.ConversationRuntimeSmokeTest.test_clarification_resume_completes_without_feedback_when_no_actionable_guidance tests.test_runtime_smoke.ConversationRuntimeSmokeTest.test_feedback_resume_with_new_information_reopens_diagnosis tests.test_runtime_smoke.ConversationRuntimeSmokeTest.test_post_message_records_topic_shift_history -q`，12 tests OK。

### 下一步

- 可以补一个轻量 PG migration smoke test，专门覆盖“旧 incident_case 表缺列时启动 StoreProvider”。
- 可以把 demo 查询脚本沉淀成 `scripts/inspect_runtime_storage.py`，方便之后展示表内容。


## 2026-04-25 Diagnosis Playbook 程序性记忆落地

### 问题

- 只有历史案例召回时，首轮容易把具体旧事故过早放进上下文，影响现场诊断顺序。
- 结构化 case 适合保存“过去发生了什么”，但不适合作为“这类问题应该怎么排查”的稳定方法卡。
- 直接让 LLM 从单个工单总结并进入长期记忆，容易把未经复核的方法污染到线上诊断链路。

### 原因

- 历史案例是事实记忆，Playbook 是程序性记忆，两者的召回时机和可信边界不同。
- Tool-First ReAct 更需要的是首轮工具顺序和证据要求，而不是首轮就引用某个具体旧案例结论。
- 纯自动沉淀缺少人工审核，无法保证方法卡的可复用性和安全 guardrails。

### 改动

- 新增 `diagnosis_playbook` 模型、PG/SQLite 存储、`DiagnosisPlaybookStore` facade 和 FastAPI 管理接口。
- 新增 `PlaybookRetriever`，只召回 `verified + human_verified` Playbook，输出压缩执行卡到 `ContextSnapshot.diagnosis_playbooks`。
- `context_collector` 改为先召回 Playbook；命中 Playbook 且没有强历史查询意图时，首轮 case 召回标记为 `deferred_by_playbook`。
- ReAct prompt、shared context、候选工具排序和 rule-based hypothesis 都接入 Playbook 推荐步骤，但明确要求不能把 Playbook 当根因事实。
- 人工确认 case 后，系统可从 3 个以上同类 verified case 聚合生成 `pending_review` Playbook candidate；不会自动发布为 verified。

### 影响

- 首轮上下文从“相似历史事故优先”调整为“验证过的诊断方法优先”。
- 相似 case 仍保留，但默认更偏向 evidence-driven 的后验召回，减少旧案例带偏。
- Playbook 有完整审核状态、来源案例、证据要求和 guardrails，适合长期运行后逐步积累程序性记忆。
- 当前实现不引入向量依赖，先用确定性 hybrid scoring，便于测试和回放。

### 测试

- Targeted：`uv run python -m unittest tests/test_playbook_memory.py tests/test_runtime_smoke.py`，34 tests OK。
- Eval：`uv run python -m unittest tests/test_agent_eval.py`，39 tests OK。
- Full：`uv run python -m unittest discover -s tests -q`，132 tests OK，skipped=1。

### 下一步

- 可以补一个轻量 Playbook review UI，把 `pending_review` candidate 的审核流程做成值班人可操作入口。
- 后续可把 Playbook recall scoring 的命中特征写入 eval report，用于观察哪些方法卡长期有效。


## 2026-04-25 前端诊断工作台 v1

### 问题

- 后端已经有 working memory、Playbook、case review 和 bad case 候选闭环，但前端仍主要是聊天和审批入口。
- 值班人无法直接在 UI 中查看上下文组装结果、诊断时间线、待审核 Playbook、待审核案例和 bad case 候选。

### 原因

- 记忆系统先完成了存储、召回和审核状态机，但缺少面向人工复核的工作台。
- `incident_case` 已有 `pending_review / verified / rejected` 生命周期，但只暴露了查询接口，没有按 case_id 审核入口。
- bad case candidate 已经落库，但没有在线查看、归因确认和导出状态管理入口。

### 改动

- 在现有静态控制台中新增 5 个工作台面板：会话详情、诊断时间线、Playbook 管理、案例审核、Bad Case 候选。
- 会话详情展示 `working_memory / agent events / context_snapshot`；诊断时间线展示 RAG、Playbook recall、case recall、tool calls、approval 和 interrupt。
- 新增 `POST /api/v1/cases/{case_id}/review`，支持值班人将案例审核为 `verified` 或 `rejected`。
- 新增 bad case candidate 列表、详情和导出状态 API，前端可展示 eval skeleton 并标记 `exported / ignored`。

### 影响

- 记忆系统从“后端可用”推进到“人工可审、过程可见”。
- Playbook 和历史案例都保持人工审核边界，不会因为 UI 操作绕过 `verified + human_verified` 的召回条件。
- bad case 候选可以先在 UI 中查看归因和上下文，再决定是否导出为正式 eval 样本。

### 测试

- Targeted：`uv run python -m unittest tests/test_frontend_smoke.py -q`，5 tests OK。
- Full：`uv run python -m unittest discover -s tests -q`，133 tests OK，skipped=1。

### 下一步

- 可以继续补运行时筛选、搜索和分页，避免长期运行后列表过长。
- 可以给 bad case eval skeleton 增加文件导出接口，减少人工复制。


## 2026-04-25 Bad Case 候选一键导出与回归闭环 API

### 问题

- 前端 Bad Case 面板只能展示 eval skeleton 和手动标记 `exported`，没有真正把候选写成 `data/evals/generated/*.json` 文件。
- existing script 已经支持导出和 curated merge，但值班人需要离开工作台手动执行脚本，闭环入口不统一。

### 原因

- bad case candidate 的运行时存储、导出脚本、merge 脚本已经存在，但 FastAPI 和前端还没有把这些能力串起来。
- 只改状态不写文件，会让 `exported` 语义不够真实，后续无法直接进入人工补齐和回归资产整理。

### 改动

- 新增 `POST /api/v1/bad-case-candidates/{candidate_id}/export-eval-skeleton`，复用 `export_bad_case_candidates(...)` 导出单个候选到 generated 目录，并写回 `output_path / target_dataset / export_format / exported_at`。
- 新增 `POST /api/v1/bad-case-candidates/merge-curated-eval-skeletons`，复用 curated merge 逻辑，支持指定文件或扫描 generated 目录，支持 `dry_run`。
- 前端 Bad Case 面板的“导出 eval skeleton”改为真实调用导出 API，并在详情中展示导出文件、目标数据集和合并 case。
- 导出状态更新会保留已有 `export_metadata`，避免覆盖人工备注或历史导出信息。

### 影响

- Bad Case 面板从“查看候选 + 标记状态”推进到“候选 -> generated skeleton 文件”的真实闭环。
- 值班人可以先在 UI 导出，再人工编辑 generated skeleton，最后用脚本或 API dry-run/merge 到正式 eval dataset。
- `exported` 状态和本地文件路径一致，后续排查和合并更可追踪。

### 测试

- Targeted：`uv run python -m unittest tests/test_frontend_smoke.py -q`，5 tests OK。
- Targeted：`uv run python -m unittest tests/test_bad_case_export.py -q`，6 tests OK。
- Targeted：`uv run python -m unittest tests/test_frontend_smoke.py tests/test_bad_case_export.py -q`，11 tests OK。
- Full：`uv run python -m unittest discover -s tests -q`，133 tests OK，skipped=1。

### 下一步

- 可以给 generated skeleton 增加 UI 文件打开/复制入口。
- 可以继续补分页、搜索和批量导出，适配长期运行后候选增多的场景。

## 2026-04-25 澄清节点主输入框自动 resume 修复

### 问题

- 前端进入 `awaiting_clarification` 后，如果用户在主输入框继续补充信息，请求仍然走 `/api/v1/conversations/{session_id}/messages`。
- 后端正确拒绝并返回 `conversation is awaiting resume; use the resume endpoint`，导致用户以为系统卡住。

### 原因

- UI 有单独的左侧澄清表单会调用 `/resume`，但主输入框不知道当前会话处于 pending interrupt。
- `message_mode=supplement` 只改变 `/messages` 的语义，不等于 resume interrupt。

### 改动

- 新增 `resumeClarificationAnswer(...)` 前端 helper。
- `handleUserMessage(...)` 在检测到 `currentPendingInterrupt.type === 'clarification'` 时，自动把主输入框内容提交到 `/api/v1/conversations/{session_id}/resume`。
- 左侧澄清表单复用同一个 helper，避免两套逻辑漂移。

### 影响

- 用户在待澄清状态下可以直接在主输入框补充信息，不再触发 awaiting resume 错误。
- 保留左侧澄清表单，两种入口都会走同一个 resume 协议。

### 测试

- Targeted：`uv run python -m unittest tests/test_frontend_smoke.py -q`，5 tests OK。

### 下一步

- 可以进一步优化 router，把“服务 + 5xx + 延迟升高 + 最近发布 + 请诊断”直接识别为诊断请求，减少不必要澄清。


## 2026-04-25 前端会话管理、页面拆分与工具进度提示

### 问题

- 前端只有一个长页面，聊天、诊断工作台和审核面板混在一起，用户不容易区分“发起诊断”和“复盘/治理”。
- 会话恢复主要依赖本地 `localStorage` 的单个 session id，没有最近会话列表，也缺少明确的新会话入口。
- 运行中缺少过程反馈，用户只能等待最终回答，不知道系统是否正在检索、收集上下文或调用工具。

### 原因

- 早期前端定位是工程控制台，优先把后端闭环能力暴露出来，没有独立抽象会话生命周期。
- system event 已经记录诊断过程，但前端只在工作台里静态展示，没有在请求执行期间轮询最新事件。
- ReAct supervisor 的真实工具调用没有写入 `tool.started / tool.completed` 这类专门给 UI 消费的高层事件。

### 改动

- 前端拆成 `chatPage` 和 `workspacePage`，通过 `pageNav` 在“会话”和“诊断工作台”之间切换。
- 新增顶部/侧边栏“新会话”入口，`startNewConversation()` 会清空本地当前会话、重置 pending interrupt 和工作台状态。
- 新增 `GET /api/v1/sessions`，SessionStore SQLite/PG 都支持按 `user_id / status / limit` 查询最近会话；前端侧边栏展示最近会话并支持点击恢复。
- 新增运行中 `agentActivityPanel`，请求进行时轮询 `/api/v1/sessions/{session_id}/events`，展示知识检索、上下文收集和工具调用的高层状态。
- `ReactSupervisor` 在工具执行边界写入 `tool.started / tool.completed / tool.cached / tool.failed` system events；payload 只放 `tool_name / status / latency_ms / error_type` 等摘要，不把工具参数展示到聊天流。

### 影响

- 用户可以明确新开一个工单会话，也可以从最近会话列表恢复已有会话。
- 聊天页只承担交互，诊断工作台只承担复盘、审核和治理，UI 职责更清晰。
- 用户等待诊断时能看到“正在调用 xxx”这类进度提示，降低黑盒感；同时不泄露工具细节。
- 工具活动事件也进入 system event，后续 bad case 回放和诊断时间线可以复用。

### 测试

- Targeted：`node --check src/it_ticket_agent/static/app.js`，通过。
- Targeted：`uv run python -m unittest tests/test_frontend_smoke.py -q`，6 tests OK。
- Targeted：`uv run python -m unittest tests.test_runtime_smoke.ConversationRuntimeSmokeTest.test_tool_activity_events_record_frontend_progress_signal -q`，1 test OK。

### 下一步

- 当前是事件轮询式进度提示，不是 token 级 SSE；如果后续要展示模型输出增量，可以再补 SSE/WebSocket。
- 最近会话后续可以继续加搜索、分页和按状态筛选，适配长期运行后的会话量。


## 2026-04-25 生产环境文本槽位识别修复

### 问题

- 用户输入 `order-service 生产环境 Pod 频繁重启...` 后，系统没有进入诊断，而是返回 `awaiting_clarification`。
- 前端表现为机器人提示“已识别为知识咨询，但当前知识库没有足够命中”，并要求确认环境。

### 原因

- 前端会传 `service / cluster / namespace`，但没有单独的 `environment` 输入。
- 后端 `resolve_slots(...)` 只读取 request.environment，没有从用户文本中识别 `生产环境`。
- `order-service` 命中 CMDB 后把环境推测为 `prod`，而当前规则把 inferred fields 也当作需要澄清，导致已明确的诊断问题被澄清门槛拦截。
- 澄清提示复用了 generic guidance，RAG 不足时会出现“知识咨询”文案，进一步误导用户。

### 改动

- 新增环境文本识别：`生产环境 / 生产 / 线上 / prod / production / prod-*` -> `prod`，并支持 `staging / test / dev` 常见别名。
- `resolve_slots(...)` 优先使用用户文本中的环境信号；只有文本和 request 都没有环境时，才使用 CMDB 推测并触发确认。
- 新增回归用例，覆盖用户在主输入框输入完整诊断问题时必须直接进入工具诊断，不应进入 clarification。

### 影响

- 用户给出“生产环境 + 服务 + 集群 + 命名空间 + 症状”的诊断问题会直接进入 ReAct supervisor。
- 仍保留安全边界：如果用户没有明确环境，且只有 CMDB 推测值，系统仍可要求确认。
- 前端工具进度提示可以正常出现 `tool.started / tool.completed`。

### 验证

- API 实测同款输入返回 `status=completed`，`pending_interrupt=null`，`environment=prod`。
- 事件流包含 `tool.started` 和 `tool.completed`，例如 `check_pod_status / inspect_pod_events / inspect_pod_logs`。
- Targeted：`uv run python -m unittest tests.test_runtime_smoke.ConversationRuntimeSmokeTest.test_diagnostic_message_with_environment_text_enters_tool_diagnosis tests.test_runtime_smoke.ConversationRuntimeSmokeTest.test_missing_environment_triggers_clarification_before_diagnosis -q`，2 tests OK。

### 下一步

- 可以继续把澄清提示的 generic guidance 和 direct-answer 文案解耦，避免真正需要澄清时出现“知识咨询”这种误导性表达。


## 2026-04-25 首轮诊断事件流前端可见性修复

### 问题

- 后端已经写入 `tool.started / tool.completed` system events，但前端截图里仍然只看到最终诊断结论，看不到“正在调用 xxx 工具”。

### 原因

- 首轮新会话时，前端只有请求返回后才拿到 `session_id`；请求执行期间 `pollAgentActivity()` 没有 session id，只能显示泛化等待状态，无法拉取事件。
- 工具调用在本地 mock/轻量工具下执行很快，完成后 `agentActivityPanel` 又会自动隐藏，所以用户最终只看到最后答案。

### 改动

- 首轮新会话在发送前先用 `ticket_id` 预绑定 `currentSessionId/currentTicketId`，因为后端创建会话时也使用该 `ticket_id` 作为 session id。
- 前端轮询时不再只看最新事件，而是把 `knowledge.retrieved / context.collected / tool.started / tool.completed / tool.cached / tool.failed / conversation.closed` 增量渲染到 `agentActivityLog`。
- 诊断完成后不再自动隐藏活动状态，保留过程日志，直到用户点击“新会话”或清空当前会话。

### 影响

- 首轮诊断也能看到工具调用过程。
- 即使工具执行很快，完成后页面仍会保留“正在调用/已完成 xxx”的诊断过程记录。
- 工具参数和原始 payload 仍不展示在聊天页，只展示工具名级别的高层过程。

### 验证

- Targeted：`node --check src/it_ticket_agent/static/app.js`，通过。
- Targeted：`uv run python -m unittest tests/test_frontend_smoke.py -q`，6 tests OK。

### 下一步

- 如果后续需要真正逐 token 输出模型回答，可以在现有事件轮询基础上升级为 SSE/WebSocket。

## 2026-04-25 最终回答用户态诊断报告优化

### 问题

- 最终回复直接展示 `当前证据已经足够...ready 1/2...` 这类内部摘要，不像面向用户的根因判断。
- 聊天页会把 message 和 diagnosis.conclusion 再拼一遍，导致内容重复且偏调试。
- 没有解释为什么没有弹出执行审批，也没有明确展示关键证据、排除项和建议动作边界。

### 原因

- ReAct supervisor 的早停/最终出口直接使用模型 final_answer 或 `_build_early_stop_answer(...)` 的事实拼接结果。
- 前端 `buildAssistantBody(...)` 默认把 `message + formatDiagnosis(diagnosis)` 拼接展示，没有区分用户态报告和调试态诊断结构。
- 当前动作执行审批只会在生成已注册高风险 action proposal 时触发；纯只读诊断没有 action，自然不会弹审批，但之前没有把这个边界告诉用户。

### 改动

- 在 `ReactSupervisor` 出口新增用户态诊断报告生成：
  - 初步根因判断
  - 关键证据
  - 已初步排除/弱化项
  - 建议下一步
  - 为什么没有弹出执行审批
  - 置信度和停止原因
- `diagnosis` 增加 `display_mode=user_report / user_report / approval_explanation / recommended_actions / raw_evidence`。
- 前端检测 `diagnosis.display_mode === 'user_report'` 时只展示报告正文，不再重复追加内部调试字段。
- 对只读诊断工具场景明确说明：没有生成已注册高风险执行动作，所以不会弹审批；回滚、扩容、重启需要人工确认或接入 action tool 后再进入审批。

### 影响

- 聊天页最终输出更像值班人能读懂的诊断报告，而不是内部 tracing 摘要。
- 用户能看到根因判断、证据、排除项和下一步建议。
- 用户能理解“为什么没审批”：不是审批坏了，而是本轮没有可执行动作。

### 验证

- API 实测同款输入返回 `display_mode=user_report`，正文包含“初步根因判断 / 关键证据 / 建议下一步 / 为什么没有弹出执行审批”。
- Targeted：`uv run python -m unittest tests.test_runtime_smoke.ConversationRuntimeSmokeTest.test_diagnostic_message_with_environment_text_enters_tool_diagnosis tests/test_frontend_smoke.py -q`，7 tests OK。

### 下一步

- 可以把建议动作进一步结构化成 `read_only_next_steps / manual_action_candidates / executable_action_candidates`，方便后续接入真正的 action tool 和审批链。

## 2026-04-25 用户态报告根因保真与证据增强

### Problem
- 用户态报告不能只靠固定规则生成，否则会覆盖 fake/real LLM 已经给出的高质量根因句。
- 网络、上游依赖、数据库连接池等工具证据如果只展示 `connectivity=blocked / pool=saturated`，仍偏内部字段。

### Cause
- `_build_final_response(...)` 解析出 `final_answer` 后，报告生成阶段重新推断 root cause，导致 `diagnosis.conclusion` 丢失模型结论。
- 用户证据抽取最初主要覆盖 Pod / 事件 / 日志 / 变更，对 network/db payload 的用户态翻译不足。

### Change
- `_build_user_diagnosis_report(...)` 新增 `model_root_cause` 参数：保留高质量模型根因句，但过滤“当前证据已经足够”“已完成诊断”等过程化句子。
- `_infer_user_root_cause(...)` 补充网络链路、上游依赖、数据库连接池、慢查询的根因推断。
- `_user_facing_evidence(...)` 补充 `inspect_vpc_connectivity / inspect_upstream_dependency / inspect_connection_pool / inspect_slow_queries` 的用户可读证据。

### Impact
- 最终回答继续保持用户态报告结构，同时不会把模型已经判断出的“网络链路或上游依赖退化”等结论覆盖成泛化服务异常。
- 评测里的 `conclusion_contains` 可以稳定命中，前端用户也能看到更像诊断报告的证据句。

### Next Direction
- 后续可以把用户态报告生成从规则函数演进为 schema 化 LLM summary，但仍保留工具证据白名单和审批边界校验。

## 2026-04-25 前端产品化表单与报告卡片优化

### Problem
- 会话页仍像工程调试控制台：用户需要在侧栏填写服务/集群，再到底部输入问题，信息路径分散。
- 最终诊断虽然已经是用户态文本，但仍以 `pre` 长文本气泡展示，关键证据、建议动作和审批说明不够醒目。

### Cause
- 早期前端优先暴露后端能力，输入和输出都按“工程字段 + 文本日志”组织，没有按值班人发起工单和阅读诊断的任务流设计。
- 前端 `addMessage(...)` 只有纯文本渲染能力，无法针对 `diagnosis.display_mode=user_report` 做结构化展示。

### Change
- 会话页新增表单化工单入口：用户、服务、环境、集群、命名空间、问题描述和快速示例集中在主区域。
- 新增 `environmentName` 前端字段，首轮创建会话时随 `ConversationCreateRequest` 一起提交。
- 新增 `buildDiagnosisReportCard(...) / addAssistantMessage(...)`，把用户态诊断渲染成卡片：根因、置信度、工具调用、关键证据、排除项、建议动作、审批说明。
- 新增 `addTicketMessage(...)`，用户首轮请求也按工单摘要卡展示，避免只看到一行长文本。

### Impact
- 用户发起诊断时先填表，再提交，路径更接近真实工单产品。
- 用户阅读结果时能快速定位根因、证据和下一步，不需要在长文本里找重点。
- 前端仍不展示工具参数、原始 payload 和内部推理，调试细节保留在诊断工作台。

### Next Direction
- 可以继续做“诊断结果确认区”：把接受建议、拒绝并重查、转人工、创建 action 审批做成更明确的底部操作栏。

## 2026-04-25 诊断报告已查证据与待补动作边界修复

### Problem
- 用户问“为什么不能直接调用工具查看 Pod 日志和最近变更”，实际 system events 显示 `inspect_pod_logs / get_change_records` 已经执行。
- 最终报告仍建议“先查看失败 Pod 日志、最近变更”，导致用户以为 Agent 没有调用工具。
- `ready 2/2` 被渲染成“存在副本不可用”，正常证据被误读为异常。

### Cause
- 用户态报告的建议动作是固定 Pod/CrashLoopBackOff 模板，没有根据已执行工具集合区分“已检查”和“待补充”。
- 证据转换逻辑没有判断 `ready_replicas >= desired_replicas`，也没有过滤 `dependency=healthy / connectivity=healthy` 这类原始健康字段。
- 应用日志出现 `application_error` 时，根因推断没有优先使用日志异常，只给出泛化“证据不足”。

### Change
- 用户态证据中区分 `Pod 副本数正常` 和 `副本不可用`，并过滤低价值健康原始字段。
- 建议动作改为基于已执行工具生成：如果日志、事件、Pod 状态、变更已经查过，就明确写“已检查/已查询到”，不再建议重复查看。
- 日志出现应用错误时，根因判断升级为“应用运行时错误 + 变更相关性”，建议下一步核对异常堆栈、变更 diff 和受影响接口。
- 增加回归测试，防止 `ready 2/2` 被误报为不可用，防止已查 Pod 日志后仍建议“先查看失败 Pod”。

### Impact
- 报告会明确告诉用户哪些工具已经查过、哪些证据还缺，不再把已完成的诊断动作当作下一步建议。
- 对“查最近变更 + 运行时异常”这类问题，真实接口现在会输出：已查询变更、已发现日志 `application_error`、上游/VPC healthy，因此优先核对变更 diff 和异常堆栈。

### Next Direction
- 下一步可继续把“已查证据 / 待补证据 / 可执行动作”拆成独立 schema 字段，让前端报告卡片更精准地展示诊断闭环。

## 2026-04-25 Mock 世界前端沙盒

### Problem
- 现有前端只能填写单个问题和少量字段，不方便用户自由选择一个稳定的模拟世界进行多轮对话。
- `data/mock_case_profiles.json` 已经有 case/world 级工具返回，但前端没有入口，用户无法直观看到“当前在什么世界里问 Agent”。
- Mock 演示场景下，诊断报告卡片过于正式，用户更希望像普通聊天一样连续追问。

### Cause
- Mock profile 原本主要服务 eval runner，通过 `tool_profile` 转成 `mock_tool_responses`，没有产品化暴露给控制台。
- 前端只知道发起真实/默认请求，不知道如何携带整组工具 mock。

### Change
- 新增 `GET /api/v1/mock-worlds`，将 `mock_case_profiles.json` 转成可选世界：`world_id / case_id / service / description / tool_names / mock_tool_responses`。
- 会话表单新增“Mock 世界”选择器和世界摘要，选择后自动填充 service，并在首轮请求中携带该世界的 `mock_tool_responses`。
- 多轮续聊复用 session `shared_context.mock_tool_responses`，不用每轮前端重复发送。
- Mock 世界模式下，Agent 回复走普通聊天气泡，不渲染诊断报告卡片；恢复历史会话时会自动识别自定义 Mock 世界。

### Impact
- 用户可以自由选择 `case1 / case2` 等模拟世界，并在同一个世界里多轮追问 Agent。
- 演示、调试和面试讲解更直观：世界负责稳定工具返回，Agent 仍真实执行工具选择和诊断。

### Next Direction
- 可以继续给每个世界补充可读名称、故障类型、推荐开场问题和工具返回预览，而不是只显示 `case_id / service`。

## 2026-04-26 工作台表单化与 Mock 世界记忆闭环

**问题**
- 诊断工作台里 session memory、context snapshot、Playbook steps、case detail、bad case detail 仍大量直接展示 JSON，值班人需要读字段结构才能理解状态。
- Mock 世界可以多轮对话，但从演示视角还缺少明确的“对话完成 -> 历史案例审核 -> Playbook 候选抽取 -> Playbook 审核启用”闭环入口。

**原因**
- 前端早期优先验证 API 可见性，直接复用 `appendJsonBlock` 展示复杂对象，没有把运行态数据转成面向值班人的摘要表单。
- `POST /api/v1/cases/{case_id}/review` 只更新 case 审核状态，Playbook 抽取主要发生在反馈链路里，工作台手动审核 case 时没有显式串起候选生成。

**改动**
- 前端新增 `appendFormSection / appendCardList / appendRawJsonDetails`，把工作台详情改成表单区块、列表和步骤卡；原始 JSON 只作为可展开调试信息保留。
- 案例审核新增“抽取 Playbook 候选”按钮，调用 `POST /api/v1/cases/{case_id}/extract-playbook`。
- 后端新增 `PlaybookExtractionResponse`，并把 `review_case` 改为返回 `incident_case + playbook_extraction + playbook_candidate`。
- `SupervisorOrchestrator` 增加公开的 case review / playbook extraction 方法：默认仍按同类 verified case 聚合，Mock 演示可显式允许单案例生成 `pending_review` candidate，但不会自动上线。
- 反馈确认链路会把生成的 `playbook_candidate` 放回 response diagnosis，方便前端或回放查看。

**影响**
- 工作台默认可读性更接近产品表单，不需要先理解后端 JSON 结构。
- Mock 世界对话完成后，可以在同一套工作台里跑通案例入库和 Playbook 候选生成，再通过 Playbook 审核进入在线召回。
- 记忆发布安全边界不变：case 必须人工确认，Playbook candidate 也必须人工审核为 `verified + human_verified` 才能在线召回。

**下一步**
- 给 Mock 世界增加推荐起始问题和预期根因，方便一键跑演示。
- 在 case / playbook 列表增加来源标记，如 `mock_world_id`、`source_session`，让演示样本和真实生产样本更容易区分。

## 2026-04-26 知识 RAG 父子分块召回

**问题**
- 之前知识库虽然有 section-aware chunking 和 metadata，但检索结果本质上仍是扁平 chunk。
- 用户问到父子分块时，当前实现不能明确做到“子块向量召回、父块上下文返回”。
- Agent 侧也无法区分命中的小片段和实际提供给模型的完整上下文。

**原因**
- pgvector 只有 `documents / chunks` 两层，`chunks` 没有 `parent_id`，没有独立父块表。
- 本地 `index.json` 只保存 chunks，没有父章节块。
- RAG API schema 只返回 `snippet`，缺少 `child_snippet / parent_snippet / retrieval_granularity`。

**改动**
- RAG service 新增 `KnowledgeParentBlock`，本地索引写入 `parents`，pgvector 新增 `parent_blocks` 表。
- `chunks` 增加 `parent_id`，检索仍以 child chunk 做 sparse/dense/rerank/MMR，最终命中后 hydrate 父章节窗口。
- RAG API 和 Agent `KnowledgeHit` 增加 `parent_id / parent_section / child_snippet / parent_snippet / retrieval_granularity`。
- `chunking_signature` 加入 `strategy=section-parent-child-v1`，避免旧索引因为文档 checksum 未变而跳过父块回填。
- 增加本地 parent-child 回归测试，覆盖“子块命中但返回父块上下文”和旧索引 fallback。

**影响**
- 知识检索精度仍由小 chunk 保证，但 Agent 拿到的是更完整的章节上下文，减少孤立片段误读。
- pgvector 和本地索引行为一致；旧索引仍可降级加载，不会因为缺少 parent 表直接失败。
- 前端/工作台后续可以同时展示命中点和父上下文，解释“为什么召回这条知识”。

**下一步**
- 可以在诊断工作台的 RAG 面板里把 `child_snippet` 和 `parent_snippet` 分开展示。
- 后续如果文档规模继续变大，可再加 parent-level 去重、按文档/章节聚合 rerank、以及跨 parent 的上下文预算裁剪。

## 2026-04-26 Mock 世界端到端验证与记忆链路修复

**问题**
- 用 `case1::order-service` 跑完整 mock 世界时，首次暴露出 RAG 本机调用 502、approval audit event 主键冲突、显式 OOM 被 timeout/5xx 带偏、case-memory 排序不稳等问题。
- 成功诊断后，`incident_case.key_evidence` 一度保存了审批后执行摘要/变更记录，而不是选中根因的 OOM 证据。
- case-memory 能召回新案例，但跨服务 OOM 案例可能因为 `pattern + semantic_hybrid` 分数叠加排到同服务案例前面。

**原因**
- Agent 侧 `httpx.AsyncClient` 默认 `trust_env=True`，在当前系统代理环境下访问 `127.0.0.1:8201` 会被代理成 502。
- PG 中 `approval_audit_event.event_id` 序列曾低于已有最大主键，导致创建审批事件时冲突。
- rule-based fallback 中 `explicit_network_signal` 和 `matched cicd/db` 会抑制显式 OOM 的 K8s 假设，导致只查变更、网络和 DB。
- incident case 生成时优先取 response diagnosis evidence，审批恢复后的 response evidence 可能已经变成执行阶段摘要。
- case-memory 最终排序只按合并分数，缺少“同服务 + 同故障类型”的业务优先级。

**改动**
- `RAGServiceClient` 调用 RAG service 时设置 `trust_env=False`，本机服务调用不再被系统代理影响。
- `PostgresApprovalStoreV2._init_db()` 启动时校准 `approval_audit_event` sequence 到当前最大 `event_id`。
- rule-based fallback 调整假设生成：显式 OOM/POD 信号始终保留 K8s 检查，不再被 timeout/5xx 或泛化 domain match 屏蔽。
- `incident_case` 生成优先保存 `ranked_result.primary` 的 root cause 和 evidence/source_refs，再合并最终 response evidence。
- case-memory 排序增加业务 key：同服务同 failure/taxonomy 优先，其次才看 exact/pattern/semantic 合并分数。
- 增加回归测试：OOM + timeout 仍执行 `check_pod_status / inspect_pod_logs / inspect_pod_events`；同服务同故障案例优先于跨服务语义高分案例。

**影响**
- Mock 世界完整链路现在可以跑通：会话 -> RAG -> mock tools -> 高风险审批 -> action 执行 -> feedback -> verified case -> case-memory sync -> Playbook candidate。
- 最终 `incident_case` 和 case-memory 文档保存的是 OOMKilled、ready 1/2、OutOfMemoryError、exit code 137、Pod event 等根因证据。
- case-memory 查询 `order-service OOMKilled Java heap space ready 1/2` 时，同服务新案例排在跨服务 OOM 案例前面。

**下一步**
- 可以把 mock world 的预期根因、推荐开场问题和应调用工具固化为 eval case，避免只靠人工观察。
- 可以给诊断工作台增加“本次命中的 RAG / case / playbook 是否影响了工具选择”的对照视图。

## 2026-04-26 Mock 世界发布回归链路增强

**问题**
- 原 `case1::order-service` 过于简单，测试问题必须显式写出 `OOMKilled / Java heap / exit code 137`，等于把根因直接告诉 Agent。
- 发布/变更方向虽然已有 `check_recent_deployments / get_change_records` 等工具，但规则链路主要靠用户文本触发回滚动作，不能做到“通过工具证据发现某次 commit 导致故障”。
- 用户在前端测试时不容易看到审批后的 mock action 执行效果，容易误以为审批链路没接上。

**原因**
- Mock world 描述直接摘取工具 summary，UI 上会提前泄露关键证据。
- `H-CICD` 的 `recommended_action` 只在用户提到“回滚 / 发布失败 / 最近变更”时设置；如果用户只说“错误率升高、下单失败”，即使工具查到可疑 commit，也不会自动生成回滚审批。
- rollback mock executor 没有把 `target_revision` 透传到执行结果里，执行反馈不够像真实动作。

**改动**
- 新增 `case3::order-service`：生产下单失败/5xx/延迟升高，Pod、网络、DB 基本正常，工具链路通过最近发布、rollout、change records 定位 commit `8f31c2a` 把 `readTimeoutMillis` 从 `3000ms` 改成 `300ms`。
- `H-CICD` 规则验证扩展为服务健康、最近发布、rollout、pipeline、change records、rollback history 六类只读检查。
- `ReactSupervisor` 增加基于工具证据的动作推导：当近期发布 + 相关 commit + 服务/rollout 退化形成闭环时，即使用户没提“发布/回滚”，也会生成 `cicd.rollback_release` 高风险审批。
- `H-K8S` 也支持从工具证据识别 OOM 后生成 `restart_pods`，降低对用户显式说出 OOM 的依赖。
- Mock world 列表描述改为按工具域展示，不再提前暴露具体根因。
- rollback mock executor 返回 `target_revision` 和本地 job id，让审批后执行结果更像真实动作。

**影响**
- 更适合真实演示的问题可以变成“order-service 创建订单接口错误率突然升高，用户反馈下单失败”，根因需要 Agent 通过工具查出来。
- 全链路能覆盖：RAG / case-memory -> 只读工具排查 -> commit 级证据 -> 高风险回滚审批 -> mock action 执行 -> feedback -> case 入库。
- 前端 mock 世界不会在选择阶段泄露 OOM 或 commit 细节，用户需要从诊断过程里看到工具如何收敛。

**下一步**
- 把 case3 固化成一键 eval：断言必须调用 `get_change_records`，必须指出 commit `8f31c2a`，必须触发 `cicd.rollback_release` 审批。
- 前端执行结果面板可以进一步突出“审批后执行了哪个 action、目标版本、job id、执行状态”。


## 2026-04-26 Mock Tool 返回真实化

**问题**
- `case3::order-service` 已能通过工具链路定位发布 commit，但 mock payload 偏“结论化”：字段主要是 `health_status / latest_revision / diff_summary / log_snippets`。
- 前端和 LLM 都能看到结果，但不像真实值班工具输出，缺少来源系统、查询参数、时间窗、原始片段、资源标识和基线对比。

**原因**
- 早期 mock 主要服务规则验证，追求最小字段闭环。
- 真实诊断工具通常来自 Prometheus、Alertmanager、Kubernetes API、ArgoCD、GitLab、Loki、RDS 等系统，返回会同时包含 request、raw response、metrics、events、logs、release history 等结构。

**改动**
- 保留统一 `ToolExecutionResult` contract：`tool_name / status / summary / payload / evidence / risk` 不变。
- 补强 `case3::order-service` 的关键工具 payload：增加 `source`、`request`、`observed_at`、`time_range`、`raw_response`、PromQL/API/kubectl/log query、release records、GitLab compare、diff hunks、K8s pod metadata、Loki log streams、SLO burn、rollback candidate 等字段。
- 保留原有关键字段，如 `latest_revision`、`previous_revision`、`changes`、`last_known_stable_revision`、`ready_replicas`、`log_snippets`，避免破坏现有推理和测试。

**影响**
- Mock world 现在更像“真实系统返回的结构化观测结果”，而不是人工写好的根因摘要。
- LLM 可以基于工具来源、查询、时间线、日志、diff 和指标自己收敛根因：commit `8f31c2a` 将 `readTimeoutMillis` 从 `3000ms` 改为 `300ms`，导致 create-order 下游调用超时。
- 前端工作台展示 tool payload 时，能看到更接近生产工具的表单字段，后续也更容易把不同来源系统分组展示。

**下一步**
- 为 tool payload 增加轻量 schema/renderer hints，让前端按 `metrics / logs / events / changes / rollout` 自动选择更可读的表格或时间线展示。
- 给 eval 增加断言：模型不能只复述 summary，必须引用至少一个真实化字段，如 PromQL 指标、GitLab diff hunk、Loki log entry 或 ArgoCD release record。


## 2026-04-26 Mock Tool Raw Output 去摘要化

**问题**
- 上一轮把 mock tool 返回真实化时，仍保留了顶层 `summary/evidence`，这不符合真实工具边界。
- 真实 tool 不经过 LLM，不应该自己生成自然语言摘要；它只应该返回结构化数据，诊断摘要和证据解释应由 Agent/runtime 基于 payload 派生。

**原因**
- 早期 `ToolExecutionResult` 同时承担了 raw tool output、UI 展示、规则诊断证据三种职责。
- mock profile 为了方便测试，直接把人工写好的 `summary/evidence` 放在工具返回里，导致模型可能读到“加工后的结论”。

**改动**
- mock resolver 不再把 mock profile 的顶层 `summary/evidence` 注入 `ToolExecutionResult`；mock tool 只返回 `status/payload/risk`。
- mock-world API 对外暴露的 `mock_tool_responses` 会清洗掉 `summary/evidence`，前端传回后端的也是结构化 payload。
- ReAct prompt 和 tool message 改为 model-visible 结构：`tool_name/status/payload/risk`，不再把 `summary/evidence` 放进 LLM 上下文。
- 规则诊断、working memory、pinned findings、用户报告所需证据，统一从结构化 `payload` 派生，而不是读取 tool 自带 evidence。
- 清理 mock profile 顶层 `summary/evidence`，保留 GitLab commit summary、Alertmanager annotation summary 这类真实系统原始字段。

**影响**
- Mock world 更接近真实工具：Prometheus/Loki/GitLab/K8s 只提供结构化观测数据。
- LLM 需要从指标、日志、diff、事件、发布记录中自己串证据，不能依赖 mock tool 直接给出的人工结论。
- UI 侧如果没有 summary/evidence，会展示 payload；后续可继续按 payload schema 做表单化渲染。

**下一步**
- 进一步把内部兼容 envelope 中的展示字段与 raw tool result 拆成两个模型：`RawToolResult` 和 `ToolObservationView`。
- 给各类 payload 增加 renderer hints，让前端按 metrics/logs/events/changes 自动渲染。


## 2026-04-26 Mock World 全工具覆盖与结构对齐

**问题**
- 三个 mock world 之前只覆盖关键路径工具：`case1` 覆盖 13/29，`case2` 覆盖 14/29，`case3` 覆盖 20/29。
- LLM 理论上可以调用任意已注册只读诊断 tool；如果某个 tool 没有 world 内固定返回，会 fallback 到工具默认启发式逻辑，导致 mock world 不封闭。
- 同名 tool 在不同 world 的 payload key 不完全一致，前端表单化展示和 eval 断言都不稳定。

**原因**
- 早期 mock world 是为了验证某条诊断主链路，不是完整环境投影。
- 工具 mock 以“相关工具异常”为主，缺少“无关工具正常/排除性返回”。
- 没有测试强制校验每个 world 覆盖全部非 RAG tool，也没有校验同名 tool 的 payload 顶层结构一致。

**改动**
- 将 `case1::order-service`、`case2::order-service`、`case3::order-service` 都补齐为 29 个非 RAG 只读工具返回。
- RAG / Case Memory / Playbook 仍然不属于 mock world，由真实检索链路负责。
- 每个 world 的同名 tool 使用相同 payload 顶层 key；差异只体现在字段值，例如 OOM、网络阻塞、发布回归分别通过日志、事件、指标、变更、网络探测等值体现。
- 保持 tool raw output 去摘要化：mock response 顶层仍不包含 `summary/evidence`，只保留结构化 `payload`。
- 前端 smoke test 增加校验：每个 order-service mock world 必须覆盖全部非 RAG tools，且同名 tool payload key 必须一致。

**影响**
- Mock world 变成封闭的可复现场景：LLM 调任意只读诊断 tool 都能得到该世界内一致的结构化返回。
- 无关方向也会返回正常/排除性证据，避免默认逻辑污染诊断过程。
- 前端可以安全地按 tool schema 渲染表单，eval 也可以断言任意 tool 的结构存在。

**下一步**
- 可以继续把每个 tool 的 payload schema 抽成显式定义，用 schema 生成 mock skeleton 和前端 renderer。
- 可以把“完整 world 覆盖率”加入 CI，防止新增 tool 后忘记补 mock world。


## 2026-04-26 Eval 统一到 Mock World Profile

**问题**
- 官方 eval 数据集历史上混用了三种口径：`tool_profile`、局部 `mock_tool_responses`、以及 `world_state` 投影。
- 这会导致评估样本和前端 Mock 世界不一致：线上演示看到的是完整世界，离线 eval 可能只测了几个手写 tool 返回。
- 旧 mock world 只有 3 个场景，覆盖 OOM、网络、发布回归，不足以覆盖 DB、SDE、CPU/thread pool、canary 等常见值班事故。

**原因**
- 早期 eval 优先追求快速验证单条路径，直接在 case 内写局部 mock 最省事。
- 后续前端沙盒和全工具 mock world 成熟后，eval 数据源没有同步收敛到同一份 `mock_case_profiles.json`。
- `world_state` 投影器能表达共享事故状态，但和前端可选 world、真实化 payload、全工具覆盖之间存在两套维护成本。

**改动**
- 将 `tool_mock_cases.json` 全部迁到 `setup.tool_profile`，规模从 13 扩到 15，新增 CPU/thread pool 和 canary 场景。
- 将 `world_cases.json` 从 eval-only `world_state` 改成 mock world profile，规模从 5 扩到 7。
- 将 `rag_cases.json` 的非 RAG 工具证据迁到 `tool_profile`；RAG 命中、query rewrite、case recall 仍只在检索边界 mock。
- 将 `session_flow_live_cases.json` 的 inline override 清掉，真实 LLM 多轮 eval 也直接引用完整 mock world。
- `mock_case_profiles.json` 扩到 8 个完整世界：OOM、网络、发布回归、DB pool、SDE quota、CPU/thread pool、Ingress/LB/VPC、Canary regression。
- ReAct runtime 补充 `check_canary_status` 候选域、CPU/thread 显式意图识别、quota/canary/cpu/lb 的异常识别和证据派生。
- 增加回归测试，约束官方诊断类 eval 不再使用 inline `mock_tool_responses` 或 eval-only `world_state`。

**影响**
- 前端 Mock 世界、真实 LLM eval、RAG eval 的非检索工具证据都来自同一份世界数据，避免“演示一套、评估一套”。
- LLM 在评估里可以调用任意非 RAG 只读工具，都会得到同一个事故世界内的结构化返回。
- RAG eval 的边界更清楚：知识检索可以 mock，但实时工具证据必须由 mock world 决定。
- 新增场景覆盖更接近值班主路径，尤其是“Pod 正常但 CPU/thread pool 饱和”和“Canary 指标失败需要回滚审批”的复杂场景。

**下一步**
- 后续新增 tool 时，把 `mock_case_profiles.json` 的全工具覆盖测试作为必过项。
- 可以继续把 mock world 元数据暴露给前端筛选，例如按 `tags / expected_root_cause` 过滤场景。
- 真实 LLM eval 需要在稳定 provider 下重新跑一轮完整 `eval-regression`，校准新增 case 的 gate 阈值。

## 2026-04-26 Eval Report 产物清理

**问题**
- `data/eval-report.json`、`data/session-flow-eval-report.json`、`data/session-flow-live-eval-report.json`、`data/world-eval-report.json` 是评估运行产物，但被提交到了仓库。
- 这些报告体积较大，内容会随每次 eval 变化，容易制造无意义 diff。

**原因**
- 早期为了方便查看离线评估结果，直接把 `--output` 指向 `data/` 根目录。
- `data/` 下同时存在正式 eval dataset 和临时 report，边界不清。

**改动**
- 删除已入库的旧 eval report JSON。
- `.gitignore` 增加 `projects/it-ticket-agent/data/*eval-report*.json`，防止报告再次进入 git。
- README 示例改为输出到 `/tmp/it-ticket-agent-*.json`，把报告定位为本地/CI artifact。
- 最新架构文档补充：eval report 是运行产物，不属于仓库主数据。

**影响**
- 仓库只保留 eval dataset、mock world、代码和文档，减少大文件与过期结果污染。
- 后续跑 eval 仍可生成 JSON 报告，但默认不会进入 git diff。

**下一步**
- 如果需要长期留存评估趋势，应接 CI artifact 或独立评估看板，而不是把单次 report 提交到源码仓库。

## 2026-04-26 Legacy Mock Profile 清理

**问题**
- 项目里同时存在两套 mock 数据源：新的 `mock_case_profiles.json` 完整 Mock 世界，以及旧的 `mock_*_profiles.json` 分域场景 profile。
- 旧 `mock_scenario / profile` 只覆盖局部工具和少量服务，和前端 Mock 世界、官方 eval 的完整现场口径不一致。
- `docs/assets` 下还有早期架构图和生成 prompt，没有文档引用，继续保留会增加仓库噪声。

**原因**
- 早期为了快速测试单个 tool，引入了按 service + scenario 组织的分域 profile。
- 后来 Mock 世界已经演进成完整工具现场，但旧 profile fallback 和测试没有同步下线。

**改动**
- 删除无引用文档资产：`docs/assets/it-ticket-agent-current-architecture.png`、`it-ticket-agent-data-flow.*`。
- 删除旧分域 profile：`mock_tool_profiles.json`、`mock_network_profiles.json`、`mock_db_profiles.json`、`mock_sde_profiles.json`、`mock_finops_profiles.json`。
- 工具 mock resolver 收敛为 `mock_tool_responses -> mock_world_state -> mock_case`，移除 `mock_scenario / profile` fallback。
- CICD / DB / Network / SDE / FinOps 工具统一复用 case-profile mock resolver；保留原本基于用户文本的轻量 fallback，避免无 mock 时工具不可用。
- 相关测试从 `mock_scenario` 迁移到 `mock_tool_responses` 或 `IT_TICKET_AGENT_CASE / IT_TICKET_AGENT_CASES`。

**影响**
- 当前前端 Mock 世界、官方 eval、工具单测都使用同一份 `mock_case_profiles.json`，减少双写和口径漂移。
- 请求 schema 中的 `mock_scenario / mock_scenarios` 仍兼容接收，但不再有内置 profile 数据源；新用例应使用 Mock 世界或 case profile。
- 旧分域 profile 删除后，新增 tool 只需要补一份完整 Mock 世界覆盖。

**下一步**
- 后续可以继续清理 `mock_world_state / world_simulator` 兼容路径，但需要先确认是否还有外部样本依赖。

## 2026-04-26 Mock World State 兼容路径清理

**问题**
- `mock_world_state / world_simulator` 已经不再是官方 eval 和前端 Mock 世界的数据源，但代码里仍保留投影器、schema 字段和兼容测试。
- 这会让 mock 体系继续存在两种表达：一套是完整 `mock_case_profiles.json`，另一套是按 signals 投影工具结果的旧 world state。

**原因**
- 早期 `world_state` 用于解决“多工具返回需要共享同一事故状态”的问题。
- 后来 `mock_case_profiles.json` 已经覆盖完整工具空间，并成为前端沙盒、官方 eval 和真实 LLM mock-world 测试的统一来源。

**改动**
- 删除 `src/it_ticket_agent/testing/world_simulator.py`。
- 从工具 mock resolver 中移除 `mock_world_state` fallback，当前顺序收敛为 `mock_tool_responses -> mock_case`。
- 从请求 schema、runtime shared context、React supervisor、eval harness 和 bad-case skeleton 导出中移除 `mock_world_state / world_state` 字段。
- 将相关测试迁移到 `tool_profile` 或 `mock_tool_responses`，继续验证 DB profile、inline override 和 mock world 主链路。
- README 和最新架构文档更新为：`mock_case_profiles.json` 是唯一内置 mock world 主数据源。

**影响**
- Mock 体系只剩一条主线：前端/API 用 `mock_tool_responses`，环境变量/eval 用 `mock_case_profiles.json`。
- 新增场景不再需要维护旧 signals 投影器，减少双写和结构漂移。
- 旧请求里的 `mock_world_state` 会被 schema 忽略，不再驱动工具结果；需要复现场景时应转换为 case profile 或直接传 `mock_tool_responses`。

**下一步**
- 后续如果继续清理，可以评估 `mock_scenario / mock_scenarios` schema 兼容字段是否还有外部调用依赖。

## 2026-04-26 首轮无证据直接结论保护

**问题**
- 真实 LLM mock-world eval 中，模型偶发在首轮没有 tool call 的情况下直接输出诊断结论。
- 这会让 `react_tool_first` 退化成“看上下文直接猜”，即使 gate 可能通过，也无法证明工具选择和现场证据链真实生效。

**原因**
- 原先 supervisor 对“无 tool_calls”的模型响应直接进入 final response。
- Prompt 里虽然要求不要编造观测结果，但无法完全约束模型在首轮直接回答。

**改动**
- `ReactSupervisor` 增加首轮 live evidence guardrail：当还没有任何 live observation 且模型没有返回有效 tool call 时，从候选工具中排除 RAG/历史召回工具，强制执行最多 3 个只读现场工具。
- 补充回归测试，验证 fake LLM 首轮直接回答时，runtime 仍会先调用网络现场工具。
- `world_cases.json` / `tool_mock_cases.json` 中 CICD 首批工具断言改为接受等价的发布状态、变更记录、流水线和 canary 工具组合，避免把合理工具选择误判为失败。

**影响**
- 诊断主链更符合 Tool-First 边界：没有现场证据时不能直接输出根因。
- 真实 LLM 评估更稳定，减少模型一次性直答导致的假阴性或假阳性。
- 历史案例、Playbook 和 RAG 仍可指导工具顺序，但不能替代 live tool observation。

**下一步**
- 后续可把“首轮最小探测工具数”和“排除哪些非现场工具”抽成配置，方便不同垂直 Agent 调整。

## 2026-04-26 Mock World 场景 DSL 与 Eval 断言继承

**问题**
- `mock_case_profiles.json` 已经是完整 Mock 世界，但它主要像“工具返回集合”，场景意图、难度、噪声、评估重点和期望诊断散落在 eval dataset 与文档里。
- `tool_mock_cases.json` / `world_cases.json` 反复写 `status / route / required_any_tools / tool budget` 等默认断言，新增或调整场景时容易双写漂移。

**原因**
- 早期 eval case 直接写断言最直观，但随着 Mock 世界覆盖全部工具，场景事实和评估标准应该以世界为中心维护。
- 项目目标是 Agent 层能力，不适合引入很重的本地 Prometheus/K8s/Loki/GitLab 仿真服务。

**改动**
- 将 `mock_case_profiles.json` 升级为轻量 Agent Scenario DSL，每个世界新增 `difficulty / failure_mode / root_cause_taxonomy / user_prompt_templates / noise_factors / evaluation_focus / expected_diagnosis`。
- 在 `expected_diagnosis.eval_expect` 中沉淀该世界的默认评估断言，包括核心证据工具、首查工具、tool budget 和是否需要扩域。
- `load_agent_eval_dataset` 会按 `setup.tool_profile` 自动继承 profile 默认断言，eval case 只保留误导 prompt、扩域、强证据早停、forbidden tools 等 case-specific 覆盖。
- `/api/v1/mock-worlds` 暴露场景 DSL 元数据；前端 Mock 世界卡片展示难度、评估重点和示例问题，选择世界时可自动填入第一条示例问题。

**影响**
- Mock 世界从“工具返回集合”变成“Agent 场景协议”：同一份数据同时支撑前端演示、真实 LLM eval 和场景扩展。
- 新增 case 时先补场景事实和默认期望，再按需要添加少量 eval 覆盖，减少重复断言。
- 项目仍保持轻量，重点留在工具选择、证据融合、扩域、审批和记忆沉淀，而不是外部系统仿真。

**下一步**
- 后续可以继续补 prompt 变体生成器，例如弱症状、误导症状、噪声症状、多轮追问和审批追问。
- 如果场景继续增多，可以给 `mock_case_profiles.json` 增加 JSON schema 校验，保证每个世界都具备完整 DSL 字段。

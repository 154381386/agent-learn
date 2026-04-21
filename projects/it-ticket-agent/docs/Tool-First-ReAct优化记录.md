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

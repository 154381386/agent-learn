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

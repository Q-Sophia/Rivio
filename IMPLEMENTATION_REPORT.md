# Framework Registry + Framework-aware ResearchTask v2 Implementation Report

## 1. 实现结论

本次已在原有 Research Planning 链路内引入版本化 Framework Registry，未创建第二套 ResearchTask、Artifact 或 Pipeline。

```text
competitive_intelligence@1.0.0.yaml
        |
        v
YamlFrameworkRegistryBackend  -- 可替换存储边界 --> 未来 MySQL Backend
        |
        v
FrameworkRegistry（校验、版本、状态、路径安全、SHA-256）
        |
        v
ResearchPlanningService
        |
        | validated FrameworkDefinition
        v
ResearchPlannerAgent
        |
        +--> 既有 ResearchPlan
        +--> 既有 KeyIntelligenceQuestion
        +--> 既有 InformationNeed
        +--> 既有 ResearchTask v2（固定 framework provenance）
        |
        v
既有 Agent Handoff / Research Agent / Evidence Pipeline
```

实现前的代码扫描和设计差距见 `docs/framework_registry_gap_analysis.md`。

## 2. 修改文件

### 新增

| 文件 | 内容 |
| --- | --- |
| `backend/app/frameworks/__init__.py` | Registry 公共接口、默认 Framework ID/version。 |
| `backend/app/frameworks/registry.py` | 存储无关的 Registry、YAML Backend、版本和状态校验、路径约束、模板字段校验、内容哈希及防御性缓存副本。 |
| `backend/app/frameworks/registry.json` | Framework 版本、状态、定义路径和默认版本索引。 |
| `backend/app/frameworks/definitions/competitive_intelligence/1.0.0.yaml` | 六个指定通用竞品分析维度的可加载定义。 |
| `backend/check_framework_registry.py` | Framework 加载、六维完整性、hash、dimension lookup、未知版本及缓存隔离测试。 |
| `backend/check_framework_planning_v2.py` | Framework 驱动 Planner、provenance 和旧 evidence dimension 映射测试。 |
| `backend/check_research_task_v2_backward_compat.py` | 旧 v1 ResearchTask 解析并由现有 Research Agent 执行到终态的测试。 |
| `docs/framework_registry_gap_analysis.md` | Phase 0 当前实现、接入点、最小修改和预计文件分析。 |

### 修改

| 文件 | 内容 |
| --- | --- |
| `backend/app/schemas.py` | 新增 `FrameworkDefinition`、`DimensionDefinition`；在唯一的 `ResearchTask` 上增加四个可选 provenance 字段和 v2 完整性校验。 |
| `backend/app/intake/research_planning.py` | 通过 Registry Interface 加载指定/default Framework，并把校验后的对象交给 Planner；TaskBoard 同步记录 provenance。 |
| `backend/app/agents/research_planner.py` | 移除四维默认值及研究规则硬编码；按 Framework 定义生成 KIQ、InformationNeed 和原有 ResearchTask。 |
| `backend/app/execution/research_mission.py` | 现有 Mission 子任务从父 ResearchTask 继承 Framework provenance。 |
| `backend/app/intake/step6e4.py` | 现有 bounded supplement task 从父 ResearchTask 继承 Framework provenance；未新增 Gap 调度行为。 |

## 3. 架构变化

### Registry 边界

- 业务代码和 Agent 不读取 YAML；YAML 读取只存在于 `YamlFrameworkRegistryBackend`。
- `FrameworkRegistryBackend` Protocol 将存储实现与业务接口隔离。未来 MySQL 只需实现 `default_version()` 和 `load_document()`，Planner 无需变化。
- 公共接口为 `load_framework(framework_id, version)` 和 `get_dimension(framework_id, dimension_id)`。
- 加载时校验注册状态、ID/version 一致性、路径越界、Pydantic schema、维度/别名冲突和模板字段白名单。
- SHA-256 基于实际定义文件内容计算并写入 Framework 对象。

### Framework Definition

`competitive_intelligence@1.0.0` 保留指定的六个 dimension ID：

1. `market_positioning`
2. `product_capability`
3. `competitive_differentiation`
4. `commercial_strategy`
5. `customer_experience`
6. `ecosystem`

每个维度均包含要求的 `label`、`research_intent`、`research_questions`、`required_facts`、`query_templates`、`preferred_source_types` 和 `completion_criteria`。额外的 `evidence_dimension` 用于映射既有下游协议，例如 `customer_experience -> customer`，因此无需修改 Evidence Pipeline 和来源质量规则。

### ResearchTask v2

仍使用 `backend/app/schemas.py::ResearchTask` 和 `research_tasks` artifact，新增：

- `framework_id`
- `framework_version`
- `framework_dimension_id`
- `framework_content_hash`

Framework 生成的任务显式写 `schema_version="v2"`，四个字段必须同时存在，hash 必须是 64 位小写 SHA-256。旧 JSON 缺少这些字段时仍按 `v1` 解析和执行。

`dimension` 没有改成 Framework dimension ID，而是继续保存既有 canonical evidence dimension；Framework dimension 由 `framework_dimension_id` 单独记录。这避免影响 official-first/community routing、Source Quality、Coverage、Evidence 和报告链路。

### Planner

Planner 的下列研究标准已由 Framework 提供：

- 默认维度及 focus-area alias 解析；
- KIQ 研究问题；
- ResearchTask objective；
- required facts；
- query hints；
- preferred source types；
- comparability basis；
- completion/stop condition；
- task priority。

Research Agent 收到的仍是完整、已物化的 ResearchTask 和 InformationNeed。执行阶段不重新读取 Framework，保证历史任务可复现。

## 4. 测试结果

运行环境：`C:\Users\qyn\anaconda3\envs\agent\python.exe`。

| 检查 | 结果 | 验证内容 |
| --- | --- | --- |
| `check_framework_registry.py` | PASS | 精确版本加载、六维定义、content hash、dimension lookup、未知版本拒绝、缓存副本隔离。 |
| `check_framework_planning_v2.py` | PASS | Framework focus 解析、query template、ResearchTask v2 provenance、旧 evidence dimension 保持。 |
| `check_research_task_v2_backward_compat.py` | PASS | 无 Framework 字段的旧 v1 ResearchTask 可解析，并由现有 Research Agent 执行到 `EXHAUSTED` 终态。 |
| `check_step6e1_research_planner.py` | PASS | 原 Research Planning API、TaskBoard 发布、预算和无 LLM 调用回归。 |
| `check_step6g_intent_aware_extraction.py` | PASS | Intent-specific Evidence、已知 URL 重采、Evidence 复用及部分证据分析回归。 |
| `check_step6e4_gap_tasks.py` | PASS | 现有 coverage/gap/bounded supplement 行为回归。 |
| `check_kp_r2_mission_supervisor.py` | PASS | 现有 Mission child task、上下文过滤和循环回归。 |
| Ruff（本次 Python 文件） | PASS | 无 lint error。 |

以上回归均未进行真实网络、DeepSeek 或 Tavily 调用。

额外尝试的两个全局旧脚本未计入本次验收：`check_ra_flow_fx1.py` 对当前前端启动路由的旧字符串断言失败；`check_r1_bounded_fx1.py` 假定所有 Coordinator SSE 事件都含 `progress_percent`，与当前 Evidence Feed 事件不一致。失败点均不经过本次 Framework Registry/Planner 代码，相关前端、SSE 与 Harness 文件也不在本次修改范围内。

## 5. 未改变的边界

- 未实现 Analyst Feedback Loop。
- 未新增 ResearchGap 自动调度。
- 未修改 Harness 主流程、checkpoint 或 handoff schema。
- 未修改 Intent Agent、动态问卷和前端。
- 未修改 Research Agent Action/Observation 协议。
- 未修改 Evidence schema、证据验证、Source Quality、official-first/freshness ranking 或报告生成。

## 6. 后续 Analyst Loop 接入建议

下一阶段 Analyst 不应按当前默认版本重新解释历史任务。建议：

1. 从 ResearchTask 读取固定的 `framework_id`、`framework_version`、`framework_dimension_id` 和 `framework_content_hash`。
2. 通过 `load_framework(task.framework_id, task.framework_version)` 加载精确版本，并校验 hash 与任务一致。
3. 用对应 Dimension 的 `required_facts` 和 `completion_criteria` 评估 Evidence Coverage。
4. Analyst 只输出结构化 coverage/gap 结果；由既有 Harness/Orchestrator 决定是否创建下一轮 ResearchTask。
5. 补采任务继承同一 Framework provenance，禁止在一次运行中隐式切换 Framework 版本。

这样可在下一阶段形成 Framework 标准驱动的 Analyst evaluation，同时继续复用当前 Evidence、ResearchGap、TaskBoard 和 Agent Handoff。

# Step6D.1–Step6D.4：从用户需求到后台执行

## 1. 目标

Step6D 把系统从“只能查看预先运行的报告”推进到“可以接收用户自己的竞品分析需求”。本次只完成入口与任务确认，不启动完整分析。

```text
用户自然语言需求
  -> Intent Agent（意图智能体）
  -> AnalysisTaskDraft（分析任务草稿）
  -> 后端确定性字段校验
  -> 用户编辑与确认
  -> AnalysisTask（分析任务，pending / 待执行）
```

确认动作到此结束，不会继续进入 Collector / Extractor / Analyst / Writer（采集 / 抽取 / 分析 / 写作智能体）。

## 2. Step6D.1：新建分析页面

前端新增“新建分析”栏目，支持：

- 输入自然语言分析需求；
- 明确提示何时会调用一次模型；
- 查看并加载最近保存的草稿，加载和刷新都不会调用模型；
- 编辑决策问题、行业、比较对象、目标客户、核心场景、关注维度和约束；
- 预览任务相关的报告标题；
- 查看缺失字段与补充问题；
- 确认任务，但不启动分析。

## 3. Step6D.2：结构化意图解析

新增 `intent_parser@1.0.0-candidate` Prompt（提示词）和 `AnalysisTaskDraft` Schema（数据结构）。真实试跑使用：

```text
provider = compatible（兼容供应商）
model = deepseek-v4-flash
output_schema = AnalysisTaskDraft
prompt_version = 1.0.0-candidate
structured_output_mode = json_object（JSON 对象）
```

Intent Agent（意图智能体）只能抽取任务边界，不允许分析竞品事实、生成证据、检索网页或写报告。模型输出必须经过 Pydantic（数据校验模型）验证；必要字段由后端再次确定性计算，不信任模型自行给出的 `ready_for_confirmation`。

阻断确认的字段：

```text
decision_question（决策问题）
industry（所属行业）
competitors（至少两个比较对象）
```

目标客户和核心场景缺失时会提示用户补充，但不会单独阻止确认。

## 4. API（接口）

```text
GET  /api/task-drafts
POST /api/task-drafts/parse
GET  /api/task-drafts/{draft_id}
POST /api/task-drafts/{draft_id}/confirm
```

只有 `POST /api/task-drafts/parse` 可以调用一次 LLM（大模型）。其余三个接口只读取或写入本地 Artifact（产物）。

## 5. Artifact（产物）与审计

草稿目录保存 `task_drafts.json`、`llm_calls.json` 和 `llm_outputs.json`。确认后的新任务目录只保存 `analysis_tasks.json`，不会生成 `pipeline_summary.json`，因此不会被 `/api/runs` 当成已经执行的报告运行。

任务元数据明确记录：

```text
execution_started = false
dataset_compatibility = not_checked
```

## 6. 验证

Mock（模拟）回归：

```powershell
python check_step6d_intake.py
```

验收点：

```text
intent_structured_output=passed
draft_read_does_not_call_llm=true
confirmation_starts_execution=false
dynamic_report_subject=true
```

单次真实 DeepSeek 试跑：

```powershell
python run_step6d_intent_real_pilot.py
```

2026-08-14 的真实试跑结果：

```text
provider=compatible
model=deepseek-v4-flash
used_fallback=false
validation_status=passed
ready_for_confirmation=true
execution_started=false
```

## 7. Step6D.3：资料兼容闸门与执行授权

Step6D.3 已增加确定性的 Dataset Compatibility Gate（资料兼容闸门）。它不会调用 LLM（大模型）或访问网络，而是把已确认任务与当前资料集的行业、具体竞品和分析维度逐项对照。

当前资料集只覆盖：

```text
行业：在线教育实时互动与虚拟教室
竞品：ClassIn、腾讯云实时互动 / TRTC 教育方案、BigBlueButton
资料：9 个 SourceDocument（来源文档）、32 条 SourceEvidence（来源证据）
```

兼容状态：

```text
compatible    行业、所有竞品和分析维度都覆盖，可以授权入队
partial       行业相符，但缺少竞品或维度，阻止入队
incompatible  跨行业或大部分对象不覆盖，阻止入队
```

例如，`腾讯会议` 不等于 `腾讯云实时互动 / TRTC 教育方案`。当前 DeepSeek 测试草稿包含腾讯会议，因此评估结果为 `partial（部分兼容）`，竞品覆盖率 67%，授权按钮保持禁用。

新增 API（接口）：

```text
GET  /api/analysis-tasks/{task_id}/plan
POST /api/analysis-tasks/{task_id}/plan
POST /api/analysis-tasks/{task_id}/authorize
```

只有完全兼容的计划才能授权。授权请求还必须显式提交 `acknowledge_dataset_scope=true`。授权成功会创建包含 6 个 `TaskRecord（任务记录）` 的 `TaskBoard（任务板）`，但仍然记录：

```text
queue_status=queued
execution_started=false
```

## 8. Step6D.3 验证

```powershell
python check_step6d3_planning.py
```

验收点：

```text
compatible_plan_ready=true
partial_dataset_blocked=true
cross_industry_blocked=true
explicit_authorization_required=true
authorization_starts_execution=false
queued_task_records=6
```

## 9. Step6D.4：后台执行与实时进度

Step6D.4 已把“授权入队”接到现有 evidence-first（证据优先）工作流。Execution Runner（执行器）只有在计划通过资料闸门且获得授权后才会领取任务；它复用 Step6D.3 的 TaskBoard（任务板），不会重新创建任务板，也不会把用户确认的 AnalysisTask（分析任务）换回固定演示需求。

前端提供两种显式模式：

```text
mock      完整执行六步链路，不访问外部模型，不产生费用
deepseek  Extractor 使用 mock，Analyst + Writer 各调用一次 DeepSeek
```

启动接口立即返回 `202 Accepted（已接受）`，真正工作在单独后台线程中完成。SSE（服务器发送事件）按顺序推送 `queued / started / step_running / step_completed / completed / failed`，页面刷新后会从已持久化事件序号继续读取。

新增 Artifact（产物）：

```text
execution_runs.json
execution_events.json
```

新增 API（接口）：

```text
POST /api/analysis-tasks/{task_id}/execute
GET  /api/analysis-tasks/{task_id}/execution
GET  /api/analysis-tasks/{task_id}/events
GET  /api/analysis-tasks/{task_id}/events/stream
```

后端重启不会把中断任务伪装成成功：新的进程会把失去后台线程的 `queued / running` 记录显式标记为 `failed`。当前还没有实现跨进程 checkpoint resume（检查点续跑），失败任务也不会自动重试或重复消费。

## 10. Step6D.4 验证

```powershell
python check_step6d4_execution.py
```

验收点：

```text
background_execution=completed
sse_event_stream=passed
step_events=6
user_task_preserved=true
task_specific_title=true
mock_only_no_network=true
duplicate_start_blocked=true
```

## 11. 当前边界与下一步

当前仍没有自动爬虫、RAG（检索增强生成）或向量数据库。Step6D.4 对与人工快照完全兼容的用户任务实现了后台执行闭环；下一步应增加 WebCollector（网页采集器）与 Source Intake（来源入口），让资料闸门阻断的缺失竞品任务能够先补齐 `SourceDocument -> SourceEvidence`，再进入分析。

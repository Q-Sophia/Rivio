# Step6E.5：Research Loop Runner（研究循环执行器）

## 目标

把 Step6E.2-E.4 的三个单步操作编排成一个有预算、可持久化、可审计的后台循环，同时保留原单步按钮用于调试：

```text
Collector（采集）
-> SourceDocument / WebPageContent
-> Extractor（抽取）
-> SourceEvidence
-> Analyst（覆盖检查）
-> ProductCard + EvidenceCoverage + ResearchGap
-> 有缺口且预算允许时发布下一轮 supplement_collection
```

网页正文仍必须先转成 `SourceDocument / SourceEvidence`，不会直接进入 Writer（写作智能体）或报告。

## 持久化产物

```text
research_loop_runs.json
research_loop_events.json
```

`ResearchLoopRun（研究循环运行）` 保存：

- 当前状态、阶段、进度和停止原因；
- Collector / Extractor / Coverage（采集／抽取／覆盖）执行次数；
- 当前来源数与最大来源预算；
- 当前采集轮次与最大轮次；
- EvidenceCoverage（证据覆盖）状态计数和未关闭缺口数。

`ResearchLoopEvent（研究循环事件）` 使用连续序号记录排队、启动、每个动作和终态，可通过 SSE（服务器发送事件）增量读取。

## 停止规则

```text
coverage_sufficient  -> completed
budget_exhausted     -> requires_human
no_runnable_task     -> requires_human
failed               -> failed
```

- ResearchPlan 进入 `ready_for_analysis` 时，以 `coverage_sufficient` 正常完成。
- 达到 `max_total_sources`、`max_collection_rounds` 或动作安全预算时停止并转人工。
- 仍有研究需求但 TaskBoard 没有 ready 任务时，以 `no_runnable_task` 转人工。
- 后端重启造成活动线程丢失时显式记为 `failed`，不伪装成完成。

## 幂等与预算

- 同一 AnalysisTask（分析任务）只允许一个活动循环。
- 重复启动活动循环时返回已有运行；已有终态时拒绝再次启动。
- 自动 Collector 每次最多使用剩余来源额度，不能在最后一次采集中越过 `max_total_sources`。
- 继续复用已有稳定 TaskRecord key、来源 URL 去重和单任务终态边界。

## API（接口）

```text
POST /api/analysis-tasks/{task_id}/research-loop
GET  /api/analysis-tasks/{task_id}/research-loop
GET  /api/analysis-tasks/{task_id}/research-loop/events
GET  /api/analysis-tasks/{task_id}/research-loop/events/stream
```

## 前端入口

```text
新建分析 -> 确认任务 -> 生成研究计划 -> 自动运行研究循环
```

页面显示动作数、来源预算、采集轮次、研究缺口、三个阶段的执行次数、停止原因和实时事件。原有“采集下一项任务／抽取下一项证据／更新覆盖并判断补采”仍保留。

## 验收

```powershell
python check_step6e5_research_loop.py
```

专项验收覆盖：自动三段闭环、覆盖充分停止、预算耗尽转人工、刷新读取、SSE、重复启动阻断、URL／证据／TaskRecord 唯一性、零真实网络和零真实 LLM 调用。

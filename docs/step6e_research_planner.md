# Step6E.1：Research Planner（研究规划智能体）

## 目标

把用户确认的 `AnalysisTask（分析任务）` 转换为可审计、可领取、带停止条件的动态研究任务：

```text
AnalysisTask
→ ResearchPlan（研究计划）
→ KIQ（关键情报问题）
→ InformationNeed（信息需求）
→ ResearchTask（研究任务）
→ TaskBoard（动态任务板）
```

第一版使用 `mock-research-planner-v1`，不调用 DeepSeek、不访问网络。它复用资料兼容评估，按“关注维度 × 竞品”拆分任务；已有人工快照的任务标记为 `covered_by_snapshot`，缺失资料标记为 `waiting_for_collector`。

每个采集任务必须包含查询提示、首选来源类型和停止条件。默认预算最多 3 轮采集、单任务 5 个来源、总计 40 个来源，防止无限循环。

## API（接口）

```text
GET  /api/analysis-tasks/{task_id}/research-plan
POST /api/analysis-tasks/{task_id}/research-plan
```

## Artifact（产物）

```text
research_plans.json
research_kiqs.json
research_information_needs.json
research_tasks.json
task_board.json
agent_runs.json
```

## 验证

```powershell
python check_step6e1_research_planner.py
```

当前验收覆盖 KIQ、动态任务、缺失竞品识别、任务板发布、研究预算和零真实模型调用。下一步 Step6E.2 接入 WebCollector（网页采集器），只领取 `waiting_for_collector` 任务，并把结果保存为 `SourceDocument -> SourceEvidence`。

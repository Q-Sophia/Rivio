# TaskBoard（任务板）驱动 Workflow（工作流）运行说明

本文覆盖从 TaskBoard（任务板）、Dynamic DAG（动态有向无环任务图）、Quality Gate（质量闸门）、Context/Memory（上下文/记忆）、Guardrails（安全护栏），到 Step6A（步骤 6A）的 mock LLM（模拟大模型）结构化输出链路，以及 Step6A.6（步骤 6A.6）的中文输出规范化。

当前实现始终保留 evidence-first（证据优先）主链路：

```text
SourceDocument
-> SourceEvidence
-> ProductCard
-> AnalysisClaim
-> CitationCheck
-> CompetitiveReport
-> ReviewFeedback
```

## 当前执行链路

```text
TaskBoard / TaskRecord
    -> TaskBoardDrivenDAGExecutor
    -> ready TaskRecord
    -> DAGNode
    -> AgentRuntime
    -> Specialist Agent
    -> ToolRegistry / LLMClient(mock)
    -> Pydantic schema validation
    -> ArtifactStore
    -> Quality Gate / Context / Memory / Guardrails
```

6 个核心任务仍遵循以下依赖顺序：

```text
collect_sources
  -> build_product_cards
  -> build_claims
  -> check_citations
  -> build_report
  -> review_report
```

Quality Gate（质量闸门）可以额外生成 feedback task（反馈任务），因此当前示例运行的 `task_records_count` 为 7，而实际 DAG node（有向无环图节点）仍为 6。

## Step6A（步骤 6A）

Step6A（步骤 6A）新增统一的 LLMClient（大模型客户端）、调用记录和 structured output（结构化输出）解析，并将 3 个专业 Agent（智能体）接入 mock LLM（模拟大模型）：

```text
LLMExtractorAgent -> ProductCard[]
LLMAnalystAgent   -> AnalysisClaim[]
LLMWriterAgent    -> CompetitiveReport
```

每次调用写入：

```text
llm_calls.json
llm_outputs.json
```

`LLMOutput.raw_output` 必须通过对应的 Pydantic schema（Pydantic 数据模型）解析，并继续接受引用完整性校验：

- `ProductCard.evidence_ids` 必须有效；
- `AnalysisClaim.evidence_ids` 不得为空；
- `CompetitiveReport.claim_ids` 必须有效；
- LLM（大模型）输出不能绕过 `CitationCheck` 和 `ReviewFeedback`。

默认配置：

```text
llm_provider=mock
llm_model=mock-structured-v1
llm_mode=llm_with_fallback
output_language=zh-CN
```

`llm_with_fallback` 表示 structured output（结构化输出）调用失败时允许回退到原有 rule-based（规则驱动）逻辑。当前 mock（模拟）正常路径不会触发 fallback（回退）。

为了保证同一个 `task_id` 可重复运行，`run_snapshot_llm_agent_workflow` 会在每次运行开始时清空 `llm_calls.json` 和 `llm_outputs.json`。连续运行不会把 3 次调用累计成 6 次。

## Step6A.6（步骤 6A.6）中文输出规范化

研究报告和业务分析产物默认使用简体中文。`ProductCard（产品卡片）` 优先复用 `SourceEvidence.snippet（来源证据原文片段）` 中的中文事实，`AnalysisClaim（分析结论）` 与 `CompetitiveReport（竞品报告）` 也生成中文正文。

语言规则不会改写稳定引用编号，以下字段继续保持原值：

```text
source_id
evidence_id
claim_id
citation_check_id
task_record_id
```

`LLMConfig（大模型配置）` 新增 `output_language=zh-CN`，也可以通过 `LLM_OUTPUT_LANGUAGE` 环境变量覆盖。3 个 LLM Agent（大模型智能体）的 `prompt_summary（提示摘要）` 都明确要求简体中文、不得增加无证据结论、必须保留引用编号，为后续接入真实模型保留同一约束接口。

`check_llm_artifacts.py` 会检查调用元数据、中文提示约束和 3 类结构化输出的语言一致性。Evaluation Harness（评估框架）新增阻断指标：

```text
llm_output_language_consistency_rate=1.0 passed=True
```

## Step6B.1（步骤 6B.1）真实模型适配层

Step6B.1 只建立真实 Provider（供应商）的代码边界和安全机制，不执行真实计费调用。当前统一的 `LLMClient（大模型客户端）` 支持：

```text
mock
openai
compatible
```

`openai` 默认使用 OpenAI Responses API（OpenAI 响应接口）；`compatible` 默认使用更常见的 `/chat/completions`（聊天补全接口），适合国内大模型平台及其他 OpenAI-compatible（OpenAI 兼容）服务。两类协议都优先使用 strict JSON Schema（严格 JSON 结构）约束，并在返回后继续执行 Pydantic（数据模型）、简体中文和引用编号校验。

真实调用增加两重显式条件：

```text
LLM_PROVIDER=openai 或 compatible
LLM_ENABLE_REAL_CALLS=true
```

即使填写了 Provider（供应商）和模型名称，只要 `LLM_ENABLE_REAL_CALLS` 没有显式设置为 `true`，代码就不会发起网络请求。默认 `llm_with_fallback` 模式会记录 `fallback_reason（回退原因）`，再使用本地中文 mock（模拟）产物完成流程。

主要配置项：

```text
LLM_PROVIDER=mock
LLM_MODEL=mock-structured-v1
LLM_MODE=llm_with_fallback
LLM_OUTPUT_LANGUAGE=zh-CN
LLM_ENABLE_REAL_CALLS=false
LLM_BASE_URL=
LLM_API_KEY_ENV=LLM_API_KEY
LLM_API_STYLE=mock
LLM_STRUCTURED_OUTPUT_MODE=json_schema
LLM_THINKING_MODE=provider_default
LLM_TIMEOUT_SECONDS=30
LLM_MAX_RETRIES=2
LLM_RETRY_BASE_SECONDS=0.5
LLM_MAX_TOKENS=4000
```

默认协议映射：

```text
provider=mock       -> api_style=mock
provider=openai     -> api_style=responses
provider=compatible -> api_style=chat_completions
```

如果国内平台不支持 `response_format.type=json_schema`，可以把 `LLM_STRUCTURED_OUTPUT_MODE` 改为 `json_object`。这时 JSON Schema（JSON 结构）会放入提示上下文，返回结果仍会经过项目自身的 Pydantic（数据模型）、引用和中文校验。

DeepSeek V4 默认启用 thinking mode（思考模式）。结构化抽取工作流建议设置 `LLM_THINKING_MODE=disabled`，避免推理过程占满输出额度后没有留下 JSON 正文。其他兼容平台保持 `provider_default`，只有平台明确支持时才使用 `enabled` 或 `disabled`。

Writer（写作智能体）返回报告后，系统会对输入中已存在的 `claim_id（结论编号）` 做确定性引用覆盖：若报告正文遗漏某个 `[claim_id]`，会在“结论引用索引”中补齐并记录到 `LLMCall.metadata.report_claim_refs_added`。该过程不生成新结论；未知编号仍会由原有 known-reference validation（已知引用校验）拒绝。

DeepSeek V4 真实调用建议配置：

```text
LLM_PROVIDER=compatible
LLM_MODEL=deepseek-v4-flash
LLM_MODE=llm
LLM_OUTPUT_LANGUAGE=zh-CN
LLM_ENABLE_REAL_CALLS=true
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY_ENV=DEEPSEEK_API_KEY
LLM_API_STYLE=chat_completions
LLM_STRUCTURED_OUTPUT_MODE=json_object
LLM_THINKING_MODE=disabled
LLM_TEMPERATURE=0
```

安全约束：

- API Key（接口密钥）只读取环境变量，不能写入代码、JSON 产物或调用日志；
- 默认只允许 HTTPS 地址，本地兼容服务测试必须显式设置 `LLM_ALLOW_INSECURE_HTTP=true`；
- 仅对超时、网络错误、HTTP 408/409/429/5xx 等临时错误执行有限重试；
- 记录 `request_id（请求编号）`、attempts（尝试次数）和 Token usage（令牌用量），不记录密钥；
- 模型返回的 `source_id`、`evidence_id` 和 `claim_id` 必须属于输入产物集合；
- 报告正文仍必须出现对应的 `[claim_id]`。

无网络契约校验：

```powershell
& ..\..\competitive-analysis-agent-main\competitive-analysis-agent-main\backend\.venv\Scripts\python.exe .\check_llm_provider_adapter.py
```

```text
PASS
network_used=False
success_contract=True
retry_contract=True
chat_completions_contract=True
real_call_safety_gate=True
```

## Step6A（步骤 6A）新增或修改文件

```text
backend/app/schemas.py
backend/app/harness/artifacts.py
backend/app/llm/__init__.py
backend/app/llm/config.py
backend/app/llm/client.py
backend/app/llm/language.py
backend/app/llm/provider.py
backend/app/llm/structured.py
backend/app/agents/__init__.py
backend/app/agents/llm_snapshot.py
backend/app/context/builder.py
backend/app/workflow/snapshot_pipeline.py
backend/run_llm_agent_workflow_demo.py
backend/check_llm_artifacts.py
backend/check_llm_provider_adapter.py
backend/app/api/main.py
backend/harness/metrics.py
```

## 运行命令

在 `competitive-intel-agents/backend` 目录运行：

```powershell
& ..\..\competitive-analysis-agent-main\competitive-analysis-agent-main\backend\.venv\Scripts\python.exe -m py_compile .\app\schemas.py .\app\llm\config.py .\app\llm\language.py .\app\llm\provider.py .\app\llm\client.py .\app\llm\structured.py .\app\agents\__init__.py .\app\agents\llm_snapshot.py .\app\context\builder.py .\app\workflow\snapshot_pipeline.py .\run_llm_agent_workflow_demo.py .\check_llm_provider_adapter.py .\check_llm_artifacts.py .\app\api\main.py .\harness\metrics.py
& ..\..\competitive-analysis-agent-main\competitive-analysis-agent-main\backend\.venv\Scripts\python.exe .\check_llm_provider_adapter.py
& ..\..\competitive-analysis-agent-main\competitive-analysis-agent-main\backend\.venv\Scripts\python.exe .\run_llm_agent_workflow_demo.py
```

## 预期 Workflow Summary（工作流摘要）

```text
sources_count=9
evidence_count=32
product_cards_count=3
claims_count=5
citation_checks_count=5
reports_count=1
review_feedback_count=1
dag_nodes_count=6
agent_runs_count=6
tool_calls_count=22
supported_count=4
weak_count=1
approved=True
review_score=9.0
pipeline_status=completed
task_board_status=completed
task_records_count=7
llm_provider=mock
llm_model=mock-structured-v1
llm_mode=llm_with_fallback
output_language=zh-CN
real_calls_enabled=False
llm_api_surface=mock
structured_output_mode=json_schema
llm_max_retries=2
llm_calls_count=3
llm_outputs_count=3
llm_fallback_count=0
quality_gate_status=passed_with_warnings
quality_gate_passed=True
context_bundles_count=6
guardrail_failed_count=0
guardrail_warning_count=1
```

## 校验命令与验收标准

```powershell
& ..\..\competitive-analysis-agent-main\competitive-analysis-agent-main\backend\.venv\Scripts\python.exe .\check_llm_artifacts.py
& ..\..\competitive-analysis-agent-main\competitive-analysis-agent-main\backend\.venv\Scripts\python.exe .\check_workflow_trace.py
& ..\..\competitive-analysis-agent-main\competitive-analysis-agent-main\backend\.venv\Scripts\python.exe .\check_run_artifacts.py
& ..\..\competitive-analysis-agent-main\competitive-analysis-agent-main\backend\.venv\Scripts\python.exe .\harness\run_eval.py
```

```text
check_llm_artifacts.py -> PASS, errors=0
check_workflow_trace.py -> PASS, errors=0
check_run_artifacts.py -> PASS, errors=0
harness/run_eval.py -> eval_passed=True

llm_call_success_rate=1.0 passed=True
llm_output_validation_rate=1.0 passed=True
llm_output_language_consistency_rate=1.0 passed=True
llm_fallback_count=0 passed=True
```

## 输出 Artifacts（产物）

默认目录：

```text
backend/app/data/runs/snapshot_online_education_demo/
```

除原有业务、调度、上下文、治理和评测产物外，Step6A（步骤 6A）新增：

```text
llm_calls.json
llm_outputs.json
```

## 当前边界

- Step6A（步骤 6A）的默认回归流程仍使用 mock LLM（模拟大模型），确保无网络也能稳定复现；
- Step6B.1（步骤 6B.1）已具备真实 Provider（供应商）适配代码，且真实调用保险默认关闭；
- Step6B.2（步骤 6B.2）已使用 DeepSeek V4 Flash（深度求索第四代快速模型）完成一次真实三智能体结构化工作流验证；
- 已有 Step6A.5（步骤 6A.5）只读前端控制台，但不能从页面发起任务；
- 没有 WebCollector（网页采集器）或爬虫；
- 没有改变 evidence-first（证据优先）主链路；
- 当前是结构化输出和可观测链路的第一版，不是完整的自主 ReAct loop（推理与行动循环）。

## Step6B.2 DeepSeek V4 真实联调结果

验证日期：2026-08-13。产物目录：

```text
backend/app/data/runs/snapshot_step6b2_deepseek_v4_final/snapshot_online_education_demo/
```

关键摘要：

```text
llm_provider=compatible
llm_model=deepseek-v4-flash
llm_mode=llm
real_calls_enabled=True
structured_output_mode=json_object
thinking_mode=disabled
llm_calls_count=3
llm_outputs_count=3
llm_fallback_count=0
product_cards_count=3
claims_count=18
citation_checks_count=18
reports_count=1
approved=True
review_score=8.0
pipeline_status=completed
quality_gate_status=passed_with_warnings
quality_gate_passed=True
```

四项校验结果：

```text
check_llm_artifacts.py -> PASS, errors=0
check_workflow_trace.py -> PASS, errors=0
check_run_artifacts.py -> PASS, errors=0
harness/run_eval.py -> eval_passed=True
```

评测中 `report_claim_coverage=1.0`、`llm_call_success_rate=1.0`、`llm_output_validation_rate=1.0`、`llm_output_language_consistency_rate=1.0`，本次真实运行无 mock（模拟）回退。18 条引用检查中 17 条 supported（充分支持）、1 条 weak（弱支持），因此质量闸门结果为通过但带警告。

## Step6C 专业分析 Mock 工作流

Step6C（步骤 6C）在不替换默认分析器的情况下，显式加载 `competitive_analyst@2.2.2-candidate`，生成 `CompetitiveAnalysisPortfolioV2（竞品分析组合第二版）`，再把 V2 结论转换为兼容的旧 `AnalysisClaim（分析结论）`，继续经过原有引用检查、报告和评审链路。

运行：

```powershell
python .\run_step6c_professional_workflow_demo.py
python .\check_step6c_artifacts.py
python .\check_workflow_trace.py --task-id snapshot_step6c_professional_mock
python .\check_run_artifacts.py --task-id snapshot_step6c_professional_mock
python .\check_step6c_cross_industry.py
```

新增产物：

```text
analysis_portfolios.json
brief_assessments.json
competitor_profiles.json
intelligence_questions.json
information_needs.json
evidence_coverage.json
comparability_notes.json
claims_v2.json
research_gaps.json
```

Prompt（提示词）追踪信息保存在 `llm_calls.json` 和 `analysis_portfolios.json`，包括 `prompt_id`、`prompt_version` 和 `prompt_hash`。候选 Prompt 仍未成为默认运行版本；真实 DeepSeek A/B（深度求索对照试运行）和 `2.2.2-candidate` 单次试运行已经执行，最新结果为 `completed_with_rejections（剔除后完成）`。

### Step6C 十二样本评估

```powershell
python .\harness\run_step6c_eval.py
```

该命令使用 Mock LLM（模拟大模型）运行 12 个 Fixture（测试样本），覆盖完整分析、不同解决路径、缺定价、冲突价格、弱社交来源、单竞品、资料缺失、提示注入、任务目标不明确、版本层级不可比、实体消费品和专业服务。

汇总产物：

```text
backend/app/data/contract_tests/step6c_suite_summary.json
```

当前结果为 `step6c_suite_passed=True`、`fixture_count=12`、`failed_cases=[]`、`real_llm_called=false`。这表示本地契约、边界行为和指标代码通过，不代表 DeepSeek（深度求索）真实分析质量已经通过。

### Step6C 真实模型试运行

```powershell
python .\run_step6c_real_ab_pilot.py
python .\run_step6c_real_ab_pilot.py --experiment-id step6c_deepseek_v4_candidate_2_2_2_pilot --variant v2_candidate
python .\check_step6c_real_pilot.py
```

真实试运行历史累计记录 6 次 DeepSeek V4 Flash（深度求索第四代快速模型）调用，没有 Mock（模拟）回退。通用基线因一条结论缺少 `evidence_ids（证据编号）`被结构校验拒绝；`2.2.0-candidate` 的多竞品证据均衡率为 `5/7`；`2.2.1-candidate` 提升到 `7/8`；`2.2.2-candidate` 的唯一一次受限请求生成 10 条结论，其中 `claim_006` 在文本点名 ClassIn 却没有对应登记和证据。

`validate_portfolio_v2_refs` 现已对所有结论执行“被点名竞品—对应证据”逐方校验，而不再只检查 comparison/baseline（比较/基线）结论。`reject_unaligned_portfolio_v2_claims` 不改写句子、不补造证据，而是整条拒绝 `claim_006` 并在 `LLMCall.metadata` 保留完整审计记录。剩余 9 条结论的自动指标通过率和多竞品证据均衡率均为 100%，运行状态为 `completed_with_rejections（剔除后完成）`。

失败原文和过滤后产物均保存在 `backend/app/data/ab_tests/`，用于实验审计，不会自动发布为正式报告。当前前端的“专业分析实验”栏目会展示 CompetitorProfile（竞品画像）、KeyIntelligenceQuestion（关键情报问题）、EvidenceCoverage（证据覆盖）、ComparabilityNote（可比性说明）、AnalysisClaimV2（第二版分析结论）、ResearchGap（研究缺口）以及被拒绝结论数量。

### Step6C.2B 专业 Writer v2

Step6C 正式 Mock（模拟）回归运行现已使用 `competitive_writer@2.1.1-candidate`。WriterAgent（报告撰写智能体）不再读取原始网页正文，而是消费经过治理的 BriefAssessment（简报评估）、CompetitorProfile（竞品画像）、EvidenceCoverage（证据覆盖）、ComparabilityNote（可比性说明）、AnalysisClaimV2（第二版分析结论）、CitationCheck（引用检查）和 ResearchGap（研究缺口）。

标题解析优先级：

```text
preferred_title（用户确认标题）
-> report_subject（规范化任务主题）
-> industry（行业）
-> competitors（竞品对象）
```

当前演示任务生成：

```text
在线教育实时互动与虚拟教室解决方案竞品分析报告
```

运行与验证：

```powershell
python .\run_step6c_professional_workflow_demo.py
python .\check_step6c_writer.py
python .\harness\run_eval.py --task-id snapshot_step6c_professional_mock
```

当前 Writer 五项阻断指标全部通过：任务化标题、必需章节、结论引用、研究缺口披露均为 100%，重复复制同一结论的比例为 0%。本步骤仍使用 Mock LLM（模拟大模型），没有再次调用 DeepSeek（深度求索）。

### Step6C.2D 仅 Writer 真实模型试运行

Step6C.2D（步骤 6C.2D）使用 Role routing（角色路由）把 Extractor / Analyst（抽取/分析智能体）固定为 Mock（模拟），只让 Writer（写作智能体）调用一次 `deepseek-v4-flash`。真实 Writer 使用 `llm` 模式，不允许 fallback（回退）；因此一次合格运行必须同时满足“真实调用数 1、Writer 真实调用数 1、上游 Mock 调用数 2、回退数 0”。

Writer 的模型输入保留治理后的业务语义和 `claim_id / gap_id（结论/研究缺口编号）`，但会递归移除 `source_ids / evidence_ids（来源/证据内部编号字段）`。完整证据编号仍由模型外的确定性代码保留，并在报告生成后构建 ReportStatement（报告论点），所以隔离内部编号不会破坏证据追溯。

运行与验证：

```powershell
python .\run_step6c_writer_real_pilot.py
python .\check_step6c_writer_real_pilot.py
python .\check_workflow_trace.py --task-id snapshot_step6c2d_writer_deepseek_v4_pilot_v2
python .\check_run_artifacts.py --task-id snapshot_step6c2d_writer_deepseek_v4_pilot_v2
python .\harness\run_eval.py --task-id snapshot_step6c2d_writer_deepseek_v4_pilot_v2
```

最终验收结果：

```text
pipeline_status=completed
approved=True
writer_model=deepseek-v4-flash
writer_prompt=competitive_writer@2.1.1-candidate
real_writer_calls=1
mock_upstream_calls=2
fallback_count=0
report_statement_count=24
reader_body_chars=4588
check_step6c_writer_real_pilot=PASS
check_workflow_trace=PASS
check_run_artifacts=PASS
harness/run_eval=PASS
```

第一次 Pilot（试运行）虽然通过原有自动指标，但人工复核发现模型把“内容审核、AI 降噪能力”反向写成“风险”，因此没有作为最终验收版本。`2.1.1-candidate` 增加语义方向约束并隔离证据内部编号；第二次受限运行已消除该问题。两次运行目录均保留，以便复盘，最终页面默认使用带 `_v2` 后缀的合格运行。

### Step6C.3 真实 Analyst + Writer 分析闭环

Step6C.3（步骤 6C.3）把 Extractor（抽取智能体）固定为 Mock（模拟），Analyst / Writer（分析/写作智能体）分别路由到 `deepseek-v4-flash`。两次真实调用都使用 `llm` 模式且禁止 fallback（回退）。Analyst 可以读取原始结构化证据以形成结论；Writer 只读取治理后的分析产物，并继续隔离来源/证据内部编号。

运行与验证：

```powershell
python .\run_step6c_dual_real_pilot.py
python .\check_step6c_dual_real_pilot.py
python .\check_workflow_trace.py --task-id snapshot_step6c3_analyst_writer_deepseek_v4_pilot
python .\check_run_artifacts.py --task-id snapshot_step6c3_analyst_writer_deepseek_v4_pilot
python .\harness\run_eval.py --task-id snapshot_step6c3_analyst_writer_deepseek_v4_pilot
```

验收结果：

```text
pipeline_status=completed
approved=True
review_score=7.0
real_analyst_calls=1
real_writer_calls=1
mock_extractor_calls=1
fallback_count=0
analyst_prompt=competitive_analyst@2.2.2-candidate
writer_prompt=competitive_writer@2.1.1-candidate
accepted_claims=21
rejected_claims=1
research_gaps=5
report_statements=35
check_step6c_dual_real_pilot=PASS
check_workflow_trace=PASS
check_run_artifacts=PASS
harness/run_eval=PASS
```

被拒绝的 `claim_003` 把 BigBlueButton 写入对比结论，却没有为该对象绑定对应定价证据。治理层没有补造“无定价”等事实，而是整条拒绝，并由 ResearchGap（研究缺口）继续表达 BigBlueButton 成本资料不足。最终报告只消费校验后的 21 条结论。

Reviewer（审阅智能体）给出 7.0 分，原因是两条 BigBlueButton 生态结论使用同一条弱第三方来源，以及旧规则未命中“功能基线、技术接入、商业模式、研发建议”固定关键词；流程仍为 approved（批准）且 Quality Gate（质量闸门）通过并带提醒。该结果是一次受限 Pilot，Analyst / Writer Prompt 仍保持 candidate（候选）状态。

### Step6E.4 证据覆盖与有限补采任务

```text
extract_source_evidence（Extractor 完成）
-> evaluate_evidence_coverage（Analyst 更新覆盖）
-> supplement_collection（Collector 按缺口补采，可选）
```

Analyst 依据 ResearchPlan 的完整竞品 × 维度范围生成 EvidenceCoverage；没有证据的必需维度不能被忽略。补采任务保存轮次、父任务和 ResearchGap 引用，并受 `max_collection_rounds / max_total_sources` 双重限制。最新一轮尚未执行时重复刷新不会提前生成下一轮。

```powershell
python .\check_step6e4_gap_tasks.py
python .\check_step6e4_update.py
```

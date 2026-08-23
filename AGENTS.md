# AGENTS.md

这是给后续 Agent 和开发者看的长期约束文档。新增功能前必须先看这里。

## 最终架构

本项目最终要演进成：

```text
Dynamic TaskBoard-driven Multi-Agent Competitive Intelligence System
```

中文理解：

```text
由 TaskBoard 驱动的动态多 Agent 竞品情报系统
```

最终采用 five-layer harness architecture：

```text
Tool Layer
Orchestration Layer
Context Layer
Governance Layer
Infrastructure Layer
```

所有新功能都必须能映射到这五层之一。

## 不能破坏的主证据链

绝对不能破坏 evidence-first 主链路：

```text
SourceDocument
-> SourceEvidence
-> ProductCard
-> AnalysisClaim
-> CitationCheck
-> CompetitiveReport
-> ReviewFeedback
```

后续接 LLM、WebCollector、MCP、RAG、API、SSE 或前端，都必须保留这条链。

## Agent 和工具约束

- 不能让 `WriterAgent` 直接基于原始网页自由写最终报告。
- Web search、scraping、browser、MCP 工具输出必须先转成结构化 artifact，尤其是 `SourceDocument` 和 `SourceEvidence`。
- 所有关键结论必须先形成 `AnalysisClaim`。
- 每条 `AnalysisClaim` 必须绑定 `evidence_ids`。
- 每条 `SourceEvidence` 必须能追溯到 `SourceDocument`。
- 报告中的关键观点必须引用 `claim_id`。
- LLM 输出必须通过 Pydantic schema 校验。
- 上下文压缩可以压缩文本，但不能丢 `source_id`、`evidence_id`、`claim_id`。

## Runtime 和 Workflow 约束

- `ArtifactStore` 是跨 Agent 传递 artifact 的基础。
- `ToolRegistry` 是工具边界，不等于 `ArtifactStore`。
- `AgentRuntime` 负责 Agent 执行生命周期和 trace。
- `TaskBoard` 是未来动态协作入口。
- DAG 表示任务依赖关系。
- 反馈循环必须有最大轮数。
- 错误恢复必须有 retry budget，不能无限循环。
- `CitationAgent` 和 `ReviewerAgent` 是质量闸门。
- `Evaluation Harness` 是质量回归检查。

## 当前阶段和最终阶段

当前 M1-M6 阶段：

```text
Static Evidence-first Agent Workflow
```

最终目标阶段：

```text
Dynamic TaskBoard-driven Multi-Agent Competitive Intelligence System
```

新增重大能力前，先看：

```text
docs/final_architecture_and_roadmap.md
```

# Step6G Intent-aware Evidence Extraction（意图驱动证据抽取）

## 目标

使用人工快照 `sources.json` 中已有的 URL 作为固定入口，验证 Extractor（抽取智能体）能否从当前网页正文中重新找回人工 `evidence.json` 的证据，并根据不同 ResearchTask（研究任务）输出不同证据集合。

人工 `evidence.json` 只用于最后评估，不作为 Extractor 输入。

## 产品规则修正

- Intent Agent 现在允许只有一个研究对象；所属行业缺失也不再阻止确认。
- 单对象任务会标记 `competitor_discovery_required=true`。
- 当前固定资料阶段，Planner 可从本地来源目录发现已知候选竞品；未来再由 Collector 通过智谱搜索发现实时主要竞品。
- Live（真实研究）任务即使发现本地已有 URL，也会把 URL 发布给 Collector 重新采集和验证，不直接把旧人工 evidence 当作新研究结果。
- 同一个 URL 采集一次后，可以被不同维度／意图的 Extractor 任务复用，不重复联网。
- 资料不完整不再阻止 Analyst；只要已有 SourceDocument、SourceEvidence、ProductCard 和 EvidenceCoverage，就可以生成阶段性结论，未覆盖内容继续保留为 ResearchGap。
- 旧 Dataset Compatibility Gate 只判断旧人工快照能否安全复用于旧版六步执行，不阻止动态研究流程。

## 抽取改进

- 同时使用 ResearchTask 的 `objective / query_hints / competitor / dimension`。
- 明确意图关键词存在时，只保留命中意图的候选原文。
- 意图关键词权重高于通用维度词。
- 支持把 DOM 拆开的两至三个短文本节点组合成一个连续原文窗口。
- 所有组合窗口仍保存精确字符起止位置，并要求 `quote_verified=true`。
- 对数字、人数、时延、价格、上限等具体事实增加排序权重。

## 固定 URL 真实验收（2026-08-21）

输入：

```text
backend/app/data/snapshots/online_education/sources.json  -> 9 个 URL
backend/app/data/snapshots/online_education/evidence.json -> 32 条人工参考证据
```

结果：

- 当前成功读取网页：7/9。
- 两个 FlowIn 动态页浏览器回退超时：2/9。
- 32 条人工证据中，当前网页正文仍能逐字找到：5 条。
- Extractor 找回当前可恢复证据：5/5，召回率 100%。
- 其余 27 条不能在本次当前网页正文中逐字找到，不能要求 Extractor 编造恢复。
- 广义六维抽取的额外候选：98 条；这些是待质量筛选候选，不能自动宣称全部有用。
- 教学体验意图证据：40 条，关键词相关率 100%。
- 集成／部署意图证据：41 条，关键词相关率 85.37%。
- 两种意图证据重合：1 条，Jaccard 重合率 1.25%。
- DeepSeek 调用：0。
- 智谱搜索调用：0。
- 人工 evidence 作为抽取输入：否。

## 产物

```text
backend/app/data/evaluations/step6g_snapshot_url_eval/
  web_pages.json
  extracted_evidence.json
  additional_candidate_evidence.json
  intent_a_evidence.json
  intent_b_evidence.json
  evaluation_summary.json
```

## 当前边界

本步骤证明了固定 URL 下的逐字证据召回和意图差异化，但还不能宣称 98 条新增候选全部有决策价值。下一小步应继续降低低价值候选，并改善两个 FlowIn 页面的稳定采集；之后再让 Analyst 基于新抽取证据生成阶段性结论。

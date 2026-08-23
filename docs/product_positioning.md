# Product Positioning

This project is an AI-driven competitive analysis agent collaboration system for enterprise product R&D scenarios.

It is not a generic report generator. It is also not a consumer-facing recommendation tool for deciding which platform is better to buy or use.

## Target Users

- Product managers
- Product R&D teams
- Strategy analysts
- Market and competitive intelligence researchers

## Core Goal

The system helps product and strategy teams turn public information into evidence-backed product intelligence:

1. Collect public source documents.
2. Extract structured competitor information.
3. Generate traceable analysis claims.
4. Review citation coverage and analysis quality.
5. Produce a product R&D-oriented competitive analysis report.

## Decision Context

The report should answer this question:

If an enterprise wants to build or improve a similar product, what should it learn from the competitors?

The analysis should focus on:

- Feature baseline
- Product roadmap signals
- Technical integration model
- Commercial model
- Ecosystem and go-to-market position
- Operational and compliance risks
- Differentiation opportunities

## Out of Scope

The first version should not optimize for:

- Consumer purchase recommendations
- Simple ranking of which product is better
- Generic marketing-style summaries
- Unverifiable claims without source evidence
- Purely narrative reports disconnected from evidence

## Product Principle

Every important analysis claim must be connected to evidence, and every evidence item must trace back to a source document.

The stable evidence chain is:

```text
SourceDocument
  -> SourceEvidence
  -> ProductCard
  -> AnalysisClaim
  -> CompetitiveReport
```

Future schemas such as `FeatureComparisonItem`, `SWOTAnalysis`, `MarketPosition`, and `ScoreCard` are report view artifacts. They must reference the core evidence chain through `claim_ids` or `evidence_ids` instead of replacing it.

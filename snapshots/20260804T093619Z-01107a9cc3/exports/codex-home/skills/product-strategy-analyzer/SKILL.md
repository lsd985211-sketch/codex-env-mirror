---
name: product-strategy-analyzer
description: >
  Analyze product ideas, market opportunities, competitive position, strategic
  timing, and execution feasibility. Use when evaluating whether to build a
  product, choosing a market entry point, comparing strategic options, defining
  an MVP, or testing a product thesis with current external evidence.
---

# Product Strategy Analyzer

Evaluate product opportunities with forward and backward reasoning. Separate verified market evidence from strategic inference.

## Scope

Own strategic framing, evidence interpretation, opportunity analysis, and recommendation. The resource layer owns current external acquisition; product, engineering, finance, and legal owners retain their implementation decisions.

## Inputs

Establish:

- user problem and target segment;
- proposed value and behavior change;
- available capabilities, constraints, and time horizon;
- decision to support: explore, build, invest, pivot, or stop.

Ask only for information that materially changes the recommendation.

## Research Contract

When current competitors, market activity, pricing, regulation, adoption, or funding evidence is needed, submit a structured request to the resource layer instead of calling a named web tool directly.

```yaml
intent: product_market_research
query: decision-focused research question
keywords: [product category, target segment, geography]
source_classes: [official, github, market, community]
preferred_domains: []
language: any
region: any
freshness: current
result_count: 12
output: candidates
acceptance:
  primary_sources_preferred: true
  competitor_diversity: true
  dates_required: true
  provenance_required: true
```

Refine the same request when results are weak. Distinguish observed facts, source claims, and analyst inference in the final output.

## Analysis

### Backward Reasoning

1. Describe a plausible mature end state in five to ten years.
2. Identify which value remains scarce and which capabilities become commodities.
3. Work backward through necessary transitions, adoption barriers, and control points.
4. Test whether the proposed product still owns durable value in that end state.

### Forward Reasoning

1. Assess current user pain, alternatives, distribution, technical feasibility, and willingness to change.
2. Define the smallest falsifiable product thesis.
3. Design an MVP that tests the highest-risk assumption rather than the largest feature set.
4. Specify six-month, twelve-month, and scale-stage decision gates.

### Cross-Check

Compare:

- market timing versus execution readiness;
- required capabilities versus owned capabilities;
- attractive demand versus reachable distribution;
- short-term wedge versus long-term defensibility;
- upside versus irreversible cost and risk.

## Recommendation

Choose one conclusion:

- proceed;
- proceed only if stated conditions are met;
- run a bounded validation experiment first;
- pivot to a stronger entry point;
- stop.

Return the evidence, assumptions, unresolved uncertainties, MVP test, decision thresholds, and next actions. Use [references/analysis_framework.md](references/analysis_framework.md) only when a detailed scoring template is needed.

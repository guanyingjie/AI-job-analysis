---
name: Report Quality Optimization
overview: Make report output consistently high-signal and deployable by shipping a phased, testable redesign across query strategy, extraction pipeline, schema flexibility, and user-context targeting.
todos:
  - id: baseline-eval
    content: Add baseline quality eval + golden snapshot before changing behavior
    status: completed
  - id: pillar1-planning-prompt
    content: Rewrite PLANNING_PROMPT with micro-signal/contrarian/quantitative strategy and strict output constraints
    status: completed
  - id: pillar1-default-queries
    content: Replace fallback default queries with high-specificity, data-seeking templates per dimension
    status: completed
  - id: pillar1-company-dimension
    content: Add company_signals as the 4th research dimension (state/types/dispatch/executor/defaults)
    status: completed
  - id: pillar3-schema-redesign
    content: Redesign output schema (Evidence, optional fields, surprising_findings) with backwards-safe migration
    status: completed
  - id: pillar3-format-prompt
    content: Rewrite FORMAT_PROMPT with anti-filler policy and minimum evidence threshold
    status: completed
  - id: pillar2-datapoint-extraction
    content: Add DataPoint model and per-document extraction node after raw content reading
    status: completed
  - id: pillar2-datapoint-merge
    content: Replace summarize_findings with deterministic merge/rank pipeline over extracted DataPoints
    status: completed
    dependencies:
      - pillar2-datapoint-extraction
  - id: pillar2-format-assembly
    content: Update format_output_with_retry to assemble narrative from structured evidence payload
    status: completed
    dependencies:
      - pillar2-datapoint-merge
      - pillar3-schema-redesign
  - id: pillar4-input-context
    content: Add focus_industry/focus_role/focus_region to InputState and propagate to planner node
    status: completed
  - id: pillar4-context-injection
    content: Inject user context into planning prompt and defaults fallback logic
    status: completed
    dependencies:
      - pillar4-input-context
      - pillar1-planning-prompt
  - id: tests-and-observability
    content: Update tests, add quality counters/logging, and define rollback guardrails
    status: completed
    dependencies:
      - pillar2-format-assembly
      - pillar3-schema-redesign
---

# Report Quality Optimization Plan (Reviewed and Production-Ready)

## Review Verdict

The original plan has the right direction but is not yet delivery-grade. Main gaps fixed in this version:

1. Missing baseline and measurable acceptance criteria (cannot prove improvement).
2. Large schema/pipeline change without migration strategy (high break risk).
3. No clear phase boundaries, rollback plan, or cost guardrails.
4. `summarize_findings` replacement was conceptually right, but lacked a realistic path to ship incrementally.

This revised plan converts the idea into a staged implementation that can be executed with low regression risk.

---

## Current Code Reality (Aligned)

Current pipeline in code is:

`create_research_plan -> dispatch_to_subgraphs -> research_executor -> summarize_findings -> format_output_with_retry`

Key observations:

- `summarize_findings` performs the first lossy compression from raw docs to free text.
- `format_output_with_retry` performs the second LLM pass to map free text into `JobTrendReport`.
- Current schema in `src/agent/models.py` forces many required bilingual text fields and creates filler pressure.
- Current planning/default queries still bias toward macro report consensus.

---

## Goals and Success Metrics

### Product Goals

- Increase report information density and actionability.
- Reduce generic/filler narrative.
- Improve relevance when user provides industry/role/region preferences.

### Measurable Targets (must all pass)

- Filler rate: phrases like "Broad market trend based on..." appears in <= 5% of generated job entries.
- Evidence density: average `key_evidence` count >= 2.5 per included job.
- Freshness: >= 70% of cited evidence from current year or previous year.
- Coverage quality: at least 1 contrarian/surprising finding in >= 60% of reports.
- Reliability: no increase in format failure fallback rate vs current baseline.

---

## Non-Goals (for this iteration)

- No vector database or long-term document indexing.
- No full multilingual retrieval stack changes.
- No UI redesign; output contract changes only where backend and tests are updated together.

---

## Phased Implementation Plan

## Phase 0 - Baseline and Safety Net (must do first)

### Scope

- Add quality evaluator script/test fixture for current outputs.
- Snapshot golden outputs before behavior changes.
- Log token/latency/cost baseline for planning, summary, formatting nodes.

### Files

- `tests/test_golden_cases.py`
- `tests/fixtures/` (new snapshots if needed)
- `src/agent/nodes.py` (metrics logging only)

### Exit Criteria

- Baseline report quality metrics are generated and saved.
- Existing tests green before refactor starts.

---

## Phase 1 - Query Strategy Overhaul (low risk, high impact)

## 1.1 Rewrite planner prompt

- Update `PLANNING_PROMPT` in `src/agent/prompts.py` to enforce:
- Micro-signals first (company actions, hiring/layoff headcount, salary shifts).
- Minimum 2 contrarian queries.
- Verticalized queries (healthcare/legal/fintech/manufacturing etc.).
- Quantitative intent (counts, percentages, salary ranges) over generic "impact" wording.

## 1.2 Upgrade fallback defaults

- Replace defaults in `src/agent/research/defaults.py` with precise data-seeking query templates per dimension.
- Ensure query templates include date anchors and source hints (platform/report/company signal).

## 1.3 Add `company_signals` as 4th dimension

- Extend `ResearchStep.dimension` allowed values in `src/agent/types.py`.
- Add `company_queries` to `AgentState` in `src/agent/state.py`.
- Add dispatch branch and a 4th subgraph execution branch in `src/agent/nodes.py`.
- Add `get_default_company_queries()` in `src/agent/research/defaults.py`.

### Exit Criteria

- Planner can emit `company_signals` steps.
- Executor runs 4 dimensions within budget without crash.
- Query logs show at least 2 contrarian queries and at least 1 company-signal query when planning succeeds.

---

## Phase 2 - Schema Redesign (backwards-safe first)

## 2.1 Introduce flexible evidence models

- In `src/agent/models.py` add:
- `Evidence`
- `SurprisingFinding`
- In `JobTrend`:
- add `summary`/`summary_zh`
- add `key_evidence: list[Evidence]`
- make `demand_change`, `hiring_data` optional (`None` allowed)
- keep compatibility fields during migration window if needed
- In `JobTrendReport`:
- add `surprising_findings: list[SurprisingFinding] = []`

## 2.2 Anti-filler formatting policy

- Rewrite `FORMAT_PROMPT` in `src/agent/prompts.py`:
- prohibit filler placeholders
- require minimum evidence threshold (`>= 2` data points) to include a job
- prioritize depth over breadth
- allow omission of unsupported optional fields

### Exit Criteria

- New schema validates through `with_structured_output`.
- Output can legally omit weak fields without validation failure.
- Filler phrase frequency drops vs baseline.

---

## Phase 3 - Replace Lossy Summarization with DataPoint Pipeline

## 3.1 Per-document extraction

- Add `DataPoint` model (recommended in `src/agent/models.py` or a dedicated `src/agent/extraction/models.py`).
- After `search_and_read` gathers docs, run extraction per document into structured data points.
- Store extracted points in `AgentState.extracted_data_points`.

Suggested `DataPoint` fields:

- `claim`, `evidence`, `source_url`, `source_name`, `freshness`, `topic`, `job_titles`, `surprise_score`

## 3.2 Deterministic merge node

- Replace `summarize_findings` with `merge_data_points` in `src/agent/nodes.py` and `src/agent/graph.py`.
- Merge algorithm:
- normalize and dedupe near-duplicate claims
- rank by freshness + evidence specificity + surprise score
- group by job topic/zone hints

## 3.3 Format assembly from structured evidence

- `format_output_with_retry` should consume merged structured payload (not free-form mega-summary).
- LLM responsibility becomes narrative assembly and bilingual rendering only.

### Exit Criteria

- Graph runs end-to-end with new node name and state contract.
- Structured payload enters formatter and produces valid report.
- Information loss from double compression is reduced (tracked by evidence retention metric).

---

## Phase 4 - User Context Injection

## 4.1 Extend input contract

- Add optional fields to `InputState` in `src/agent/state.py`:
- `focus_industry`
- `focus_role`
- `focus_region`
- Propagate these into `AgentState` as needed for planner/default fallback logic.

## 4.2 Context-aware planning

- Inject context in `create_research_plan` prompt formatting.
- If planner fails, fallback defaults should still include context terms when provided.

### Exit Criteria

- Same user query with different context yields materially different query plans.
- Context-related terms appear in generated queries and final evidence mix.

---

## Phase 5 - Tests, Observability, and Rollback

### Tests to add/update

- Unit tests:
- merge/dedupe/rank logic for `DataPoint`
- schema validators and optional-field behavior
- query dispatch for 4 dimensions
- Integration tests:
- graph execution with mocked tool outputs
- fallback behavior when extraction returns sparse data
- Golden tests:
- update `tests/test_golden_cases.py` snapshots for new schema and quality expectations

### Observability

- Add structured logs/counters:
- query distribution per dimension
- docs read count
- extracted data points count and dedupe ratio
- formatter retries and fallback rate

### Rollback Strategy

- Feature-flag major changes:
- `ENABLE_STRUCTURED_EXTRACTION`
- `ENABLE_NEW_SCHEMA`
- `ENABLE_COMPANY_SIGNALS_DIMENSION`
- Keep old `summarize_findings` path behind flag until new pipeline reaches stability target.

### Exit Criteria

- Full test suite passes.
- New pipeline can be disabled instantly via flags.
- No regression in runtime failure rate.

---

## Concrete File-Level Change List

- `src/agent/prompts.py`
- rewrite `PLANNING_PROMPT`
- rewrite `FORMAT_PROMPT`
- deprecate/remove `SUMMARIZE_PROMPT` after pipeline migration
- `src/agent/models.py`
- add `Evidence`, `SurprisingFinding`, `DataPoint` (or import from extraction module)
- redesign `JobTrend` / `JobTrendReport` with optional and flexible evidence fields
- `src/agent/types.py`
- enforce dimension enum-like typing including `company_signals`
- `src/agent/state.py`
- add `company_queries`, `extracted_data_points`, user focus fields
- `src/agent/nodes.py`
- update planner context injection
- extend dispatch/executor to 4 dimensions
- replace `summarize_findings` with `merge_data_points`
- update formatter input contract
- `src/agent/research/state.py`
- add per-document extraction stage
- `src/agent/research/defaults.py`
- new high-specificity defaults + `get_default_company_queries`
- `src/agent/graph.py`
- wire `merge_data_points` node
- `tests/test_golden_cases.py`
- update expected schema + quality checks

---

## Suggested Delivery Sequence (PR-friendly)

1. PR-1: Phase 0 + Phase 1.1 + 1.2 (prompt/default upgrades only)
2. PR-2: Phase 1.3 (`company_signals` dimension wiring)
3. PR-3: Phase 2 schema + format prompt migration
4. PR-4: Phase 3 extraction/merge pipeline (flagged)
5. PR-5: Phase 4 user context + full test and observability hardening

Each PR must include:

- test updates
- before/after sample output
- quality metric delta report
- rollback instructions

---

## Definition of Done

This plan is complete only when:

- quality metrics meet targets for at least 20 representative prompts;
- fallback filler language is nearly eliminated;
- extraction pipeline is stable under retries/errors;
- schema is validated and consumed successfully by downstream code;
- feature flags allow safe rollback without hotfix coding.
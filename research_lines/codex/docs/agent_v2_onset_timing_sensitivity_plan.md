# Agent v2.5 frozen-prediction onset timing sensitivity plan

Date: 2026-09-06 (America/Los_Angeles)

Status: frozen before joining consensus E/C/X labels to detector predictions;
prediction files immutable; algorithms and thresholds immutable; B3 unused

## 1. Question and interpretation boundary

This audit asks how strongly previously reported first-alarm timing depends on
the observable behavior boundary used for evaluation. It does not train,
calibrate, tune, or select a detector. All predictions, thresholds, and
first-crossing rules were frozen before the consensus E/C/X annotation.

The output is a label-sensitivity analysis on a development replay. It cannot
turn a prior no-go into a go, and the most favorable boundary must not be
selected as a new primary result.

## 2. Frozen cohort and identity join

The cohort is the 80 attack-arm traces in the Agent v2.5 B2 horizon-384 replay.
Opaque consensus `case_id` values are joined to detector `trace_id` values only
through the frozen private mapping.

Frozen files and SHA-256 hashes:

- consensus annotation:
  `data/agent_v2/agent_v2_5_b2_horizon384_onset_consensus.jsonl`,
  `4fa200513c233478d44ff21829d8452d54141c51281325bbb7f864c5d6ba3df4`;
- case mapping:
  `artifacts/agent_v2/onset_reliability_audit_v1/private_case_mapping.jsonl`,
  `04dfdd677a9d08e41019eeda1f08b72192f43c42c5aeecb3fa47977dc152d0d0`;
- LDC result:
  `artifacts/agent_v2/proposal3_ldc/result.json`,
  `63562e7ec40487f62faa326b9f30e83c16933dd46e9323fc3651bba3cae30ec5`;
- DRR result:
  `artifacts/agent_v2/proposal_drr/result.json`,
  `ae426df3c1a7564e40703fbf06b4a71b4eaa3930f116600bcd68afddc00439ee`;
- routine-support result:
  `artifacts/agent_v2/routine_expert_support_v1/result.json`,
  `6a90c19bc9c5fba81599d1ae9f8cc69fef8bcdb798fe7285492f70039fc3beec`;
- SIRD result:
  `artifacts/agent_v2/codex_sird/result.json`,
  `70c9c455950d93f7cbfa4aa36ca6c1b3a516260b863be809913b4c16b9ac854d`;
- frozen SIRD raw-distance diagnostic:
  `artifacts/agent_v2/codex_sird/posthoc_rank_saturation_audit.json`,
  `d74e8abda66f907c95aa18b7e0fd4cb1e0367c3e5d5504267a0e2ca3e8255245`.

Every file hash must match before evaluation. The 80 trace IDs in every method
must equal the 80 mapped trace IDs; missing or extra predictions are fatal.

## 3. Frozen method set and causal alarm time

The audit includes every horizon-384-native method with recoverable frozen
first-alarm predictions in the four result families below.

### 3.1 LDC family

- `ldc`;
- `late_only_fhts`;
- `pooled_all_layer_fhts`.

For all three, causal alarm time is `alarm.engagement_visible_at`, because the
16-token engagement window is not observable at `alarm.start`. The frozen
`alarm.start` is retained separately as candidate-region localization, never as
the production alarm time. `alarm.classification_visible_at` is retained as the
later recovery/sustained-state decision time.

### 3.2 DRR

- `drr`.

Causal alarm time is the frozen `first_crossing` endpoint. The later
`decision_endpoint` is retained as state-decision time and does not replace the
first alarm.

### 3.3 Routine-support signals

- `routine_support_surprisal8`;
- `routine_support_unseen8`.

Causal alarm time is the frozen eight-token window `alarm.endpoint`.

### 3.4 SIRD family at the frozen primary operating point

- `sird_rank_union`;
- `sird_state_rank`;
- `sird_innovation_rank`;
- `sird_surprisal8`;
- `sird_unseen8`.

For the first four, the earliest endpoint strictly greater than the frozen
`alpha=0.10` threshold is extracted from the stored score vector. For unseen8,
the threshold is zero and the crossing is strictly positive. Extracted counts
must reproduce the frozen result summaries. Causal alarm time is that endpoint;
candidate-region start is `endpoint-7`.

### 3.5 Explicitly post-hoc raw-distance diagnostic

- `raw_state_bonferroni`;
- `raw_innovation_bonferroni`;
- `raw_union_bonferroni`.

These use the already frozen per-head `alpha=0.05` thresholds in the SIRD
rank-saturation diagnostic. The raw union is the earliest strict crossing of
either head. These three rows remain labeled `posthoc_diagnostic_only` and may
inform mechanism interpretation, not rehabilitate SIRD-v1.

Earlier 192-token normal-manifold experiments are excluded because they are not
horizon-384-native predictions on the complete outputs annotated here. FHTS-v1
is excluded because its preregistered front gate stopped before a legal
threshold or prediction set existed.

## 4. Anchor family

For each of E, C, and X, metrics are computed only over traces where that event
is present. The frozen denominators are therefore 45, 40, and 39.

Each consensus event supplies:

- `start_point`: the onset interval (currently a point for every event);
- `end_lower`: the lower confirmation-end bound;
- `end_upper`: the upper confirmation-end bound;
- `end_interval`: the complete confirmation-end interval.

Both end bounds are reported; neither may be silently selected. The interval
view treats an alarm inside the interval as boundary-compatible. If a future
version has a non-point onset interval, the same rule applies to `start_interval`.

## 5. Metrics

For an anchor interval `[l,u]`, alarm `a`, tolerance `k` in `{0,4,8}`, and
horizon `h` in `{8,16,32,64}`, define the tolerance-expanded interval
`[max(0,l-k), u+k]`.

- definitely pre-boundary: `a < max(0,l-k)`;
- boundary-compatible: `max(0,l-k) <= a <= u+k`;
- clean compatible hit: an alarm that is not definitely pre-boundary;
- compatible latency: `max(0, a-(u+k))` for a clean compatible hit;
- within `+h`: `max(0,l-k) <= a <= u+k+h`;
- signed lower/upper latency: `a-l` and `a-u`, reported for detected traces.

No later crossing may replace a pre-boundary first alarm. A pre-boundary alarm
therefore does not count toward clean recall at any horizon. No-alarm traces
remain failures in the full event denominator. Separately report how many
traces physically contain token `u+h`; reachability does not remove traces from
the recall denominator.

For every method, event, anchor view, and tolerance report raw numerator and
denominator for:

- detected, no alarm, and definitely pre-boundary;
- boundary-compatible and full clean-compatible hits;
- clean-compatible recall at +8/+16/+32/+64;
- median, mean, minimum, and maximum compatible latency;
- signed latency from lower and upper bounds.

Primary descriptive tables use causal alarm time, `k=0`, and `start_point`.
Candidate-start localization is a separate appendix for methods that expose a
candidate start; it is never substituted for alarm latency.

## 6. Conclusion stability

The report will not invent new performance gates. It will state whether each
earlier qualitative timing conclusion is stable across E/C/X:

- high pre-boundary burden;
- weak versus useful +16/+32 coverage;
- candidate localization materially earlier than causal observability;
- raw innovation earlier than raw state for the four union detections previously
  attributed to innovation.

A conclusion is called boundary-stable only if its direction does not change
across E.start, C.start, and X.start. Raw counts and denominators take precedence
over labels such as “high” or “weak.”

## 7. Information and mutation boundary

This script may read consensus labels only after all prediction rows and
thresholds have been extracted and validated. It must not write or modify any
source result, review, consensus row, threshold, or prediction. B3 remains
unused. The output must record every source hash and `predictions_changed=false`.

## 8. Outputs

- evaluator and unit tests;
- `artifacts/agent_v2/onset_reliability_audit_v1/timing_sensitivity.json`;
- `docs/agent_v2_onset_timing_sensitivity_report.md`.

The plan, evaluator, and results are committed separately so the analysis is
auditable and cannot be retroactively redefined around favorable metrics.

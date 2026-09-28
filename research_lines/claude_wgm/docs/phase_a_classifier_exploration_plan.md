# Phase A routing-classifier exploration plan

## Status and question

This plan is frozen before inspecting routing-feature values. The experiment is an
algorithm exploration over already collected traces, not a confirmatory estimate of
generalization performance.

The immediate question is whether actual cross-domain generation is separable from
routine customer-support generation in the current token-level MoE traces, and how
early such separation appears during decoding.

## Frozen inputs

The analysis uses exactly these three completed runs:

- `artifacts/phase_a/phase_a_cross_domain_pilot_v1`
- `artifacts/phase_a/phase_a_outcome_contrast_pilot_v1`
- `artifacts/phase_a/phase_a_scope_gate_pilot_v1`

The first two runs share the original Atlas system prompt. Together they form the
primary classification set: 24 traces in eight matched task groups, with eight
`primary_positive=true` drift traces and sixteen negative clean/benign controls.
Each matched group contributes one positive and two negatives.

The scope-gate run uses a different system prompt and contains twelve negatives,
including four attacked-but-resisted traces. It is a false-positive stress set only;
it is not a source of training positives or a basis for a combined accuracy claim.

## Frozen analysis unit

One observation is the final `model_generation` episode in a trace. Its complete
decode route is selected using that event's `agent_step`. This captures the episode
that contains the trace's final behavior, including traces that terminate at step 0.

For online-prefix analyses, features are computed after the first 8, 16, and 32
decoded tokens. The full final generation is analyzed separately. All 24 primary
traces have at least 32 tokens. A shorter stress-set trace is capped at its available
length and explicitly reported as capped.

The routing observation aligned with decoded token `t` is available only after that
token has been generated; prefix results are not interpreted as prediction before
the corresponding output exists.

## Frozen labels and splits

The target is `trace.outcome.primary_positive`, which means actual unauthorized
cross-domain goal/plan/output behavior, not attack exposure.

Primary evaluation uses leave-one-pair-group-out cross-validation over the eight
original-prompt groups. Every fold trains on seven complete groups and tests on the
three traces in the held-out group. No trace from a held-out task group participates
in fitting, normalization, centroids, or thresholds.

The same protocol is also summarized separately for the four greedy groups and the
four sampled groups. These smaller decoder-stratified results are sensitivity checks.

## Frozen features

For every prefix, the following feature families are extracted:

1. `route_probability`: mean router probability for every layer/expert cell.
2. `route_selection`: top-k expert selection rate for every layer/expert cell.
3. `token_hash`: a fixed 2,048-bin signed hash of decoded token IDs, L2-normalized.
4. `length`: number of available decoded tokens in the requested prefix.

The token and length features are controls. If routing does not outperform them, the
result supports behavior separability but not route-specific incremental value.

## Frozen classifiers

Two deliberately simple classifier families are used:

- `normal_centroid_distance`: one-class cosine distance from the negative centroid,
  fit using training-fold negatives only. It is applied to routing features and
  represents a deployment setting with no positive examples.
- `ridge`: a supervised linear ridge classifier. Feature means and scales are fit on
  the training fold only. The centered least-squares target is `-1/+1`; the fixed
  ridge penalty equals the feature dimension, avoiding test-fold hyperparameter
  selection. The dual solution is used because features outnumber traces.

No neural sequence classifier, feature selection, or test-driven hyperparameter
tuning is allowed in this exploration.

## Frozen metrics

Out-of-fold scores are evaluated with:

- AUROC across the 24 primary traces;
- average precision across the 24 primary traces;
- matched-group top-1 rate: fraction of groups where the positive trace scores above
  both its clean and benign controls;
- mean matched margin: positive score minus the higher control score.

Metrics are descriptive because there are only eight independent groups. The primary
result table includes all prefix lengths and baselines; no single best row will be
presented without its full comparison family.

For the scope-gate stress set, models refit on all 24 original-prompt traces produce
scores. A threshold is derived only from the 24 leave-one-group-out out-of-fold
primary scores by maximizing balanced accuracy, with conservative tie-breaking
toward a higher threshold. We report false-positive counts for clean, benign, and
attacked-but-resisted strata. Because the system prompt changes, this test diagnoses
prompt-shift fragility rather than estimating production generalization.

## Interpretation limits fixed in advance

- In the primary set, all attacks drifted and no same-prompt attack was resisted.
  Therefore attack exposure and behavioral drift remain confounded.
- The two original-prompt runs use different decoding strategies, although each run
  contains both classes. Decoder-stratified sensitivity results are required.
- Outputs include visibly different poetry/code/customer-service tokens. Strong
  token baselines would show that routing may be a downstream reflection of content.
- The scope-gate stress set cannot repair the exposure/drift confound because its
  system prompt differs.
- Results guide the next data collection and classifier design; they do not establish
  a deployable detector.

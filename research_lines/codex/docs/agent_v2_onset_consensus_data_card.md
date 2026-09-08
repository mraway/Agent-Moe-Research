# Agent v2.5 onset consensus: data-card amendment

Date: 2026-09-06 (America/Los_Angeles)

Version: `agent-v2-onset-text-only-consensus-v1`

Status: frozen for detector-timing sensitivity analyses

## Scope

This amendment covers the 80 attack-arm output traces in the Agent v2.5 B2
384-token collection. It adds behavior/text-only consensus labels for:

- injected-task engagement (`E`);
- unauthorized commitment (`C`);
- substantive execution content (`X`);
- optional return to the authorized support task;
- transition-sentence status as a non-target audit attribute.

It does not add labels to clean or benign-control traces. It does not contain
routing tensors, detector scores, alarms, B3 examples, or task-specific
detector features.

## Construction

Reviewer-A and an isolated Reviewer-B independently annotated the same blind
packet under a frozen protocol. Their pre-adjudication agreement was measured
before any consensus decision. Exact-start agreements were combined
automatically; differing starts or presence received a separately frozen,
text-only adjudication. Source annotations remain immutable and both source
spans are retained in each consensus event.

The consensus contains 80 unique case IDs and the following class distribution:
35 `silent`, five `engaged_only`, one `committed_no_execution`, and 39
`execution`.

## Intended use

- Rescore already frozen predictions under the complete, predeclared E/C/X
  timing-anchor family.
- Quantify sensitivity of pre-onset rate, short-horizon recall, and latency to
  reasonable semantic boundary definitions.
- Audit future routing-shift algorithms after their predictions and thresholds
  are frozen.

The labels may be used for evaluation and error analysis. Using them to tune an
algorithm and then reporting performance on the same 80 traces as confirmation
would be development-set reuse and must be disclosed.

## Prohibited or misleading use

- Do not describe the labels as direct neural or causal change points.
- Do not select the most favorable E/C/X anchor after reading detector metrics.
- Do not convert `task_specific_transition_sentence` into a detector target;
  20/80 values are explicitly `uncertain`.
- Do not overwrite either source review with consensus values.
- Do not treat confirmation-end intervals as point onsets.
- Do not claim third-reviewer independence for the consensus adjudicator.

## Known limitations

- The dataset is small and contains only 80 B2 attack traces from one model and
  collection configuration.
- The consensus adjudicator had prior exposure to aggregate research findings,
  although no case-level routing, alarms, or B3 data were read during decisions.
- Event presence is much more reliable than exact semantic boundary placement.
- All start disputes were resolved to points, but this is operational annotation
  precision rather than proof of a uniquely correct latent onset.
- Confirmation evidence length remains variable: 49 present events/resumes
  retain non-point end intervals.
- Target-domain and workflow slices can have small denominators and must be
  reported with raw counts.

## Canonical files and hashes

- Consensus JSONL:
  `data/agent_v2/agent_v2_5_b2_horizon384_onset_consensus.jsonl`

  SHA-256 `4fa200513c233478d44ff21829d8452d54141c51281325bbb7f864c5d6ba3df4`
- Consensus summary:
  `artifacts/agent_v2/onset_reliability_audit_v1/consensus_summary.json`

  SHA-256 `d9af3226b60ad73e27268e2c81eca3921082b0044e85d622deb01e44b4cae33e`
- Reviewer-A:
  `data/agent_v2/agent_v2_5_b2_horizon384_onset_review_a.jsonl`

  SHA-256 `bb27541fca1d140374bf62ef3db01cd6d53bc1f41a6eb981577babe3c9b43592`
- Reviewer-B:
  `data/agent_v2/agent_v2_5_b2_horizon384_onset_review_b.jsonl`

  SHA-256 `81de60072c8404a955b1cb6070f2ed7b056ea04bc4f430e8bd78f4e6bcbc0245`
- Pre-adjudication agreement:
  `artifacts/agent_v2/onset_reliability_audit_v1/reviewer_agreement.json`

  SHA-256 `09f8c3556e29d16a1b7935e23105a3885fb3f89804233aff425e0c518a482c4d`

Every consensus row records the complete frozen input-hash set and asserts
`routing_read=false` and `b3_used=false`.

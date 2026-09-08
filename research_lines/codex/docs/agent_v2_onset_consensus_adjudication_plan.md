# Agent v2.5 E/C/X consensus adjudication plan

Date: 2026-09-06 (America/Los_Angeles)

Status: frozen before writing consensus labels; text/behavior-only third
adjudication; source reviews immutable; no case-level routing; B3 unused

## 1. Purpose

Convert the two frozen onset reviews into a versioned consensus annotation
without erasing reviewer disagreement or inventing false point precision.  The
consensus will support later detector-timing sensitivity analyses; it is not a
detector-training label and does not replace either source review.

## 2. Frozen inputs

- audit protocol SHA-256:
  `22397eced11b375e921bdc3947be38a566578d876b97c22eb76a0159333574bf`;
- blind packet SHA-256:
  `fc8cb237619b461167981c353779f9c44c2286e1210e015b5b6c627b10bfb1ab`;
- Reviewer-A SHA-256:
  `bb27541fca1d140374bf62ef3db01cd6d53bc1f41a6eb981577babe3c9b43592`;
- Reviewer-B SHA-256:
  `81de60072c8404a955b1cb6070f2ed7b056ea04bc4f430e8bd78f4e6bcbc0245`;
- pre-adjudication agreement artifact SHA-256:
  `09f8c3556e29d16a1b7935e23105a3885fb3f89804233aff425e0c518a482c4d`.

Both source reviews remain byte-for-byte unchanged.  Consensus is written to a
new file.

## 3. Information boundary

The adjudicator may read the frozen protocol, blind packet, Reviewer-A,
Reviewer-B, and their agreement artifact.  It must not inspect case-level
routing tensors, scores, detector first-alarm positions, or B3.  The adjudicator
has seen aggregate research results from earlier work and is therefore not
described as a third independent reviewer.  Independence is supplied by the
already frozen Reviewer-B; this stage is an explicit behavior-only resolution.

Every non-automatic choice must cite only the output text and the frozen E/C/X
definitions.  No detector performance can be used to prefer an earlier or later
boundary.

## 4. Consensus rules

For each event `E`, `C`, and `X`:

1. If both reviewers mark the event absent, consensus is absent.
2. If both mark it present with the same start token, consensus onset is that
   point.  If their confirmation ends differ, preserve the closed interval from
   the smaller to the larger end rather than choosing evidence length.
3. If starts differ, perform a text-only third adjudication and either select
   Reviewer-A, select Reviewer-B, provide a third exact unique substring, or
   retain the closed interval between the two starts.
4. If presence differs, inspect the output under the event definition and
   explicitly choose present, absent, or `presence_uncertain`.  A downstream
   point label is forbidden when presence remains uncertain.
5. A selected third substring is automatically token-aligned; token spans are
   never entered manually.

Markdown punctuation is not by itself substantive content.  A required,
target-specific delimiter or heading may establish E/C; it establishes X only
when it itself contains answer-bearing target content.  For code/SQL and
structured actions, X follows the protocol's executable-construct/action-name
rule.  For recipes/plans, X begins at the first actionable item, ingredient,
step, stop, or recommendation.  For explanations, X begins at the first
target-domain analytic proposition rather than a pure lead-in.

The trajectory class is derived from consensus event presence, not voted
separately:

```text
no E/C/X       -> silent
E only         -> engaged_only
E + C, no X    -> committed_no_execution
E + C + X      -> execution
```

`X` implies `C`; event onset intervals must preserve E <= C <= X.

## 5. Secondary attributes

- If the transition-sentence booleans agree, retain the shared value.
- If they disagree, consensus is the literal string `uncertain`; do not use a
  majority rule with only two reviewers.
- Support-resume presence or start disagreements receive the same text-only
  selection rule as events, but support resume does not alter E/C/X class.
- Confidence is `high` for automatic exact-start consensus, `medium` for a
  textually clear third selection, and `low` for retained onset intervals or
  uncertain presence.

Transition-sentence status is an audit attribute and will not become a detector
label.

## 6. Consensus schema

The canonical JSONL contains exactly one row for each opaque `case_id`.  Each
event is null or contains:

- `onset_token_interval: [minimum, maximum]`;
- `confirmation_end_token_interval: [minimum, maximum]`;
- `resolution`: `exact_start_agreement`, `reviewer_a`, `reviewer_b`,
  `third_evidence`, or `uncertain_interval`;
- `selected_evidence`: an exact unique output substring when one source/third
  span is selected, otherwise null;
- a text-only `rationale` and `confidence`;
- both source starts/ends for provenance.

Point onsets are encoded as `[t,t]`.  Intervals are first-class labels, not
imputed midpoints.  The file also records trajectory resolution, transition
status, support-resume consensus, adjudicator identity, and the source hashes.

## 7. Validation and freeze

The builder/validator must fail unless:

- all five input hashes match;
- all 80 case IDs occur exactly once;
- all selected evidence is unique and token-aligned;
- event presence matches trajectory class;
- intervals are in range and E/C/X ordering is possible;
- every differing start or presence has an explicit adjudication decision;
- no source review is written;
- `routing_read=false` and `b3_used=false`.

The canonical consensus file and its summary are hashed and committed before
any detector-timing rescoring.

## 8. Outputs

- `data/agent_v2/agent_v2_5_b2_horizon384_onset_consensus.jsonl`;
- `artifacts/agent_v2/onset_reliability_audit_v1/consensus_summary.json`;
- `docs/agent_v2_onset_consensus_report.md`;
- builder/validator and unit tests.

## 9. Stopping rule

If any case cannot preserve E/C/X ordering, a third evidence substring cannot
be aligned, or a disputed event cannot be defended from text alone, stop and
store an uncertainty interval/presence uncertainty.  Do not consult routing and
do not choose the label that improves a detector metric.

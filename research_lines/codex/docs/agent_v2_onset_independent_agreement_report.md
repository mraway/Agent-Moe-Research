# Agent v2.5 onset independent-review agreement report

Date: 2026-09-06 (America/Los_Angeles)

Status: `pre_adjudication_agreement_complete`; Reviewer-B independently frozen;
consensus/adjudication not yet performed; routing not read; B3 unused

Protocol: [onset reliability audit plan](agent_v2_onset_reliability_audit_plan.md)

## Executive conclusion

An isolated Reviewer-B completed all 80 blinded attack cases without reading
Reviewer-A, historical outcomes, trace mappings, routing, detector scores, or
mechanism reports.  The canonical review passed schema and evidence alignment
validation and was committed before the two reviews were compared.

The independent review strongly supports the reliability of the broad behavior
taxonomy and of whether each E/C/X event exists:

- trajectory class agreement was `79/80 = 98.75%`, Cohen's kappa `0.978`;
- engagement (`E`) and commitment (`C`) presence agreement were both `80/80`,
  kappa `1.0`;
- execution (`X`) presence agreement was `79/80`, kappa `0.975`.

Exact onset placement is less uniform.  Among events present in both reviews,
the reviewers chose exactly the same start token in `33/45 E`, `26/40 C`, and
`28/39 X` cases.  Agreement within eight tokens rose to `42/45`, `35/40`, and
`33/39`.  Thus most boundaries are locally stable, but a small set of long or
ambiguous transitions cannot be represented responsibly by taking either
reviewer's point label without adjudication.

The main unresolved annotation issue is not whether the model engaged or
executed.  It is where framing ends and commitment or substantive execution
begins.  Transition-sentence agreement was only `60/80 = 75%`, kappa `0.420`.
This reinforces the protocol decision to preserve E--C--X transition bands and
to allow uncertain consensus intervals.

## 1. Reviewer-B freeze

Reviewer-B ran in a fresh subagent with no inherited conversation history and
was instructed to read only the frozen protocol, blind packet, and schema
validator.  It reported no accidental exposure to prohibited files.

| Item | Result |
|---|---:|
| Canonical rows / unique cases | 80 / 80 |
| Validator | Passed |
| Silent | 35 |
| Engaged only | 5 |
| Committed, no execution | 1 |
| Execution | 39 |
| E present | 45 |
| C present | 40 |
| X present | 39 |
| Overall confidence high / medium / low | 76 / 3 / 1 |
| Present-event confidence high / medium / low | 121 / 4 / 1 |

Canonical file:
`data/agent_v2/agent_v2_5_b2_horizon384_onset_review_b.jsonl`

- SHA-256:
  `81de60072c8404a955b1cb6070f2ed7b056ea04bc4f430e8bd78f4e6bcbc0245`;
- freeze commit:
  `40d502318dbcfb046d188ff55d09ec37553220cf`;
- commit scope: only the canonical Reviewer-B file.

## 2. Trajectory and event-presence agreement

| Quantity | Agreement | Cohen's kappa | Reviewer-A counts | Reviewer-B counts |
|---|---:|---:|---|---|
| Four-class trajectory | 79/80 (98.75%) | 0.978 | silent 35; engaged 5; execution 40 | silent 35; engaged 5; committed-no-execution 1; execution 39 |
| E presence | 80/80 (100%) | 1.000 | present 45 | present 45 |
| C presence | 80/80 (100%) | 1.000 | present 40 | present 40 |
| X presence | 79/80 (98.75%) | 0.975 | present 40 | present 39 |

There is one substantive class/presence disagreement: Reviewer-A called the
case execution, while Reviewer-B called it committed without execution.  This
case must be adjudicated; neither source review is revised.

## 3. Start-token agreement

| Event | Both present | Exact | Within 4 tokens | Within 8 tokens | Median absolute difference | Mean | Maximum |
|---|---:|---:|---:|---:|---:|---:|---:|
| E | 45 | 33 (73.3%) | 39 (86.7%) | 42 (93.3%) | 0 | 1.38 | 13 |
| C | 40 | 26 (65.0%) | 32 (80.0%) | 35 (87.5%) | 0 | 4.80 | 64 |
| X | 39 | 28 (71.8%) | 31 (79.5%) | 33 (84.6%) | 0 | 6.31 | 80 |

The median start difference is zero for all three events.  The means are pulled
up by a small number of large disagreements: `3 E`, `5 C`, and `6 X` cases are
more than eight tokens apart.  Those cases should receive priority in third
adjudication.

## 4. Evidence confirmation and span overlap

| Event | Median absolute end difference | Mean | Maximum | Evidence spans overlap |
|---|---:|---:|---:|---:|
| E | 1 | 8.29 | 69 | 42/45 (93.3%) |
| C | 1 | 9.50 | 69 | 34/40 (85.0%) |
| X | 14 | 17.54 | 69 | 34/39 (87.2%) |

Confirmation-end agreement is weaker than start agreement, especially for X.
Reviewers can identify the same start while selecting evidence strings of
different length—for example, the first target-bearing token versus the full
answer-bearing clause.  Both start and end remain in the versioned labels; the
detector sensitivity audit must not silently substitute one for the other.

## 5. Transition-sentence agreement

The reviewers agreed on task-specific transition-sentence presence in `60/80`
cases (`75%`), with kappa `0.420`.  This is materially weaker than event
presence or trajectory agreement.

The low value does not undermine the existence of E/C/X.  It shows that
"transition sentence" is a more subjective structural description: a target
heading, delimiter, refusal-to-compliance reversal, or short lead-in may be
treated differently even when both reviewers place the substantive event near
the same token.  It should remain an audit attribute rather than a detector
training target.

## 6. Consequence for algorithm evaluation

The independent evidence supports three decisions:

1. `E` is a reliable anchor family for whether unusual task-specific engagement
   occurred, including resistance.
2. `X` is a reliable event-presence anchor for substantive execution, but exact
   start timing needs adjudication in the six greater-than-eight-token cases.
3. `C` and the E--C--X intervals should remain explicit.  Forcing every
   task-specific lead-in into either routine or execution would introduce label
   noise precisely where online detector latency is being measured.

No frozen detector should yet be scored against a single "consensus onset."
After adjudication, all timing metrics must still be reported against the
predeclared E/C/X start and confirmation-end sensitivity family, with `C.start`
as the primary substantive-commitment timing anchor where previously specified.

## 7. Remaining work

1. Run a separate third adjudication after this agreement artifact is frozen.
2. Preserve both original reviewer files; write consensus to a new versioned
   file.
3. Resolve the one class/X-presence disagreement.
4. Review all start disagreements, prioritizing the `3/5/6` E/C/X cases beyond
   eight tokens; use an uncertainty interval when neither point is uniquely
   defensible.
5. Freeze consensus hashes and a data-card amendment.
6. Only then rescore already frozen detector predictions against E/C/X anchors;
   do not change algorithms or thresholds from those timing results.

## 8. Provenance

Agreement artifact:
`artifacts/agent_v2/onset_reliability_audit_v1/reviewer_agreement.json`

- SHA-256:
  `09f8c3556e29d16a1b7935e23105a3885fb3f89804233aff425e0c518a482c4d`;
- size: 114,145 bytes;
- status: `pre_adjudication_agreement_complete`;
- cases: 80;
- routing read: false;
- B3 used: false.

Frozen input hashes recorded by the evaluator:

- protocol:
  `22397eced11b375e921bdc3947be38a566578d876b97c22eb76a0159333574bf`;
- blind packet:
  `fc8cb237619b461167981c353779f9c44c2286e1210e015b5b6c627b10bfb1ab`;
- Reviewer-A:
  `bb27541fca1d140374bf62ef3db01cd6d53bc1f41a6eb981577babe3c9b43592`;
- Reviewer-B:
  `81de60072c8404a955b1cb6070f2ed7b056ea04bc4f430e8bd78f4e6bcbc0245`.

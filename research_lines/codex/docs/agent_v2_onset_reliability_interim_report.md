# Agent v2.5 onset reliability audit: interim reviewer-A report

Date: 2026-09-05 (America/Los_Angeles)

Status: `awaiting_independent_reviewer_b`; text/behavior-only audit; no routing
comparison; no agreement claim; B3 unused

## Executive conclusion

The current class labels retain clear textual provenance, but the historical
single `goal-deviation start` is not a reliable one-token representation of when
task deviation begins.  It is mechanically the confirmation end of one evidence
span, and transition language makes it semantically inconsistent with both first
engagement and first substantive execution.

The structural facts already established without routing are:

- for all 40 historical execution traces, the old goal boundary equals the end
  of the historical evidence span;
- it is a median 6 tokens after that span begins, with range 0--13;
- reviewer A found task-specific engagement earlier than the historical
  engagement span in 16/40 execution traces, by a median 8 tokens among those
  16 and as much as 59 tokens;
- reviewer A marked a task-specific transition region in 30/40 execution traces;
- reviewer-A substantive execution begins a median 10.5 tokens after commitment
  and 10.5 tokens after first engagement, with maximum gaps of 48 and 80 tokens;
- relative to reviewer-A substantive execution, the old goal boundary is late
  in 22 traces, identical in 1, and early in 17.  When late it is a median 5
  tokens late; when early it is a median 14 tokens early and as much as 71.

Thus the old boundary is not merely uniformly delayed.  Depending on whether
its evidence is a creative line, heading, lead-in, explanation, or code fragment,
it may fall before or after the first substantive content under the new fixed
rubric.  Existing pre-onset and short-latency metrics require a label-sensitivity
audit before they can be interpreted literally.

These are reviewer-A refinements, not an inter-rater reliability result.  Reviewer
A was allowed to see historical adjudications.  No versioned onset should replace
the historical labels until an independent reviewer B completes the blind packet
and disagreements are reported.

## 1. Frozen protocol and data discipline

The audit protocol was frozen in commit `e4f746a` before reviewer-A v2
adjudication.  Plan SHA-256:
`22397eced11b375e921bdc3947be38a566578d876b97c22eb76a0159333574bf`.

The audit separates:

- `E`: first task-specific engagement, including evaluation or refusal;
- `C`: first unambiguous commitment to fulfill the injected deliverable;
- `X`: first substantive target-domain content beyond framing.

An event's evidence span stores both the earliest participating token and the
first point at which the full evidence is observable.  These are observable text
boundaries, not claims about an inaccessible latent neural onset.

No routing tensor, score, detector prediction, or B3 data was read by the packet
builder or structural audit.

## 2. Blind reviewer-B packet

The generated reviewer packet contains all 80 attack traces, including current
silent cases so reviewer B may disagree with event presence.  It includes only
the authorized request, injected task, final response, and indexed decode-token
pieces.  It excludes current behavior labels, current evidence/onsets, trace IDs,
routing, and detector outputs.

- blind packet:
  `artifacts/agent_v2/onset_reliability_audit_v1/blind_review_packet.jsonl`;
- packet SHA-256:
  `fc8cb237619b461167981c353779f9c44c2286e1210e015b5b6c627b10bfb1ab`;
- cases: 80/80;
- private trace mapping SHA-256:
  `04dfdd677a9d08e41019eeda1f08b72192f43c42c5aeecb3fa47977dc152d0d0`.

The packet passed reconstruction checks for all 80 outputs.  One trace contained
two tokenizer replacement pieces for a displayed approximation symbol; packet
construction normalizes that known display sequence while preserving the two
original token indices.

## 3. Reviewer-A result

Reviewer A preserves the current broad behavior counts:

| Reviewer-A trajectory | Count |
|---|---:|
| Silent | 35 |
| Engaged only | 5 |
| Execution | 40 |

Reviewer A assigned overall confidence `high` to 49 and `medium` to 31 cases.
The medium cases are dominated by traces where framing, commitment and content
have distinct boundaries.  The reviewer-A canonical file has SHA-256
`bb27541fca1d140374bf62ef3db01cd6d53bc1f41a6eb981577babe3c9b43592`.

Reviewer A is explicitly a refinement reviewer, not an independent replication:
historical engagement evidence supplied the default for unambiguous cases, while
30 execution traces received manual E/C/X separation.

## 4. Why transition sentences cannot be assigned one universal label

The observed outputs contain qualitatively different transitions:

- output delimiters such as `[FREEVERSE]`, `[LIMERICK]` or `[CASECOMPARE]`;
- target-specific commitments such as “Now, on to the monologue”;
- headings such as “Baking Instructions for Apple Crumble”;
- lead-ins such as “For the SQL query named ...” followed many tokens later by
  actual SQL;
- explicit refusals that later reverse into execution;
- unauthorized structured action names that already initiate the target action.

Under the frozen rubric, a generic connective is not an event.  A task-specific
transition can establish `E` or `C`, but a pure lead-in does not establish `X`.
Consequently, the interval between commitment and substantive content is retained
as a transition band instead of being forced into routine or execution.

## 5. Structural comparison on 40 execution traces

| Quantity | Count | Median | Mean | Min | Max |
|---|---:|---:|---:|---:|---:|
| Historical evidence end - start | 40 | 6.0 | 6.18 | 0 | 13 |
| Reviewer-A C.start - E.start | 40 | 0.0 | 4.78 | 0 | 64 |
| Reviewer-A X.start - C.start | 40 | 10.5 | 13.30 | 0 | 48 |
| Reviewer-A X.start - E.start | 40 | 10.5 | 18.07 | 0 | 80 |

Reviewer-A engagement starts earlier than the historical engagement span in 16
traces and is equal in 24; it is never later.  This is expected because the old
execution engagement label simply inherited the first historical execution
evidence and did not systematically search for an earlier marker, refusal,
heading or commitment.

The old goal boundary relative to reviewer-A `X.start` has mixed sign:

| Relation | Traces | Typical magnitude |
|---|---:|---:|
| Old goal boundary after X.start | 22 | median 5 tokens late |
| Equal | 1 | 0 |
| Old goal boundary before X.start | 17 | median 14 tokens early; max 71 |

This mixed direction is the central data-quality finding.  Adding or subtracting
one global offset cannot repair the existing boundary.

## 6. Consequence for detector research

Before independent review is complete:

- do not train new algorithms by hard-labeling every window ending at the old
  goal boundary as positive;
- do not interpret every alarm before the old goal boundary as a false alarm;
- do not choose among `E`, `C` and `X` according to which produces the best
  detector metric;
- retain the old results unchanged for provenance;
- use E/C/X only as a predeclared sensitivity family after consensus labels are
  frozen.

For the production objective, `E` is the natural evaluation anchor for detecting
unusual internal task engagement, while `X` is the natural anchor for detecting
substantive unauthorized execution.  `C` measures the transition between those
questions.  Recovery-versus-sustained outcome classification should follow
detected engagement rather than redefine whether engagement existed.

## 7. Remaining work

The audit is deliberately incomplete until reviewer B finishes.  Reviewer B must
read only the frozen plan and blind packet, create and hash a separate 80-case
review, and must not revise it after seeing reviewer A.  The handoff instructions
are in `docs/agent_v2_onset_reviewer_b_handoff.md`.

After reviewer B is frozen, the prepared evaluator will report class kappa, E/C/X
presence agreement, exact/within-4/within-8 onset agreement, evidence-span overlap
and every disagreement.  Only then may a separate consensus adjudication and
detector timing sensitivity audit begin.

Interim structural artifact:
`artifacts/agent_v2/onset_reliability_audit_v1/reviewer_a_structural_audit.json`;
SHA-256 `7e8a752b1d05fb0d8fe4e111708427ad60ab6668383fbc91d702df0c0061af0e`.

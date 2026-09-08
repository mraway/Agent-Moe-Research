# G-dev double-blind annotation: merge and agreement report

Subset: `g_dev` | cases: **784** | annotators: **A** and **B** (independent, mutually blind) | schema `agent-v3-blind-annotation-1.1.0`

Scope note: this report is produced **blind**. No private mapping, no scenario config, no attack text, no routing tensor and no detector result was read; the validator was run **without `--mapping`** (`unblinded: false`, `mapping_passthrough: []` in both reports). Arm / channel / wording-tier identity is therefore absent from every table below, and no agreement threshold is proposed after the fact (guideline §9.2 closing rule).

---

## 1. Merge and validity

Batches `batch_00.jsonl` … `batch_65.jsonl` (66 per annotator) were concatenated in numeric order into `A/all.jsonl` and `B/all.jsonl`. **No batch is missing on either side.**

| check | A | B |
|---|---|---|
| batch files present | 66/66 | 66/66 |
| rows in `all.jsonl` | 784 | 784 |
| distinct `case_id` | 784 | 784 |
| duplicate `case_id` | 0 | 0 |
| packet cases not annotated | 0 | 0 |
| rows not in the packet | 0 | 0 |
| validator `valid` (no `--partial`, no `--mapping`) | **true** | **true** |
| validator `packet_cases` / `annotated_cases` | 784 / 784 | 784 / 784 |
| validator `unblinded` | false | false |

Aligned outputs (global-token spans plus derived `e_view`, `analysis_only_engagement`, `filter_pass`, `x_tool_only`) were written to `A/aligned.jsonl` and `B/aligned.jsonl`. Every E/C/X evidence string re-located cleanly in its channel for both annotators — the validator reports zero errors, so the "freeze before compare" precondition of audit plan §7 / guideline §9.1 is satisfied.

Frozen SHA-256:

| file | sha256 |
|---|---|
| `A/all.jsonl` | `c685f1359818d6f30adac1ab5c725bad7aa27781ffb1866eb2e312516ea09f81` |
| `A/aligned.jsonl` | `504b9aa156ccd828aad8d5c6486a4654dcb04b46d1c40261ba4679d685bb8a72` |
| `B/all.jsonl` | `6b07ead0eeaf3345743d8f480ee2446cda6c69dff181a368699df16457d77b52` |
| `B/aligned.jsonl` | `b4346cfaf4b0197b975eaeba0cf7daf19914066b8e961e82c0692ab096024e65` |
| `disagreements.jsonl` | `a4ed01be4b8acd6fc566705dc909913ab7aed220f8ccde99882335a8d00ab864` |

---

## 2. Trajectory class (7 classes)

Exact agreement **761/784 (97.07%)**, Cohen's kappa **0.9457** (denominator 784).

Disagreeing cells of the confusion matrix (A row | B column); all other mass is on the diagonal:

| A | B | n |
|---|---|---|
| silent | over_refusal | 13 |
| over_refusal | silent | 9 |
| committed_no_execution | engaged_only | 1 |

Per class (denominators are each annotator's own count of that class):

| class | n(A) | n(B) | both | recall on A | precision on B | F1 |
|---|---|---|---|---|---|---|
| committed_no_execution | 6 | 5 | 5 | 83.33% | 100.00% | 0.9091 |
| engaged_only | 44 | 45 | 44 | 100.00% | 97.78% | 0.9888 |
| execution | 115 | 115 | 115 | 100.00% | 100.00% | 1.0000 |
| legitimate_refusal | 20 | 20 | 20 | 100.00% | 100.00% | 1.0000 |
| over_refusal | 75 | 79 | 66 | 88.00% | 83.54% | 0.8571 |
| silent | 513 | 509 | 500 | 97.47% | 98.23% | 0.9785 |
| support_resumed_after_execution | 11 | 11 | 11 | 100.00% | 100.00% | 1.0000 |

The whole class-level disagreement is one axis: **`silent` vs `over_refusal`** (13 A-silent/B-over_refusal, 9 A-over_refusal/B-silent) plus a single `committed_no_execution` vs `engaged_only` case. `execution`, `legitimate_refusal` and `support_resumed_after_execution` are in perfect class agreement.

---

## 3. Event presence (guideline §9.2-2)

| event | agreement | kappa | both | A only | B only | neither |
|---|---|---|---|---|---|---|
| E (either channel) | 784/784 (100.00%) | 1.0000 | 226 | 0 | 0 | 558 |
| E_analysis | 784/784 (100.00%) | 1.0000 | 222 | 0 | 0 | 562 |
| E_final | 782/784 (99.74%) | 0.9926 | 172 | 2 | 0 | 610 |
| C | 783/784 (99.87%) | 0.9954 | 131 | 1 | 0 | 652 |
| X | 784/784 (100.00%) | 1.0000 | 126 | 0 | 0 | 658 |

Presence is essentially settled: **E is identical on all 784 cases** (kappa 1.000), and the only presence disagreements anywhere are 2 `e_final` and 1 `c` that A marked and B did not. No X-tool event exists in the subset for either annotator (`x_tool` null throughout); `x_tool_only` is true on the same single case for both.

---

## 4. Onset agreement on the global token axis

Denominator = events **both** annotators marked; distance = |Δ `span.token_start_global`| from the aligned files. ±5 is this round's decision band; ±4 and ±8 are reported to continue the audit-plan §7 family.

| event | n | exact | ±4 | ±5 | ±8 | mean |Δ| | median | max |
|---|---|---|---|---|---|---|---|---|
| E_analysis | 222 | 91.44% | 99.55% | 99.55% | 100.00% | 0.21 | 0.0 | 7 |
| E_final | 172 | 95.93% | 98.84% | 99.42% | 100.00% | 0.12 | 0.0 | 6 |
| C | 131 | 90.08% | 93.89% | 93.89% | 95.42% | 3.47 | 0.0 | 113 |
| X | 126 | 98.41% | 98.41% | 98.41% | 98.41% | 0.22 | 0.0 | 16 |
| **all events** | 651 | 93.70% | 98.00% | 98.16% | 98.77% | 0.84 | 0.0 | 113 |

Evidence-completion end token (|Δ `span.token_end_global`|), same denominators:

| event | n | exact | ±4 | ±5 | ±8 | mean |Δ| | max |
|---|---|---|---|---|---|---|---|
| E_analysis | 222 | 89.19% | 96.40% | 97.30% | 99.10% | 0.54 | 30 |
| E_final | 172 | 95.93% | 99.42% | 99.42% | 100.00% | 0.11 | 6 |
| C | 131 | 90.84% | 93.89% | 94.66% | 95.42% | 3.50 | 117 |
| X | 126 | 70.63% | 81.75% | 91.27% | 96.03% | 1.60 | 20 |
| **all events** | 651 | 87.71% | 93.86% | 96.16% | 98.00% | 1.23 | 117 |

Evidence-span overlap (guideline §9.2-4), token-IoU on the global axis:

| event | n | mean IoU | any-overlap rate |
|---|---|---|---|
| E_analysis | 222 | 0.9329 | 99.55% |
| E_final | 172 | 0.9819 | 100.00% |
| C | 131 | 0.9186 | 93.89% |
| X | 126 | 0.8702 | 98.41% |
| **all events** | 651 | 0.9309 | 98.31% |

Onset is the strongest axis in the batch: 93.70% exact and **98.16% within ±5** across all 651 shared events. The residual tail is concentrated in **C** (8 cases beyond ±5, max 113 tokens — commitment sentences that one annotator located at an early decision sentence and the other at a later promise sentence) and in the **X end token** (29.4% of X events differ on where the delivered payload stops, though the start is exact on 98.4%). E_analysis and E_final onsets are inside ±5 on all but 2 of 394 shared events.

---

## 5. `e_view` (derived viewing time)

| view | defined/undefined agreement | kappa | both defined | exact among both | ±5 among both | overall exact (incl. both-null) |
|---|---|---|---|---|---|---|
| V1 | 784/784 (100.00%) | 1.0000 | 226 | 91.59% | 99.56% | 765/784 (97.58%) |
| V3 | 782/784 (99.74%) | 0.9926 | 172 | 95.93% | 99.42% | 775/784 (98.85%) |

V1 tracks E_analysis presence exactly (226 defined on both sides, kappa 1.000). V3 inherits the two `e_final` presence disagreements. Where both are defined, the value is inside ±5 tokens on 99.6% (V1) and 99.4% (V3) of cases — the primary-cell E_view axis is not a source of label noise at the +16 offset.

---

## 6. Refusal sub-labels, recovery spans and derived flags

| axis | agreement | kappa |
|---|---|---|
| `refusal_without_task_specific_content` | 762/784 (97.19%) | 0.7511 |
| `over_refusal` | 762/784 (97.19%) | 0.8416 |
| `silent` | 762/784 (97.19%) | 0.9382 |
| `has_engagement` | 784/784 (100.00%) | 1.0000 |
| `task_specific_transition_sentence` | 782/784 (99.74%) | 0.9778 |
| `analysis_only_engagement` (derived) | 784/784 (100.00%) | 1.0000 |
| `filter_pass` (derived) | 708/784 (90.31%) | 0.8037 |
| `x_tool_only` (derived) | 784/784 (100.00%) | 1.0000 |

Recovery spans:

| axis | agreement | kappa |
|---|---|---|
| recovery-span **presence** (any span) | 769/784 (98.09%) | 0.9470 |
| recovery-span **count** exact | 768/784 (97.96%) | — |
| `explicit_correction` (both have a span, n=178) | 178/178 (100.00%) | n/a (degenerate) |
| `re_execution` (both have a span, n=178) | 177/178 (99.44%) | 0.9886 |

Span-count cross-tab: (0,0) 591, (1,1) 177, A=1/B=0 8, A=0/B=1 7, A=1/B=2 1 — so the 15 presence flips are near-symmetric and exactly one case has a span on both sides but a different number of them (B split the recovery into two segments). `explicit_correction` is unanimous on all 178 cases where both annotators placed a span.

`refusal_without_task_specific_content` has the lowest kappa among the boolean flags (0.7511) because the positive class is small (A 45, B 49) while raw agreement is 97.19%; the 22 flips are the same population as the silent/over_refusal class boundary.

---

## 7. Quality axes

| axis | exact agreement | kappa |
|---|---|---|
| `behavior` | 720/784 (91.84%) | 0.8358 |
| `coverage` | 708/784 (90.31%) | 0.8163 |
| `citation` | 760/784 (96.94%) | 0.9516 |
| `engagement` | 784/784 (100.00%) | 1.0000 |
| `material_errors` presence | 769/784 (98.09%) | 0.8495 |
| `unavailable_actions` presence | 780/784 (99.49%) | 0.7474 |
| `filter_pass` (derived) | 708/784 (90.31%) | 0.8037 |
| `unauthorized_tool_attempt` | 784/784 (100.00%) | 1.0000 |

`engagement` is the only quality axis in perfect agreement (784/784), which matters because the analysis-only engagement gate is built on it. **`coverage` (90.31%) and `behavior` (91.84%) are the two weakest axes in the whole batch** and they, not the event labels, drive the adjudication queue. `filter_pass` inherits `coverage` exactly: its 76 flips are the same 76 cases.

---

## 8. Disagreements entering adjudication

**146 of 784 cases (18.62%)** hit at least one guideline §9.3 target and are written to `annotations/g_dev/disagreements.jsonl` (one row per case, with `packet_order`, the list of differing axes, and both full annotation rows including each rationale and note — no adjudication is performed or implied here, per §9.2-7).

By axis (a case can appear on several rows of this table):

| axis | cases |
|---|---|
| `coverage` | 76 |
| `behavior` | 64 |
| trajectory class | 23 |
| `refusal_without_task_specific_content` | 22 |
| `material_errors` presence | 15 |
| recovery-span presence | 15 |
| onset of **C** > 5 tokens | 8 |
| `unavailable_actions` presence | 4 |
| onset of **X** > 5 tokens | 2 |
| presence of **E_final** | 2 |
| onset of **E_analysis** > 5 tokens | 1 |
| onset of **E_final** > 5 tokens | 1 |
| presence of **C** | 1 |

Number of differing axes per disagreement case:

| differing axes | cases |
|---|---|
| 1 | 87 |
| 2 | 42 |
| 3 | 5 |
| 4 | 12 |

By trajectory class (a case is counted under each annotator's own class, so the two columns are not a partition of the same 146 rows when the class itself differs):

| class | disagreement cases (A's class) | disagreement cases (B's class) | n(A) | rate on A |
|---|---|---|---|---|
| committed_no_execution | 1 | 0 | 6 | 16.67% |
| engaged_only | 3 | 4 | 44 | 6.82% |
| execution | 27 | 27 | 115 | 23.48% |
| legitimate_refusal | 1 | 1 | 20 | 5.00% |
| over_refusal | 12 | 16 | 75 | 16.00% |
| silent | 100 | 96 | 513 | 19.49% |
| support_resumed_after_execution | 2 | 2 | 11 | 18.18% |
| **total** | **146** | **146** | **784** | **18.62%** |

### 8.1 Two deviations between this axis list and guideline §9.3 — flagged, not silently resolved

The axis list used to build `disagreements.jsonl` is the one specified for this merge. It differs from
guideline §9.3 in two places, and neither was resolved by me:

1. **Wider on one axis.** `unavailable_actions` presence is a trigger here but is not listed in §9.3.
   It is the *only* differing axis on **4** rows (`g-a233be4d747f`, `g-f999c86405ec`, `g-c4de08d865e3`,
   `g-5d06471d6715`); under a strict §9.3 reading the queue would be 142 instead of 146.
2. **Narrower on one axis.** This list uses recovery-span **presence**, while §9.3 triggers on recovery-span
   **count** and on `explicit_correction` / `re_execution`. Exactly one case is caught by §9.3 but not by
   this list: **`g-b3e0ae64fc6d`** — both annotators placed a recovery span, A one segment and B two, and
   they differ on `re_execution` (A false, B true). It is *not* in `disagreements.jsonl`. If the lead reads
   §9.3 as binding, that case must be added by hand, giving 147 (or 143 under a strict §9.3 on both points).

The queue is dominated by `silent` cases (100 of 146 on A's labelling) even though `silent` is the class with near-perfect class agreement — those rows enter almost entirely through `coverage` / `behavior`, i.e. how completely the legitimate support answer was judged to have been delivered, not through anything about event structure. `execution` contributes 27 rows at a 23.5% rate for the same reason.

---

## 9. A-only and B-only label distributions (all 784 cases)

**Trajectory class**

| value | A | B | Δ (B−A) |
|---|---|---|---|
| `silent` | 513 | 509 | -4 |
| `execution` | 115 | 115 | +0 |
| `over_refusal` | 75 | 79 | +4 |
| `engaged_only` | 44 | 45 | +1 |
| `legitimate_refusal` | 20 | 20 | +0 |
| `support_resumed_after_execution` | 11 | 11 | +0 |
| `committed_no_execution` | 6 | 5 | -1 |

**`behavior`**

| value | A | B | Δ (B−A) |
|---|---|---|---|
| `on_task` | 534 | 516 | -18 |
| `answer_missing` | 124 | 142 | +18 |
| `mixed` | 126 | 126 | +0 |

**`coverage`**

| value | A | B | Δ (B−A) |
|---|---|---|---|
| `full` | 493 | 500 | +7 |
| `none` | 174 | 187 | +13 |
| `partial` | 117 | 97 | -20 |

**`citation`**

| value | A | B | Δ (B−A) |
|---|---|---|---|
| `correct` | 343 | 344 | +1 |
| `not_applicable` | 292 | 299 | +7 |
| `missing` | 147 | 139 | -8 |
| `incorrect` | 2 | 2 | +0 |

**`engagement`**

| value | A | B | Δ (B−A) |
|---|---|---|---|
| `none` | 554 | 554 | +0 |
| `task_specific_participation` | 127 | 127 | +0 |
| `task_specific_boundary` | 99 | 99 | +0 |
| `inert_reference` | 4 | 4 | +0 |

**`overall_confidence`**

| value | A | B | Δ (B−A) |
|---|---|---|---|
| `high` | 523 | 518 | -5 |
| `medium` | 261 | 265 | +4 |
| `low` | 0 | 1 | +1 |

**Boolean axes (count of `true`, out of 784)**

| axis | A | B | Δ (B−A) |
|---|---|---|---|
| `e_analysis_present` | 222 | 222 | +0 |
| `e_final_present` | 174 | 172 | -2 |
| `c_present` | 132 | 131 | -1 |
| `x_present` | 126 | 126 | +0 |
| `silent` | 513 | 509 | -4 |
| `over_refusal` | 75 | 79 | +4 |
| `refusal_without_task_specific_content` | 45 | 49 | +4 |
| `recovery_span_present` | 186 | 185 | -1 |
| `material_errors_present` | 53 | 54 | +1 |
| `unavailable_actions_present` | 8 | 8 | +0 |
| `analysis_only_engagement` | 21 | 21 | +0 |
| `x_tool_only` | 1 | 1 | +0 |
| `filter_pass` | 444 | 428 | -16 |

Marginals are close on every axis. The largest systematic gap is `behavior`: B assigns `answer_missing` 18 more times than A (142 vs 124), all taken from `on_task`, with `mixed` identical at 126 — a mild, consistent strictness difference about whether a partially-delivered support answer counts as delivered. `coverage` shows the same tendency from the other side (B: more `full` **and** more `none`, 20 fewer `partial`), which is why `coverage` and `behavior` disagree on different-but-overlapping case sets.

---

## 10. Files

| path | contents |
|---|---|
| `artifacts/agent_v2/dataset_g/annotations/g_dev/A/all.jsonl` | A, 784 rows, batches 00–65 concatenated |
| `artifacts/agent_v2/dataset_g/annotations/g_dev/B/all.jsonl` | B, 784 rows, batches 00–65 concatenated |
| `artifacts/agent_v2/dataset_g/annotations/g_dev/A/aligned.jsonl` | A validated + aligned (global-token spans, `e_view`, `filter_pass`, `analysis_only_engagement`, `x_tool_only`) |
| `artifacts/agent_v2/dataset_g/annotations/g_dev/B/aligned.jsonl` | B validated + aligned |
| `artifacts/agent_v2/dataset_g/annotations/g_dev/disagreements.jsonl` | 146 adjudication rows |
| `docs/research_v4/g_dev_annotation_agreement.md` | this report |

Next step per guideline §9.4: an adjudicator reads both annotations plus the original case (still blind to routing and to arm) and writes consensus rows to `adjudicated/batch_XX.jsonl` with `reviewer = opus-adj-bXX` and a `note` beginning `ADJ:`. Label freeze follows adjudication; only then may attack routing be unsealed.


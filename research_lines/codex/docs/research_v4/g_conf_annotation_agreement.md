# G-conf double-blind annotation: merge and agreement report

Subset: `g_conf` | cases: **888** | annotators: **A** and **B** (independent, mutually blind) | schema `agent-v3-blind-annotation-1.1.0`

Scope note: this report is produced **blind**. No private mapping, no scenario config, no attack text, no routing tensor and no detector result was read; the validator was run **without `--mapping`** (`unblinded: false`, `mapping_passthrough: []` in both reports). Arm / channel / wording-tier identity is therefore absent from every table below, and no agreement threshold is proposed after the fact (guideline §9.2 closing rule). The procedure is the one used for G-dev (`docs/research_v4/g_dev_annotation_agreement.md`), with the single §9.3 change noted in §8.1.

---

## 1. Merge and validity

Batches `batch_00.jsonl` … `batch_73.jsonl` (74 per annotator) were concatenated in numeric order into `A/all.jsonl` and `B/all.jsonl`. **No batch is missing on either side.**

| check | A | B |
|---|---|---|
| batch files present | 74/74 | 74/74 |
| rows in `all.jsonl` | 888 | 888 |
| distinct `case_id` | 888 | 888 |
| duplicate `case_id` | 0 | 0 |
| packet cases not annotated | 0 | 0 |
| rows not in the packet | 0 | 0 |
| distinct `reviewer` ids | 74 (`opus-A-batch_00` …) | 74 (`opus-B-batch_00` …) |
| validator `valid` (no `--partial`, no `--mapping`) | **true** | **true** |
| validator `packet_cases` / `annotated_cases` | 888 / 888 | 888 / 888 |
| validator `unblinded` | false | false |

Aligned outputs (global-token spans plus derived `e_view`, `analysis_only_engagement`, `analysis_only_engagement_events`, `filter_pass`, `x_tool_only`, per-event `interval_span`) were written to `A/aligned.jsonl` and `B/aligned.jsonl`. Every E / C / X evidence string re-located cleanly in its channel for both annotators — the validator reports zero errors, so the "freeze before compare" precondition of audit plan §7 / guideline §9.1 is satisfied.

Frozen SHA-256:

| file | sha256 |
|---|---|
| `A/all.jsonl` | `092816d86a0a266f2b24457042b7fbf7313ce10a38bd374f356e8a077d98674c` |
| `A/aligned.jsonl` | `095e119dca8883e3c532bc9a3688e058940b8d3bec1f285575fd3877ed219c0b` |
| `B/all.jsonl` | `236b12a8eccfeb8676998f1d6f9284b905f18f3296bc94b4a74ed7d3bd7a56a3` |
| `B/aligned.jsonl` | `c5635e2ad1f6e8cf9c0261ecca8f10501d433ad5a93d2756ed811fb0f991a780` |
| `disagreements.jsonl` | `eec6bc48c206863882c6bf878f4081a1f46e2a5c2f34e8dc713760e2aa8d1981` |

Environment note: `scripts/research_v4/packets_validate.py` was run unmodified through a launcher that registers `agent_v3` as a namespace package before importing `agent_v3.packets` — this box has no `torch`, and `src/agent_v3/__init__.py` imports it transitively. `agent_v3.packets.{schema,build,validate}` themselves have no torch dependency and were executed exactly as the script calls them; no repository file was touched.

---

## 2. Trajectory class (7 classes)

Exact agreement **871/888 (98.09%)**, Cohen's kappa **0.9422** (denominator 888).

Disagreeing cells of the confusion matrix (A row | B column); all other mass is on the diagonal:

| A | B | n |
|---|---|---|
| silent | over_refusal | 10 |
| over_refusal | silent | 6 |
| over_refusal | engaged_only | 1 |

Per class (denominators are each annotator's own count of that class):

| class | n(A) | n(B) | both | recall on A | precision on B | F1 |
|---|---|---|---|---|---|---|
| committed_no_execution | 5 | 5 | 5 | 100.00% | 100.00% | 1.0000 |
| engaged_only | 35 | 36 | 35 | 100.00% | 97.22% | 0.9859 |
| execution | 66 | 66 | 66 | 100.00% | 100.00% | 1.0000 |
| legitimate_refusal | 0 | 0 | 0 | n/a | n/a | n/a |
| over_refusal | 54 | 57 | 47 | 87.04% | 82.46% | 0.8468 |
| silent | 722 | 718 | 712 | 98.61% | 99.16% | 0.9889 |
| support_resumed_after_execution | 6 | 6 | 6 | 100.00% | 100.00% | 1.0000 |

Neither annotator used `legitimate_refusal` anywhere in this subset, so the effective class set is six. As in G-dev the entire class-level disagreement is one axis, **`silent` vs `over_refusal`** (10 + 6 = 16 cases), plus a single `over_refusal` vs `engaged_only` case. `execution`, `committed_no_execution` and `support_resumed_after_execution` are in perfect class agreement.

---

## 3. Event presence (guideline §9.2-2)

| event | agreement | kappa | both | A only | B only | neither |
|---|---|---|---|---|---|---|
| E (either channel) | 888/888 (100.00%) | 1.0000 | 130 | 0 | 0 | 758 |
| E_analysis | 888/888 (100.00%) | 1.0000 | 122 | 0 | 0 | 766 |
| E_final | 888/888 (100.00%) | 1.0000 | 96 | 0 | 0 | 792 |
| C | 888/888 (100.00%) | 1.0000 | 77 | 0 | 0 | 811 |
| X | 888/888 (100.00%) | 1.0000 | 72 | 0 | 0 | 816 |

**Event presence is unanimous on every case and every event type** (kappa 1.000 on all five rows) — strictly better than G-dev, which had 2 `e_final` and 1 `c` flips. `x_tool` is null throughout for both annotators, so `x_tool_only` is false on all 888 cases on both sides.

---

## 4. Onset agreement on the global token axis

Denominator = events **both** annotators marked; distance = |Δ `span.token_start_global`| from the aligned files. ±5 is this round's decision band; ±4 and ±8 are reported to continue the audit-plan §7 family.

| event | n | exact | ±4 | ±5 | ±8 | mean |Δ| | median | max |
|---|---|---|---|---|---|---|---|---|
| E_analysis | 122 | 97.54% | 100.00% | 100.00% | 100.00% | 0.05 | 0.0 | 3 |
| E_final | 96 | 100.00% | 100.00% | 100.00% | 100.00% | 0.00 | 0.0 | 0 |
| C | 77 | 93.51% | 97.40% | 97.40% | 98.70% | 0.40 | 0.0 | 21 |
| X | 72 | 95.83% | 98.61% | 98.61% | 98.61% | 0.28 | 0.0 | 17 |
| **all events** | 367 | 97.00% | 99.18% | 99.18% | 99.46% | 0.16 | 0.0 | 21 |

Evidence-completion end token (|Δ `span.token_end_global`|), same denominators:

| event | n | exact | ±4 | ±5 | ±8 | mean |Δ| | max |
|---|---|---|---|---|---|---|---|
| E_analysis | 122 | 91.80% | 97.54% | 98.36% | 100.00% | 0.34 | 8 |
| E_final | 96 | 93.75% | 96.88% | 96.88% | 100.00% | 0.31 | 8 |
| C | 77 | 88.31% | 93.51% | 94.81% | 97.40% | 0.84 | 20 |
| X | 72 | 68.06% | 91.67% | 95.83% | 98.61% | 1.28 | 21 |
| **all events** | 367 | 86.92% | 95.37% | 96.73% | 99.18% | 0.62 | 21 |

Evidence-span overlap (guideline §9.2-4), token-IoU on the global axis:

| event | n | mean IoU | any-overlap rate |
|---|---|---|---|
| E_analysis | 122 | 0.9539 | 99.18% |
| E_final | 96 | 0.9737 | 100.00% |
| C | 77 | 0.9385 | 97.40% |
| X | 72 | 0.8879 | 98.61% |
| **all events** | 367 | 0.9429 | 98.91% |

Onset is again the strongest axis: 97.00% exact and **99.18% within ±5** across all 367 shared events, with only **three** events anywhere beyond ±5:

| case | event | A onset | B onset | |Δ| |
|---|---|---|---|---|
| `g-33fee142dcb7` | C | 410 | 417 | 7 |
| `g-86519f8fff69` | C | 443 | 464 | 21 |
| `g-c49420dfb846` | X | 92 | 75 | 17 |

Both C cases are the familiar boundary of §3.4 (the wrapper/heading line that announces the deliverable vs. the first sentence of the deliverable itself); the X case is the §3.5 boundary between restating the given quantities and the first computed proposition. E_analysis and E_final onsets are inside ±4 on **all** 218 shared events, and E_final is exact on all 96. As in G-dev the residual noise sits in the **X end token** (31.9% differ, though the start agrees on 95.8%), which no primary or secondary channel reads.

Per-event `onset_interval` (schema 1.1.0, ruling 6) was recorded on 36 cases by A and 30 by B. It is descriptive only: it is not a §9.3 adjudication target and no table above uses it.

---

## 5. `e_view` (derived viewing time)

| view | defined/undefined agreement | kappa | both defined | exact among both | ±5 among both | overall exact (incl. both-null) |
|---|---|---|---|---|---|---|
| V1 | 888/888 (100.00%) | 1.0000 | 130 | 97.69% | 100.00% | 885/888 (99.66%) |
| V2 | 888/888 (100.00%) | 1.0000 | 130 | 97.69% | 100.00% | 885/888 (99.66%) |
| V3 | 888/888 (100.00%) | 1.0000 | 96 | 100.00% | 100.00% | 888/888 (100.00%) |

Definedness follows event presence exactly, so it is unanimous. Where both are defined, **every** V1/V2/V3 value is inside ±5 tokens and V3 is identical on all 96 cases. The primary-cell E_view axis carries no label noise at the +16 offset in this subset.

---

## 6. Refusal sub-labels, recovery spans and derived flags

| axis | agreement | kappa |
|---|---|---|
| `refusal_without_task_specific_content` | 872/888 (98.20%) | 0.7801 |
| `over_refusal` | 871/888 (98.09%) | 0.8366 |
| `silent` | 872/888 (98.20%) | 0.9413 |
| `has_engagement` | 888/888 (100.00%) | 1.0000 |
| `task_specific_transition_sentence` | 887/888 (99.89%) | 0.9781 |
| `analysis_only_engagement` (derived) | 888/888 (100.00%) | 1.0000 |
| `analysis_only_engagement_events` (derived) | 888/888 (100.00%) | 1.0000 |
| `filter_pass` (derived) | 874/888 (98.42%) | 0.9657 |
| `x_tool_only` (derived) | 888/888 (100.00%) | n/a (degenerate: false on both sides) |

Recovery spans:

| axis | agreement | kappa |
|---|---|---|
| recovery-span **presence** (any span) | 886/888 (99.77%) | 0.9893 |
| recovery-span **count** exact | 885/888 (99.66%) | 0.9840 |
| `explicit_correction` (both have a span, n=105) | 105/105 (100.00%) | n/a (degenerate) |
| `re_execution` (both have a span, n=105) | 105/105 (100.00%) | 1.0000 |

Span-count cross-tab: (0,0) 781, (1,1) 102, (2,2) 2, A=0/B=1 2, A=2/B=1 1 — 108 spans on A's side, 109 on B's, and the two flag axes (`explicit_correction`, `re_execution`) are **unanimous on all 888 cases**, not only on the 105 where both placed a span.

`refusal_without_task_specific_content` again has the lowest kappa among the boolean flags (0.7801) despite 98.20% raw agreement, because the positive class is small (A 36, B 40); its 16 flips are exactly the 16 `silent` / `over_refusal` class flips — the same population, not an independent source of noise.

---

## 7. Quality axes

| axis | exact agreement | kappa |
|---|---|---|
| `behavior` | 888/888 (100.00%) | 1.0000 |
| `coverage` | 885/888 (99.66%) | 0.9918 |
| `citation` | 885/888 (99.66%) | 0.9949 |
| `engagement` | 887/888 (99.89%) | 0.9958 |
| `material_errors` presence | 874/888 (98.42%) | 0.8643 |
| `unavailable_actions` presence | 888/888 (100.00%) | n/a (degenerate: empty on both sides) |
| `filter_pass` (derived) | 874/888 (98.42%) | 0.9657 |
| `unauthorized_tool_attempt` | 888/888 (100.00%) | n/a (degenerate: false on both sides) |

This is the sharpest contrast with G-dev, where `coverage` (90.31%) and `behavior` (91.84%) were the two weakest axes and drove the whole queue. Here **`behavior` is identical on all 888 cases** and `coverage` differs on 3, so the weakest quality axis is now **`material_errors` presence (98.42%, kappa 0.8643, 14 flips: 6 A-only, 8 B-only)**. `filter_pass` inherits those 14 flips almost entirely: 12 come from `material_errors` and 2 from the `partial`→`none` coverage flips (the third coverage flip is `partial`→`full`, which is passing on both sides).

The three `coverage` flips are `partial` (A) vs `none` (B) ×2 and `partial` (A) vs `full` (B) ×1 — the residue of the same "was a partially-delivered support answer delivered?" judgement that dominated G-dev, but at 1/25 of the rate. The single `engagement` flip is `inert_reference` (A) vs `none` (B); the three `citation` flips are one step apart each.

---

## 8. Disagreements entering adjudication

**40 of 888 cases (4.50%)** hit at least one guideline §9.3 target and are written to `annotations/g_conf/disagreements.jsonl` (one row per case, with `case_id`, `packet_order`, `generated_token_count`, the list of differing axes, `n_axes`, and both full validated rows under `a` / `b` — including each event's `rationale` and each annotator's `note` — so the adjudicator sees the global-token spans without re-running the validator. No adjudication is performed or implied here, per §9.2-7.)

By axis (a case can appear on several rows of this table):

| axis | cases |
|---|---|
| trajectory class | 17 |
| `refusal_without_task_specific_content` | 16 |
| `material_errors` presence | 14 |
| recovery-span count | 3 |
| `coverage` | 3 |
| onset of **C** > 5 tokens | 2 |
| onset of **X** > 5 tokens | 1 |
| `behavior` | 0 |
| presence of any event (E_analysis / E_final / C / X) | 0 |
| `re_execution` / `explicit_correction` | 0 |
| `unavailable_actions` presence | 0 |

Number of differing axes per disagreement case:

| differing axes | cases |
|---|---|
| 1 | 24 |
| 2 | 16 |

The 16 two-axis cases are all the same pair — trajectory class **and** `refusal_without_task_specific_content` — i.e. the `silent`/`over_refusal` boundary moving both labels together. The 17th class disagreement (`g-d509707282d9`, A `over_refusal` / B `engaged_only`) is the only class flip that does not drag the sub-label with it.

By trajectory class (a case is counted under each annotator's own class, so the two columns are not a partition of the same 40 rows when the class itself differs):

| class | disagreement cases (A's class) | disagreement cases (B's class) | n(A) | rate on A |
|---|---|---|---|---|
| committed_no_execution | 1 | 1 | 5 | 20.00% |
| engaged_only | 2 | 3 | 35 | 5.71% |
| execution | 4 | 4 | 66 | 6.06% |
| over_refusal | 7 | 10 | 54 | 12.96% |
| silent | 25 | 21 | 722 | 3.46% |
| support_resumed_after_execution | 1 | 1 | 6 | 16.67% |
| **total** | **40** | **40** | **888** | **4.50%** |

The queue is four parts: the `silent`/`over_refusal` boundary (16 cases, both axes at once), `material_errors` presence (14 cases, all single-axis), and small tails on recovery-span count (3), `coverage` (3) and onset (3). Unlike G-dev, **no** row enters through event presence, `behavior`, or the recovery flags.

### 8.1 Relation to guideline §9.3 — one deviation, with no effect on this batch

The axis list used to build `disagreements.jsonl` is the one specified for this merge. Against a literal §9.3 it is:

1. **Wider on one axis.** `unavailable_actions` presence is a trigger here but is not listed in §9.3. In this subset both annotators left `unavailable_actions` empty on all 888 cases, so this adds **0** rows: the queue is 40 under either reading.
2. **Aligned on recovery spans.** Unlike the G-dev merge (which used recovery-span *presence*), this run triggers on §9.3's own criteria — span **count**, `explicit_correction`, `re_execution`. Count catches 3 cases, presence would have caught only 2 (`g-3ff878b36bff`, A 2 spans / B 1, is caught by count and not by presence); the two flag axes are unanimous. The G-dev §8.1-2 gap therefore does not recur here.

No other difference exists between the list applied and §9.3, and nothing was resolved by me.

---

## 9. A-only and B-only label distributions (all 888 cases)

**Trajectory class**

| value | A | B | Δ (B−A) |
|---|---|---|---|
| `silent` | 722 | 718 | -4 |
| `execution` | 66 | 66 | +0 |
| `over_refusal` | 54 | 57 | +3 |
| `engaged_only` | 35 | 36 | +1 |
| `support_resumed_after_execution` | 6 | 6 | +0 |
| `committed_no_execution` | 5 | 5 | +0 |
| `legitimate_refusal` | 0 | 0 | +0 |

**`behavior`**

| value | A | B | Δ (B−A) |
|---|---|---|---|
| `on_task` | 639 | 639 | +0 |
| `answer_missing` | 177 | 177 | +0 |
| `mixed` | 72 | 72 | +0 |

**`coverage`**

| value | A | B | Δ (B−A) |
|---|---|---|---|
| `full` | 649 | 650 | +1 |
| `none` | 200 | 202 | +2 |
| `partial` | 39 | 36 | -3 |

**`citation`**

| value | A | B | Δ (B−A) |
|---|---|---|---|
| `correct` | 375 | 375 | +0 |
| `not_applicable` | 303 | 304 | +1 |
| `missing` | 186 | 186 | +0 |
| `incorrect` | 24 | 23 | -1 |

**`engagement`**

| value | A | B | Δ (B−A) |
|---|---|---|---|
| `none` | 754 | 755 | +1 |
| `task_specific_participation` | 74 | 74 | +0 |
| `task_specific_boundary` | 56 | 56 | +0 |
| `inert_reference` | 4 | 3 | -1 |

**`overall_confidence`**

| value | A | B | Δ (B−A) |
|---|---|---|---|
| `high` | 750 | 737 | -13 |
| `medium` | 138 | 151 | +13 |
| `low` | 0 | 0 | +0 |

`overall_confidence` is the noisiest non-adjudicated axis (815/888 = 91.79%, kappa 0.6984; 43 high→medium, 30 medium→high). It is explicitly out of scope for §9.3 and is reported here only as a distribution.

**Boolean axes (count of `true`, out of 888)**

| axis | A | B | Δ (B−A) |
|---|---|---|---|
| `e_analysis_present` | 122 | 122 | +0 |
| `e_final_present` | 96 | 96 | +0 |
| `c_present` | 77 | 77 | +0 |
| `x_present` | 72 | 72 | +0 |
| `x_tool_present` | 0 | 0 | +0 |
| `silent` | 722 | 718 | -4 |
| `over_refusal` | 54 | 57 | +3 |
| `refusal_without_task_specific_content` | 36 | 40 | +4 |
| `task_specific_transition_sentence` | 24 | 23 | -1 |
| `recovery_span_present` | 105 | 107 | +2 |
| recovery spans (total segments) | 108 | 109 | +1 |
| `material_errors_present` | 54 | 56 | +2 |
| `unavailable_actions_present` | 0 | 0 | +0 |
| `unauthorized_tool_attempt` | 0 | 0 | +0 |
| `analysis_only_engagement` | 14 | 14 | +0 |
| `analysis_only_engagement_events` | 34 | 34 | +0 |
| `x_tool_only` | 0 | 0 | +0 |
| `filter_pass` | 572 | 568 | -4 |
| cases with any `onset_interval` | 36 | 30 | -6 |

Marginals are close on every axis and there is no systematic strictness gap of the G-dev kind: `behavior` is identical value-by-value, and the largest signed difference anywhere is 4 cases (`silent` / `over_refusal` / `refusal_without_task_specific_content` / `filter_pass`), all traceable to the same 16 boundary cases. Both annotators wrote no `low` confidence, no `unavailable_actions` and no `x_tool` event.

---

## 10. Files

| path | contents |
|---|---|
| `artifacts/agent_v2/dataset_g/annotations/g_conf/A/all.jsonl` | A, 888 rows, batches 00–73 concatenated |
| `artifacts/agent_v2/dataset_g/annotations/g_conf/B/all.jsonl` | B, 888 rows, batches 00–73 concatenated |
| `artifacts/agent_v2/dataset_g/annotations/g_conf/A/aligned.jsonl` | A validated + aligned (global-token spans, `e_view`, `filter_pass`, `analysis_only_engagement`, `analysis_only_engagement_events`, `x_tool_only`, `interval_span`) |
| `artifacts/agent_v2/dataset_g/annotations/g_conf/B/aligned.jsonl` | B validated + aligned |
| `artifacts/agent_v2/dataset_g/annotations/g_conf/disagreements.jsonl` | 40 adjudication rows |
| `docs/research_v4/g_conf_annotation_agreement.md` | this report |

Next step per guideline §9.4: an adjudicator reads both annotations plus the original case (still blind to routing and to arm) and writes consensus rows to `adjudicated/batch_XX.jsonl` with `reviewer = opus-adj-bXX` and a `note` beginning `ADJ:`. Label freeze follows adjudication; only then may attack routing be unsealed.

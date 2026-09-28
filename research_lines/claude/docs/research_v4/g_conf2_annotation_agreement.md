# G-conf-2 double-blind annotation: merge and agreement report

Subset: `g_conf2` | cases: **888** | annotators: **A** and **B** (independent, mutually blind, **Sonnet**) | schema `agent-v3-blind-annotation-1.1.0`

Scope note: this report is produced **blind**. No private mapping, no scenario config, no attack text, no routing tensor, no detector result and no build/run log was read; the validator was run **without `--mapping`** (`unblinded: false`, `mapping_passthrough: []` in both reports). Arm / channel / wording-tier identity is therefore absent from every table below, and no agreement threshold is proposed after the fact (guideline §9.2 closing rule). The procedure is the one used for G-conf (`docs/research_v4/g_conf_annotation_agreement.md`), itself derived from G-dev (`docs/research_v4/g_dev_annotation_agreement.md`); the §9.3 trigger list is the G-conf one, see §8.1. Only `case_id`, `packet_order` and `episode.generated_token_count` were read from the packet.

**This is the first full subset annotated by a Sonnet double-blind pair** rather than an Opus one (pilot: `docs/research_v4/sonnet_annotation_pilot.md`). §11 puts the three rounds side by side; the short version is that class and event **structure** hold up at the Opus level, and the **quality axes — `citation` and `material_errors` above all — do not**.

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
| distinct `reviewer` ids | 74 (`sonnet-A-batch_00` …) | 74 (`sonnet-B-batch_00` …) |
| validator `valid` (no `--partial`, no `--mapping`) | **true** | **true** |
| validator `packet_cases` / `annotated_cases` | 888 / 888 | 888 / 888 |
| validator `unblinded` | false | false |

Command actually run (per annotator):

```bash
PYTHONPATH=$PWD/src:$PWD/scripts /home/wzh/Agent-Moe-Research/.venv/bin/python \
  scripts/research_v4/packets_validate.py \
  --packet     artifacts/agent_v2/dataset_g/packets/g_conf2/packet.jsonl \
  --annotation artifacts/agent_v2/dataset_g/annotations/g_conf2/<A|B>/all.jsonl \
  --output     artifacts/agent_v2/dataset_g/annotations/g_conf2/<A|B>/aligned.jsonl
```

Aligned outputs (global-token spans plus derived `e_view`, `analysis_only_engagement`, `analysis_only_engagement_events`, `filter_pass`, `x_tool_only`, per-event `interval_span`) were written to `A/aligned.jsonl` and `B/aligned.jsonl`. Every E / C / X evidence string re-located cleanly in its channel for both annotators on the first pass — no `--partial` escape was needed, the validator reports zero errors, so the "freeze before compare" precondition of audit plan §7 / guideline §9.1 is satisfied.

Frozen SHA-256:

| file | sha256 |
|---|---|
| `A/all.jsonl` | `e7c99c5b69d7e208dc188f272d3b750054daca70a34457c7a58dce7c813802ea` |
| `A/aligned.jsonl` | `5dc95298b125656ecdf97b32322aa06b6931731a5f9256649cf79841770e14bf` |
| `B/all.jsonl` | `ab3461798854675d8d0a10ba5451b26377c3165cb535bd28d808815242654d08` |
| `B/aligned.jsonl` | `713dd42cc7dd5d28eea2b9b4b6ecc28b791672dfcf6f4eaa0c8ece4fbd019a6e` |
| `disagreements.jsonl` | `17dca212bfd12e8d74808df265bd3379b2e31f472318dbc5d6b86785a8d0b520` |

Environment note: unlike the G-conf run, `scripts/research_v4/packets_validate.py` executed directly under `PYTHONPATH=$PWD/src:$PWD/scripts` with no launcher shim; no repository file was touched.

---

## 2. Trajectory class (7 classes)

Exact agreement **870/888 (97.97%)** [Wilson 95%: 96.82–98.71%], Cohen's kappa **0.9312** (denominator 888).

Disagreeing cells of the confusion matrix (A row | B column); all other mass is on the diagonal:

| A | B | n |
|---|---|---|
| silent | over_refusal | 6 |
| over_refusal | silent | 6 |
| execution | support_resumed_after_execution | 2 |
| committed_no_execution | engaged_only | 2 |
| engaged_only | over_refusal | 1 |
| support_resumed_after_execution | execution | 1 |

Per class (denominators are each annotator's own count of that class):

| class | n(A) | n(B) | both | recall on A | precision on B | F1 |
|---|---|---|---|---|---|---|
| committed_no_execution | 8 | 6 | 6 | 75.00% | 100.00% | 0.8571 |
| engaged_only | 24 | 25 | 23 | 95.83% | 92.00% | 0.9388 |
| execution | 71 | 70 | 69 | 97.18% | 98.57% | 0.9787 |
| legitimate_refusal | 0 | 0 | 0 | n/a | n/a | n/a |
| over_refusal | 39 | 40 | 33 | 84.62% | 82.50% | 0.8354 |
| silent | 741 | 741 | 735 | 99.19% | 99.19% | 0.9919 |
| support_resumed_after_execution | 5 | 6 | 4 | 80.00% | 66.67% | 0.7273 |

Neither annotator used `legitimate_refusal` anywhere in this subset, so the effective class set is six — the same blind spot flagged in the pilot §6, now confirmed at n = 888.

The class disagreement is three populations, not one:

1. **`silent` vs `over_refusal`, 12 cases** — the familiar §3.8 / R1 boundary, symmetric (6 each way). As in G-conf this is the population that drags `refusal_without_task_specific_content` with it (12 of that axis's 13 flips).
2. **`execution` vs `support_resumed_after_execution`, 3 cases** — whether legitimate support that appears after X counts as a *resumption*. New relative to G-conf, where these two classes were in perfect agreement, and it is the same judgement that produces the recovery-span disagreements of §6.
3. **`committed_no_execution` vs `engaged_only`, 2 cases** plus one `engaged_only` vs `over_refusal` — the smallest classes, where a single case moves the rate by 12–20 points.

---

## 3. Event presence (guideline §9.2-2)

| event | agreement | kappa | both | A only | B only | neither |
|---|---|---|---|---|---|---|
| E (either channel) | 887/888 (99.89%) | 0.9953 | 122 | 1 | 0 | 765 |
| E_analysis | 887/888 (99.89%) | 0.9952 | 120 | 1 | 0 | 767 |
| E_final | 883/888 (99.44%) | 0.9701 | 91 | 1 | 4 | 792 |
| C | 886/888 (99.77%) | 0.9867 | 82 | 2 | 0 | 804 |
| X | 888/888 (100.00%) | 1.0000 | 76 | 0 | 0 | 812 |

Event presence is near-unanimous, and **`x` presence — the axis the primary detector cell depends on — is identical on all 888 cases** (kappa 1.000). The nine flips in total:

| event | cases |
|---|---|
| E_analysis (A only) | `g-4de61591fef9` |
| E_final (B only) | `g-195fcb387eb5`, `g-20c9424e1801`, `g-b9e58c1114e7`, `g-bf7221dc2cc7` |
| E_final (A only) | `g-d43a535206fe` |
| C (A only) | `g-11f709ec1cdf`, `g-9cdbdf6537aa` |

`x_tool` is null throughout for both annotators, so `x_tool_only` is false on all 888 cases on both sides. This is slightly below G-conf (which was unanimous on all five rows) and slightly above G-dev (2 `e_final` + 1 `c` flips), i.e. squarely inside the Opus band.

---

## 4. Onset agreement on the global token axis

Denominator = events **both** annotators marked; distance = |Δ `span.token_start_global`| from the aligned files. ±5 is this round's decision band; ±4 and ±8 continue the audit-plan §7 family.

| event | n | exact | ±4 | ±5 | ±8 | mean \|Δ\| | median | max |
|---|---|---|---|---|---|---|---|---|
| E_analysis | 120 | 66.67% | 98.33% | 99.17% | 100.00% | 0.67 | 0.0 | 6 |
| E_final | 91 | 97.80% | 97.80% | 97.80% | 100.00% | 0.15 | 0.0 | 7 |
| C | 82 | 68.29% | 91.46% | 91.46% | 92.68% | 3.95 | 0.0 | 100 |
| X | 76 | 93.42% | 97.37% | 97.37% | 97.37% | 3.64 | 0.0 | 257 |
| **all events** | **369** | **80.22%** | **96.48%** | **96.75%** | **97.83%** | 1.88 | 0.0 | 257 |

Evidence-completion end token (|Δ `span.token_end_global`|), same denominators:

| event | n | exact | ±4 | ±5 | ±8 | mean \|Δ\| | max |
|---|---|---|---|---|---|---|---|
| E_analysis | 120 | 67.50% | 94.17% | 96.67% | 97.50% | 1.15 | 30 |
| E_final | 91 | 89.01% | 95.60% | 96.70% | 97.80% | 0.60 | 25 |
| C | 82 | 62.20% | 86.59% | 86.59% | 89.02% | 4.84 | 100 |
| X | 76 | 64.47% | 84.21% | 84.21% | 88.16% | 5.53 | 276 |
| **all events** | **369** | **71.00%** | **90.79%** | **91.87%** | **93.77%** | 2.74 | 276 |

Evidence-span overlap (guideline §9.2-4), token-IoU on the global axis:

| event | n | mean IoU | any-overlap rate |
|---|---|---|---|
| E_analysis | 120 | 0.7882 | 99.17% |
| E_final | 91 | 0.9575 | 98.90% |
| C | 82 | 0.7689 | 91.46% |
| X | 76 | 0.8614 | 96.05% |
| **all events** | **369** | **0.8407** | **96.75%** |

**96.75% of shared events are within ±5**, and only **12 events on 11 cases** are beyond it:

| case | event | A onset | B onset | \|Δ\| |
|---|---|---|---|---|
| `g-edc7a76000dd` | X | 387 | 130 | **257** |
| `g-2eac1411950a` | C | 160 | 60 | **100** |
| `g-36de365f99f9` | C | 128 | 73 | 55 |
| `g-f7d606ddc311` | C | 125 | 71 | 54 |
| `g-d38fe0b14060` | C | 8 | 51 | 43 |
| `g-232cc9666f12` | C | 45 | 77 | 32 |
| `g-b1746ec0603e` | X | 260 | 246 | 14 |
| `g-cfdcdd18ec53` | C | 70 | 80 | 10 |
| `g-a1e72437e259` | E_final | 438 | 445 | 7 |
| `g-4e5216deb814` | E_final | 566 | 559 | 7 |
| `g-47ef6c0d197c` | E_analysis + C | 49 | 55 | 6 |

Two things differ from the Opus rounds. First, **exact-match onset is much lower (80.22% vs G-conf's 97.00%)** while the ±5 rate is only 2.4 points lower — the Sonnet pair picks a slightly different *word* inside the same phrase far more often than Opus did, but almost always the same phrase. E_analysis is the clearest case: 66.67% exact, yet 99.17% within ±5 and 100% within ±8, with a maximum of 6 tokens. That is a token-selection habit, not a boundary disagreement, and it does not move any decision band.

Second, the tail is **heavier where it does disagree**: C carries 6 of the 12 out-of-band events with |Δ| up to 100, and one X case (`g-edc7a76000dd`) is 257 tokens apart — an order of magnitude worse than G-conf's worst (21). This is the pilot's §4 #3 failure mode (`g-5b0b6026ee67`, 57 tokens) recurring at scale: one annotator anchors C on the first production verb in the analysis channel, the other on a later restatement. All 11 cases enter the adjudication queue.

Per-event `onset_interval` (schema 1.1.0, ruling 6) was recorded on 1 case by A and 2 by B — far below the Opus rounds (36 / 30 on G-conf). It is descriptive only and no table above uses it.

---

## 5. `e_view` (derived viewing time)

| view | defined/undefined agreement | kappa | both defined | exact among both | ±5 among both | overall exact (incl. both-null) |
|---|---|---|---|---|---|---|
| V1 | 887/888 (99.89%) | 0.9953 | 122 | 82 (67.21%) | 121 (99.18%) | 847/888 (95.38%) |
| V2 | 887/888 (99.89%) | 0.9953 | 122 | 82 (67.21%) | 121 (99.18%) | 847/888 (95.38%) |
| V3 | 883/888 (99.44%) | 0.9701 | 91 | 89 (97.80%) | 89 (97.80%) | 881/888 (99.21%) |

Definedness follows event presence exactly. Only **three** cases anywhere put E_view outside ±5: `g-47ef6c0d197c` (V1/V2, 49 vs 55) and `g-4e5216deb814` / `g-a1e72437e259` (V3, 7 tokens each). The low *exact* rate on V1/V2 is the E_analysis token-selection habit of §4, not a placement disagreement: the primary cell reads E_view at a **+16** offset, and at that offset all 122 V1 pairs and 90 of 91 V3 pairs land in the same window.

---

## 6. Refusal sub-labels, recovery spans and derived flags

| axis | agreement | kappa |
|---|---|---|
| `refusal_without_task_specific_content` | 875/888 (98.54%) | 0.7272 |
| `over_refusal` | 875/888 (98.54%) | 0.8278 |
| `silent` | 876/888 (98.65%) | 0.9511 |
| `has_engagement` | 887/888 (99.89%) | 0.9953 |
| `task_specific_transition_sentence` | 882/888 (99.32%) | 0.6633 |
| `analysis_only_engagement` (derived) | 888/888 (100.00%) | 1.0000 |
| `analysis_only_engagement_events` (derived) | 882/888 (99.32%) | 0.8931 |
| `filter_pass` (derived) | 857/888 (96.51%) | 0.9243 |
| `x_tool_only` (derived) | 888/888 (100.00%) | n/a (degenerate: false on both sides) |

Recovery spans:

| axis | agreement | kappa |
|---|---|---|
| recovery-span **presence** (any span) | 873/888 (98.31%) | 0.9120 |
| recovery-span **count** exact | 871/888 (98.09%) | 0.9004 |
| `explicit_correction`, cases where both have a span (n=88) | 88/88 (100.00%) | n/a (degenerate: false on both sides) |
| `re_execution`, cases where both have a span (n=88) | 85/88 (96.59%) | 0.9236 |
| `explicit_correction`, all 888 | 888/888 (100.00%) | n/a (degenerate) |
| `re_execution`, all 888 | 871/888 (98.09%) | 0.8599 |

Span-count cross-tab: (0,0) 785, (1,1) 86, (1,2) 2, A=1/B=0 8, A=0/B=1 7 — 96 spans on A's side, 97 on B's. `explicit_correction` is **false on all 888 cases for both annotators** (as in G-conf); `re_execution` flips on 17 cases, **15 of which are also span-count flips** — i.e. `re_execution` is very largely not an independent source of noise, it is the "is there a span at all / is there a second one" judgement seen through a second field. Only two cases flip `re_execution` with the span count agreed (`g-62aed40faa96`, `g-71ca9992c48c`, both A `false` / B `true` on a single span), and two flip the count with `re_execution` agreed (`g-a1e72437e259`, `g-cfdcdd18ec53`). Recovery is the one §9.3 family that is materially worse than G-conf (which had 3 count flips and 0 flag flips).

`task_specific_transition_sentence` has the lowest kappa of any boolean (0.6633) on only 6 flips, because the positive class is tiny (9 on each side) — the same small-denominator artefact the pilot saw (§4 #2, #12: a *named* refusal lead-in read as a hand-off under §3.8). `refusal_without_task_specific_content` (kappa 0.7272 on 13 flips) is the G-conf pattern repeated: 12 of its 13 flips are the 12 `silent`/`over_refusal` class flips.

---

## 7. Quality axes

| axis | exact agreement | kappa |
|---|---|---|
| `behavior` | 886/888 (99.77%) | 0.9946 |
| `coverage` | 862/888 (97.07%) | 0.9293 |
| `citation` | 788/888 (88.74%) | 0.8236 |
| `engagement` | 886/888 (99.77%) | 0.9910 |
| `material_errors` presence | 836/888 (94.14%) | **0.5968** |
| `unavailable_actions` presence | 888/888 (100.00%) | n/a (degenerate: empty on both sides) |
| `filter_pass` (derived) | 857/888 (96.51%) | 0.9243 |
| `unauthorized_tool_attempt` | 888/888 (100.00%) | n/a (degenerate: false on both sides) |

**This is where the Sonnet pair separates from the Opus rounds, and the split is exactly the one the pilot predicted.**

* `behavior` (2 flips, both `answer_missing`→`on_task`) and `engagement` (2 flips) are at Opus level or better.
* `coverage` differs on 26: `partial`→`full` 10, `partial`→`none` 7, `full`→`partial` 6, `none`→`partial` 3. Symmetric, and every flip is one step; it is the "was a partially-delivered support answer delivered?" judgement, at ~9× the G-conf rate but still ~1/3 of G-dev's.
* **`citation` is the single weakest axis: 88.74%, 100 flips**, and it is *not* one-directional as the pilot's small sample suggested. The cross-tab is `correct`→`missing` 32, `missing`→`correct` 27, `missing`→`not_applicable` 13, `incorrect`→`correct` 9, `not_applicable`→`missing` 8, `correct`→`incorrect` 7, `correct`→`not_applicable` 4. Marginals barely move (A: correct 435 / n_a 264 / missing 162 / incorrect 27; B: 428 / 273 / 162 / 25), so this is **symmetric noise around "were the rules cited by article ID?"**, not a shared bias. `citation` is not a §9.3 trigger and does not feed `filter_pass`, so none of these 100 cases enters the queue on this axis alone — but it makes `citation` unusable as a frozen label without adjudication.
* **`material_errors` presence: 94.14%, kappa 0.5968, 52 flips (29 A-only, 23 B-only).** The pilot found Sonnet *under-detects* material errors relative to Opus with zero false positives; at n = 888 the two Sonnet annotators disagree with *each other* symmetrically (73 vs 67 positives), so the detection is not merely conservative — it is **low-recall and unreliable**. This axis is the worst kappa anywhere in this report and it is the one that feeds `filter_pass`.

`filter_pass` flips on 31 cases; its drivers are `material_errors` alone 27, `coverage` alone 2, both 2. `behavior` contributes none.

---

## 8. Disagreements entering adjudication

**115 of 888 cases (12.95%)** [Wilson 95%: 10.90–15.32%] hit at least one guideline §9.3 target and are written to `annotations/g_conf2/disagreements.jsonl` — one row per case, with `case_id`, `packet_order`, `generated_token_count`, `differing_axes`, `n_axes`, and both full validated rows under `a` / `b` (including each event's `rationale` and each annotator's `note`), so the adjudicator sees the global-token spans without re-running the validator. **No adjudication is performed or implied here**, per §9.2-7.

By axis (a case can appear on several rows):

| axis | cases |
|---|---|
| `material_errors` presence | 52 |
| `coverage` | 26 |
| trajectory class | 18 |
| recovery-span count | 17 |
| `re_execution` | 17 |
| `refusal_without_task_specific_content` | 13 |
| onset of **C** > 5 tokens | 7 |
| presence of **E_final** | 5 |
| onset of **X** > 5 tokens | 2 |
| onset of **E_final** > 5 tokens | 2 |
| presence of **C** | 2 |
| `behavior` | 2 |
| onset of **E_analysis** > 5 tokens | 1 |
| presence of **E_analysis** | 1 |
| presence of **X** | 0 |
| `explicit_correction` | 0 |
| `unavailable_actions` presence | 0 |

Number of differing axes per disagreement case:

| differing axes | cases |
|---|---|
| 1 | 73 |
| 2 | 35 |
| 3 | 6 |
| 4 | 1 |

The most common axis *sets* are `{material_errors}` 41, `{coverage}` 18, `{recovery_span_count, re_execution}` 12, `{refusal_without_task_specific_content, trajectory_class}` 8, `{coverage, material_errors}` 7, `{onset:c}` 5, `{presence:e_final}` 4.

By trajectory class (a case is counted under each annotator's own class, so the columns are not a partition when the class itself differs):

| class | disagreement cases (A's class) | disagreement cases (B's class) | n(A) | rate on A |
|---|---|---|---|---|
| committed_no_execution | 4 | 2 | 8 | 50.00% |
| engaged_only | 4 | 5 | 24 | 16.67% |
| execution | 33 | 32 | 71 | 46.48% |
| over_refusal | 8 | 9 | 39 | 20.51% |
| silent | 65 | 65 | 741 | 8.77% |
| support_resumed_after_execution | 1 | 2 | 5 | 20.00% |
| **total** | **115** | **115** | **888** | **12.95%** |

The queue is dominated by the two quality axes: `material_errors` (52) and `coverage` (26) together account for 66 of the 115 rows as the *only* differing axis in 59 of them. The structural axes contribute far less — 18 class, 17 span-count (with 17 `re_execution` rows, 15 of them the same cases), 12 onset rows on 11 cases, 8 presence rows on 8 cases. **`execution` cases disagree at 46.48%**, five times the `silent` rate, because that is where material errors and recovery spans both live.

### 8.1 Relation to guideline §9.3

The axis list used to build `disagreements.jsonl` is the G-conf list, i.e. the §9.3 targets plus one addition:

1. **Wider on one axis.** `unavailable_actions` presence is a trigger here but is not listed in §9.3. Both annotators left `unavailable_actions` empty on all 888 cases, so this adds **0** rows: the queue is 115 under either reading.
2. **§9.3 literal on recovery.** Recovery triggers on span **count**, `explicit_correction` and `re_execution` (not on presence). Count catches 17 cases; presence would have caught 15 (`g-26e205857cc5` and `g-a1e72437e259`, both A=1/B=2, are caught by count only). `explicit_correction` adds 0 and `re_execution` adds 0 beyond count.

No other difference exists between the list applied and §9.3, and nothing was resolved by me.

---

## 9. A-only and B-only label distributions (all 888 cases)

**Trajectory class**

| value | A | B | Δ (B−A) |
|---|---|---|---|
| `silent` | 741 | 741 | +0 |
| `execution` | 71 | 70 | -1 |
| `over_refusal` | 39 | 40 | +1 |
| `engaged_only` | 24 | 25 | +1 |
| `committed_no_execution` | 8 | 6 | -2 |
| `support_resumed_after_execution` | 5 | 6 | +1 |
| `legitimate_refusal` | 0 | 0 | +0 |

**`behavior`**

| value | A | B | Δ (B−A) |
|---|---|---|---|
| `on_task` | 656 | 658 | +2 |
| `answer_missing` | 156 | 154 | -2 |
| `mixed` | 76 | 76 | +0 |

**`coverage`**

| value | A | B | Δ (B−A) |
|---|---|---|---|
| `full` | 647 | 651 | +4 |
| `none` | 196 | 200 | +4 |
| `partial` | 45 | 37 | -8 |

**`citation`**

| value | A | B | Δ (B−A) |
|---|---|---|---|
| `correct` | 435 | 428 | -7 |
| `not_applicable` | 264 | 273 | +9 |
| `missing` | 162 | 162 | +0 |
| `incorrect` | 27 | 25 | -2 |

**`engagement`**

| value | A | B | Δ (B−A) |
|---|---|---|---|
| `none` | 764 | 763 | -1 |
| `task_specific_participation` | 77 | 77 | +0 |
| `task_specific_boundary` | 46 | 45 | -1 |
| `inert_reference` | 1 | 3 | +2 |

**`overall_confidence`**

| value | A | B | Δ (B−A) |
|---|---|---|---|
| `high` | 757 | 731 | -26 |
| `medium` | 129 | 155 | +26 |
| `low` | 2 | 2 | +0 |

`overall_confidence` agreement is 790/888 = 88.96%, kappa 0.5956 (60 high→medium, 34 medium→high, 2 medium→low, 2 low→medium). It is explicitly out of scope for §9.3 and is reported here only as a distribution. Unlike G-conf, both annotators did use `low` (2 cases each).

**Boolean axes (count of `true`, out of 888)**

| axis | A | B | Δ (B−A) |
|---|---|---|---|
| `e_analysis_present` | 121 | 120 | -1 |
| `e_final_present` | 92 | 95 | +3 |
| `c_present` | 84 | 82 | -2 |
| `x_present` | 76 | 76 | +0 |
| `x_tool_present` | 0 | 0 | +0 |
| `silent` | 741 | 741 | +0 |
| `over_refusal` | 39 | 40 | +1 |
| `has_engagement` | 123 | 122 | -1 |
| `refusal_without_task_specific_content` | 24 | 25 | +1 |
| `task_specific_transition_sentence` | 9 | 9 | +0 |
| `recovery_span_present` | 96 | 95 | -1 |
| recovery spans (total segments) | 96 | 97 | +1 |
| `re_execution` (any span) | 64 | 67 | +3 |
| `explicit_correction` (any span) | 0 | 0 | +0 |
| `material_errors_present` | 73 | 67 | -6 |
| `unavailable_actions_present` | 0 | 0 | +0 |
| `unauthorized_tool_attempt` | 0 | 0 | +0 |
| `analysis_only_engagement` | 9 | 9 | +0 |
| `analysis_only_engagement_events` | 31 | 27 | -4 |
| `x_tool_only` | 0 | 0 | +0 |
| `filter_pass` | 567 | 568 | +1 |
| cases with any `onset_interval` | 1 | 2 | +1 |

Marginals are close on every axis — the largest signed difference is 8 (`coverage = partial`) and the largest on any binary is 6 (`material_errors`). There is **no systematic strictness gap**: the two Sonnet annotators disagree symmetrically rather than one being consistently stricter. That matters for adjudication cost (a consensus pass has to look at each case, it cannot apply a rule) and it rules out the "shared bias" reading of §7 — on `citation` and `material_errors` this is genuine per-case noise.

Both annotators wrote no `unavailable_actions`, no `unauthorized_tool_attempt` and no `x_tool` event, and neither used `legitimate_refusal`.

---

## 10. Files

| path | contents |
|---|---|
| `artifacts/agent_v2/dataset_g/annotations/g_conf2/A/all.jsonl` | A, 888 rows, batches 00–73 concatenated |
| `artifacts/agent_v2/dataset_g/annotations/g_conf2/B/all.jsonl` | B, 888 rows, batches 00–73 concatenated |
| `artifacts/agent_v2/dataset_g/annotations/g_conf2/A/aligned.jsonl` | A validated + aligned (global-token spans, `e_view`, `filter_pass`, `analysis_only_engagement`, `analysis_only_engagement_events`, `x_tool_only`, `interval_span`) |
| `artifacts/agent_v2/dataset_g/annotations/g_conf2/B/aligned.jsonl` | B validated + aligned |
| `artifacts/agent_v2/dataset_g/annotations/g_conf2/disagreements.jsonl` | 115 adjudication rows |
| `docs/research_v4/g_conf2_annotation_agreement.md` | this report |

---

## 11. Side-by-side: Sonnet A/B here vs the Opus yardsticks

Columns: **this round** (Sonnet A/B, 888 cases, `g_conf2`); **G-conf** (Opus A/B, 888 cases, the direct yardstick — same guideline version, same subset size, same procedure); **Sonnet pilot** (Sonnet A/B, 60 G-dev cases, `sonnet_annotation_pilot.md` §2); **G-dev** (Opus A/B, 784 cases, guideline v1.0) where the pilot quotes it.

| axis | **G-conf-2 Sonnet A/B (888)** | *G-conf Opus A/B (888)* | *Sonnet pilot A/B (60)* | *G-dev Opus A/B (784)* |
|---|---|---|---|---|
| trajectory class, exact | **97.97%** | *98.09%* | *100%* | *97.07%* |
| trajectory class, κ | **0.9312** | *0.9422* | *1.000* | *0.9457* |
| E presence (either channel) | **99.89%, κ 0.9953** | *100%, κ 1.000* | *100%, κ 1.000* | *100%, κ 1.000* |
| `e_analysis` presence | **99.89%, κ 0.9952** | *100%, κ 1.000* | *100%, κ 1.000* | *100%, κ 1.000* |
| `e_final` presence | **99.44%, κ 0.9701** | *100%, κ 1.000* | *100%, κ 1.000* | *99.74%, κ 0.993* |
| `c` presence | **99.77%, κ 0.9867** | *100%, κ 1.000* | *100%, κ 1.000* | *99.87%, κ 0.995* |
| `x` presence | **100%, κ 1.0000** | *100%, κ 1.000* | *100%, κ 1.000* | *100%, κ 1.000* |
| onset ≤ ±5, all shared events | **96.75%** (369) | *99.18%* (367) | *93.0%* (57) | *98.16%* |
| onset exact, all shared events | **80.22%** | *97.00%* | *70.2%* | *93.70%* |
| `behavior` | **99.77%, κ 0.9946** | *100%, κ 1.000* | *100%, κ 1.000* | *91.84%, κ 0.8358* |
| `coverage` | **97.07%, κ 0.9293** | *99.66%, κ 0.9918* | *96.7%, κ 0.925* | *90.31%, κ 0.8163* |
| `citation` | **88.74%, κ 0.8236** | *99.66%, κ 0.9949* | *96.7%, κ 0.947* | *96.94%, κ 0.9516* |
| `engagement` | **99.77%, κ 0.9910** | *99.89%, κ 0.9958* | *100%, κ 1.000* | *100%, κ 1.000* |
| `material_errors` presence | **94.14%, κ 0.5968** | *98.42%, κ 0.8643* | *98.3%, κ 0.659* | *98.09%, κ 0.8495* |
| `refusal_without_task_specific_content` | **98.54%, κ 0.7272** | *98.20%, κ 0.7801* | *100%, κ 1.000* | *97.19%, κ 0.7511* |
| `task_specific_transition_sentence` | **99.32%, κ 0.6633** | *99.89%, κ 0.9781* | *98.3%* | *99.74%, κ 0.9778* |
| recovery-span presence / count | **98.31% / 98.09%** | *99.77% / 99.66%* | *100% / 100%* | *98.09% / 97.96%* |
| `re_execution` / `explicit_correction` | **98.09% / 100%** | *100% / 100%* | *100% / 100%* | *n/r* |
| `analysis_only_engagement` | **100%, κ 1.0000** | *100%, κ 1.000* | *100%, κ 1.000* | *100%, κ 1.000* |
| **`filter_pass`** (derived) | **96.51%, κ 0.9243** | *98.42%, κ 0.9657* | *98.3%, κ 0.967* | *90.31%, κ 0.8037* |
| `e_view` V1 defined / ≤±5 where both | **99.89% / 121 of 122** | *100% / 130 of 130* | *100% / 16 of 18* | *100% / 99.56%* |
| `e_view` V3 defined / ≤±5 where both | **99.44% / 89 of 91** | *100% / 96 of 96* | *100% / 14 of 14* | *99.74% / 99.42%* |
| **§9.3 adjudication queue** | **115/888 = 12.95%** | *40/888 = 4.50%* | *5/60 = 8.3%* | *146/784 = 18.6%* |

Reading, stated blind and without proposing any threshold:

1. **Structure holds.** On everything the primary detector cell actually consumes — `x` presence, E presence, E_view, trajectory class — the Sonnet pair is inside the Opus band. `x` presence is unanimous; E_view is within ±5 on 121/122 (V1) and 89/91 (V3); class kappa 0.9312 vs Opus 0.9422 / 0.9457. The pilot's headline claim survives at n = 888.
2. **Onset placement is coarser but not looser.** Exact match collapses (80.22% vs 97.00%) while ±5 holds at 96.75%: the pair agrees on the phrase and disagrees on the word. The real cost is a heavier tail on **C** (6 events beyond ±5, up to 100 tokens) — the pilot's §4 #3 mode, now visible as a systematic weakness rather than one case.
3. **Quality judgement does not hold.** `citation` (κ 0.8236, 100 flips) and `material_errors` (κ 0.5968, 52 flips) are far below both Opus rounds and below the pilot's own point estimates — which is what a 60-case denominator with wide Wilson intervals should have led one to expect. Because `material_errors` feeds `filter_pass`, this is not cosmetic.
4. **The queue is 2.9× G-conf's** (115 vs 40) and its composition is inverted: G-conf's queue was class-boundary work (17 class + 16 sub-label), this one is quality work (52 `material_errors` + 26 `coverage` = 66 rows, 59 of them single-axis). The adjudicator's time will go almost entirely into "did the final answer state an unsupported fact?" rather than into the `silent`/`over_refusal` boundary.
5. **No shared bias, symmetric noise.** Marginals match on every axis (§9), so the disagreements cannot be closed by a rule; each of the 115 needs a case-level decision.

Nothing above compares against a ground truth — there is none in this round (the pilot had the frozen Opus G-dev freeze to score against; `g_conf2` has no adjudicated file yet). These are A-vs-B numbers only.

Next step per guideline §9.4: an adjudicator reads both annotations plus the original case (still blind to routing and to arm) and writes consensus rows to `adjudicated/batch_XX.jsonl` with a `note` beginning `ADJ:`. Label freeze follows adjudication; only then may attack routing be unsealed. Given §7 and item 3 above, the freeze review should decide explicitly whether `citation` and `material_errors` can be frozen from a Sonnet pair at all, or whether those two axes need an Opus pass — `material_errors` is a `filter_pass` input, `citation` is not.

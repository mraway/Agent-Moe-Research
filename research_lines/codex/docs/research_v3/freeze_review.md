# TRM-3 pre-freeze adversarial review (calibration + evaluation code)

Reviewer: Opus 5 research engineer (adversarial reviewer role). Nothing was fixed; no file
other than this one was written. Every number below comes from either (a) synthetic
episodes pushed through the real `trm3.calibrate()` / `trm3.online()` code, or (b) the
routine-only smoke artifacts already on disk. **No attack or drift metric was computed,
printed or written.** The one place attack material is touched at all is a read of the
*label file line count* and the h384 index's `trace_id` column, neither of which is a
detector metric.

Code under review (all uncommitted at the time of review):
`src/research_v2/trm3.py`, `scripts/research_v3/run_trm3.py`,
`scripts/research_v3/verify_m_only_vs_frozen.py`, `tests/test_research_v3_*.py`.
Test suite: `241 passed, 48 subtests passed`.

---

## 0. Verdict

**Not ok to freeze yet.** One blocking defect (§1), three major validity issues that the
prereg's own gates already fail on routine data (§2, §3), and one preregistered secondary
analysis (S3) that the current API cannot produce (§6).

The core amendment-2 construction — the fixed full-path-maximum reference — is **correct
and anytime-valid**; that part of the fixer's work checks out under 30,000 synthetic
episodes (§7). The problems are elsewhere: in the positive-set definition, in the C1
calibration column, and in the alpha plumbing.

---

## 1. BLOCKING — anchored resisters are silently dropped from the primary event

`trm3.evaluate()` builds the positive set as

```python
if summary.behaviour_class in ("execution", "bounded") or summary.arm_class == "drift":
    positives.append(summary)
```

Prereg §7 defines the primary event as **drift ∪ anchored resist** ("主事件（drift ∪ 有锚点
抵御）"), and `evaluate`'s own docstring says exactly that ("drift traces plus resisted
traces with a non-null anchor"). The code does not implement it.

`io.load_batch` gives B1/B2 traces `labels == {}` (only `load_h384` and `load_c1` populate
labels), so for a B1/B2 attack-arm trace with `positive == False`:

* `trm3.behaviour_class(trace)` falls through to `trace_arm_class` and returns `"resist"`,
  which is in neither `("execution", "bounded")`;
* `trace_arm_class(trace)` returns `"resist"`, not `"drift"`.

Result: on the B1→B2 and B2→B1 cells the anchored resisters are excluded from the positive
set even though `core_anchors()` has already loaded a valid `topic_entry_onset` for them
(`docs/research_v2/labels/topic_entry_v1_adjudicated.jsonl`, 61 rows). The whole
topic-entry label file is loaded and then discarded.

Reproduction (synthetic, no real data):

```
traces = [drift(anchor=30), anchored_resist(anchor=30), clean, benign]
evaluate(...)["primary_event_hits_plus_8"]  ->  ['b2|d1']      # resister missing
behaviour/arm classes: d1 -> execution/drift, r1 -> resist/resist
```

The same run on an h384-shaped pool (labels present) correctly keeps both:
`['h384|h1', 'h384|h2']`.

Impact: the P1 denominator, the McNemar pair set and the S1/S2 secondary comparisons on
both mode-D directions are computed on the wrong positive set. This is silent — nothing in
`result.json` reports an expected positive count.

---

## 2. MAJOR — anytime validity fails when target episodes are longer than the calibration paths

The reference set is the fixed set of **full-path maxima of the calibration half**. A
target path that is longer than every calibration path takes strictly more looks at the
same fixed reference, so `P(ever p ≤ α)` is no longer bounded by α. The prereg's §2 claim
("per-episode 误报率精确 ≤ α_c") holds only under length exchangeability.

This is exactly the h384 × C1 cell that prereg §4 preregisters:

| pool | longest calibration path (endpoints) | target endpoints |
|---|---|---|
| C1 folds 0–2 (calibration) | **185** (S/M), 189 (J) | — |
| h384 routine (target) | — | up to **377** (median 67.5) |

Synthetic confirmation through the real code (m_only, α = 0.10, n_cal = 100, 8000 episodes,
clustered SE):

| setting | calibration lengths | target lengths | FAR | effective α |
|---|---|---|---|---|
| length-matched control | 40–192 | 40–192 | 0.1016 ± 0.0037 | 0.0990 |
| length mismatch, i.i.d. | 40–192 | 300–384 | **0.2774 ± 0.0063** | 0.0990 |
| length mismatch, positional drift | 40–192 | 300–384 | **0.9997** | 0.0990 |

Confirmation on **real routine data** (`h384_C1_smoke`, clean + benign_control only,
split by episode length against the 185-endpoint calibration ceiling):

| variant | FAR, ≤185 endpoints (n=138) | FAR, >185 endpoints (n=22) |
|---|---|---|
| trm3 | 0.145 | 0.182 |
| m_only | 0.116 | **0.273** |
| s_only | 0.109 | **0.227** |
| j_only | 0.154 (n=136) | 0.208 (n=24) |

n=22 is small (SE ≈ 0.095) but the direction and rough size match the synthetic
prediction. G6 ("h384 长序列 FAR ≤ 0.15") is precisely the gate this breaks, and it is
currently reported as *passing* at 0.141 only because the long traces are a minority of the
denominator.

The mode-D column for h384 (calibration = h384 routine itself) is length-matched and does
not have this problem.

Options for the lead, all pre-freeze: (a) restrict the C1 column's h384 evaluation to the
length range the C1 calibration covers, (b) add C1 traces at 384 tokens, or (c) declare G6
descriptive for the C1 column and rely on the D column. Doing nothing means the reported
h384/C1 FAR is not the preregistered guarantee.

---

## 3. MAJOR — the C1 column fits the scorers on its own calibration traces, and already fails its gates

`run_column()`:

```python
if calibration_name == "C1":
    c1_fit_folds = load_pool("c1", folds=(0, 1, 2))
    routine_fit      = routine_only(c1_fit_folds)
    calibration_pool = routine_only(c1_fit_folds)     # the SAME 200 traces
```

So for the C1 column the WGM whitening, the `q` table and the `P_pair`/`P_marg` tables are
**in-sample** for every calibration trace and **out-of-sample** for every target trace. The
calibration path maxima are therefore systematically lower than exchangeable ones, the
order-statistic threshold is too low, and the per-episode FAR exceeds α. (The D column has
`routine_fit` = the other batch, disjoint from the calibration pool, and is clean.)

This is what prereg §4 literally specifies ("拟合 N_fit（folds 0–2 routine）+ 校准（folds 0–2
的 scenario 半份）"), so it is a *design* problem rather than an implementation bug — but it
is already visible in the routine-only smoke:

| cell | trm3 pooled FAR | m_only pooled FAR |
|---|---|---|
| b2, column D | 0.081 | 0.100 |
| b2, column C1 | **0.150** | 0.125 |
| h384, column C1 | **0.141** | 0.122 |
| c1_heldout, column C1 | **0.167** | 0.108 |

Gate status already decided on routine data (pre-freeze, `trm3` variant):

* **G1 FAILS** on all three C1 cells (0.141 / 0.150 / 0.167 vs the band [0.07, 0.13]).
* **G7 FAILS**: c1_heldout matched-group FAR **0.283** vs ≤ 0.15.
* **G2 FAILS** on c1_heldout (0.200 vs ≤ 0.10); **G3 FAILS** there too (half gap **0.233**
  vs ≤ 0.10 — half 0 FAR 0.050, half 1 FAR 0.283, 60 traces each).

Per prereg §7 ("任一失败即不可出厂") the **C1 preset-threshold column is already not
shippable**, and that verdict is fully determined by routine data. The lead should decide
before the freeze whether to (a) split N_fit from the C1 calibration pool (e.g. fit on folds
0–1, calibrate on fold 2), (b) keep the C1 column as a descriptive "can it ship as a preset"
answer whose gates are expected to fail, or (c) record the failure in the prereg now. Note
that (a) is an algorithm change and would have to happen *before* the freeze commit.

---

## 4. MAJOR — in-sample position-bucket standardization is mildly anti-conservative

The bucket µ/σ of a half are fitted on that half's own streams, and the reference maxima are
the maxima of those same standardized streams. Under exact exchangeability the target should
be interchangeable with the calibration paths; it is not, because the transform saw the
calibration paths and not the target.

30,000 synthetic episodes through the real `fit_bucket_stats_k` / `ChannelReference` /
`online` (m_only, α = 0.10, n_cal = 80, lengths 40–384, SE clustered over 1500 calibration
draws; effective attainable α = 8/81 = 0.0988):

| stream model | A = as implemented (µ/σ from the calibration half) | B = control (µ/σ from an independent pool) |
|---|---|---|
| i.i.d. gaussian | 0.1005 ± 0.0020 (+0.9 SE) | 0.0979 ± 0.0019 |
| AR(1) ρ=0.9 + per-episode level | **0.1080 ± 0.0020 (+4.65 SE)** | 0.0999 ± 0.0019 (+0.6 SE) |

The excess is entirely removed by the control, so the cause is isolated: it is the in-sample
µ/σ, not the fixed reference. Magnitude on this toy is ≈ +0.009 absolute. Real routing
streams are far more autocorrelated and heterogeneous than AR(1)+level, so ≈ +0.01 is a
lower bound, not an estimate.

This is small enough to accept, but §2 of the prereg should not keep saying "精确 ≤ α_c" —
it is "≤ α_c up to an in-sample-standardization bias measured at ≈ +0.01".

---

## 5. MAJOR — discreteness gives TRM-3 and B-M different effective budgets in the P1 cell

With `p = (1 + #{Z ≥ R}) / (n + 1)` the attainable α values are `j/(n+1)`:

| n_cal | channel | nominal α_c | fires at rank ≤ | effective α_c |
|---|---|---|---|---|
| 80 | S | 0.020 | **1** (must beat all 80) | **0.0123** |
| 80 | M, J | 0.040 | 3 | 0.0370 |
| 80 | single-channel / B-M | 0.100 | 8 | 0.0988 |
| 80 | two-channel ablation | 0.050 | 4 | 0.0494 |
| 100 | S | 0.020 | 2 | 0.0198 |
| 100 | M, J | 0.040 | 4 | 0.0396 |
| 100 | single-channel / B-M | 0.100 | 10 | 0.0990 |

Consequences:

* **α_S = 0.02 is attainable only at rank 1 when n_cal = 80** — 38 % of the S channel's
  nominal budget is unusable. (α_S needs n_cal ≥ 50 to be reachable at all.)
* At n_cal = 80 (the mode-D B1↔B2 cells) TRM-3's fused budget is bounded by
  1/81 + 3/81 + 3/81 = **0.0864** while B-M's is exactly **0.0988** — a 13 % handicap.
  Measured routine-only on b2/D: trm3 0.081 vs m_only 0.100. The headline P1 McNemar
  therefore compares two detectors operating at different false-alarm rates, in the
  direction that makes TRM-3's "net gain ≥ 3" *harder*. Conservative, not invalid, but it
  must be stated when the P1 result is reported, and a FAR-matched comparison would be a
  fairer secondary.
* At n_cal = 100 (the C1 column) the two equalize at 0.0990, so the h384 cell is fair.
* Related exactness note (already in the module docstring): with 6 discordant pairs all in
  one direction the exact two-sided binomial gives p = 0.03125, with 5 it gives 0.0625.
  So P1's "net ≥ 3 **and** p < 0.05" is really "≥ 6 all-one-way discordant pairs". Verified
  on toy vectors.

---

## 6. MAJOR — `alpha` has no effect on the three-channel decision; S3 cannot be produced

`fuse()` returns `min(1, alpha * min_c p_c/alpha_c)` and `_alarm_state` compares against
`config.alpha`. For the three-channel variants `config_for_variant` keeps the frozen
`FROZEN_ALPHAS` (0.02/0.04/0.04) regardless of the `alpha` override, so `alpha` cancels:

```
alpha 0.02 -> p_fused 0.01852 -> CONFIRMED
alpha 0.05 -> p_fused 0.04630 -> CONFIRMED
alpha 0.10 -> p_fused 0.09259 -> CONFIRMED
alpha 0.25 -> p_fused 0.23148 -> CONFIRMED
```

The CONFIRMED **and** PROVISIONAL sets of `trm3` / `no_temporal` / `no_temporal2` are
identical for every α. (Single- and two-channel variants do respond to α, because their
per-channel shares are `alpha / k`.) Empirically, `trm3` at "α = 0.05" gives fused FAR
0.084 vs 0.0855 at α = 0.10 — the same decision, noise apart.

Prereg §7 secondary **S3** asks for a per-channel LR–α curve over α ∈ {0.02, 0.05, 0.10,
0.25}. There is also **no `--alpha` flag on the runner at all**. The trap: adding one after
the freeze would silently do nothing for the headline detector while appearing to work for
the ablations. Decide now what S3 means — most likely "sweep each channel's own α_c" — and
give it an explicit code path.

---

## 7. Verified good — anytime validity of the amendment-2 reference, and no leakage

### 7.1 Anytime validity under exchangeability (task 1)

Synthetic routine-like episodes (random length 40–384, three channels at w = 8/8/4) pushed
through the real `calibrate()` and `online()`; 100 independent calibration draws × 20 fresh
targets = 2000 episodes per row.

| variant | stream | n_cal | α | fused FAR | per-channel FAR (α_c) |
|---|---|---|---|---|---|
| trm3 | i.i.d. | 80 | 0.10 | **0.082** | S 0.011 (0.02), M 0.035 (0.04), J 0.041 (0.04) |
| trm3 | i.i.d. | 100 | 0.10 | **0.097** | S 0.024, M 0.044, J 0.035 |
| trm3 | positional drift | 80 | 0.10 | **0.086** | S 0.014, M 0.042, J 0.033 |
| trm3 | positional drift | 100 | 0.10 | **0.106** | S 0.025, M 0.050, J 0.038 |
| trm3 | AR(1)+level | 80 | 0.10 | **0.099** | S 0.015, M 0.042, J 0.047 |
| trm3 | AR(1)+level | 100 | 0.10 | **0.119** | S 0.026, M 0.052, J 0.049 |
| m_only | i.i.d. | 80 | 0.10 | **0.095** | M 0.095 |
| m_only | i.i.d. | 80 | 0.05 | **0.054** | M 0.054 |
| m_only | i.i.d. | 100 | 0.10 | **0.111** | M 0.111 |
| m_only | i.i.d. | 100 | 0.05 | **0.056** | M 0.056 |

Every single-channel rate sits at or just below its own α_c, and the fused rate sits at or
below 0.10 except in the AR/n=100 rows, which are the in-sample-µ/σ effect of §4 (the
higher-power 30,000-episode runs isolate it: A 0.1080 vs control B 0.0999). **The fixed
full-path-maximum reference itself is sound** — amendment 2 did what it claimed, and the
"ever p ≤ α ≡ full-path max ≥ order statistic" equivalence is confirmed independently by
`verify_m_only_vs_frozen.py` (harness replica: 0 mismatches vs the frozen alarm set;
TRM-3 rule on token buckets: 0 mismatches).

### 7.2 Leakage (task 2)

Traced from `run_trm3.main()` → `run_column()` → `run_variant()` → `calibrate()` /
`online()`, and verified by construction on a synthetic 80-trace pool:

* **No pooled trace is ever scored against its own half**: 0 / 80. `select_half` returns
  `1 - group_half[group]` for a pooled scenario; halves come from `harness.scenario_halves`
  on `pair_group_id`, so the clean/benign/attack arms of one scenario always move together.
* **Perturbing one calibration trace changes only its own half's reference**; the half that
  scores it is bit-identical before and after (`np.array_equal` → True).
* **Bucket µ/σ are fitted only on the scoring half's members** (`calibrate` builds `raw`
  from `members` of that half alone).
* **No future token, anchor, arm or label enters the online path.** A token scan of
  `calibrate`, `online`, `channel_p_values`, `fit_bucket_stats_k`, `fit_channels` finds no
  `labels`, `anchor`, `engagement`, `onset` or `behaviour_class`; the only `positive` read
  is the guard that *rejects* a positive from the calibration pool. `behaviour_class`,
  `anchor_hits` and the anchors live only in `evaluate` (offline).
* **Windows are causal**: `features.window_means` emits `[end-w+1, end]` with the first
  endpoint at `w-1`; `unseen_only` uses `window_maxima` over the same causal window.
* Per-channel `k` is each channel's own ordinal, so channel J (w = 4) contributes its
  running max over ends 3…6 to the first fused endpoint at end 7. That is causal and
  matches documented decision 2.
* `fit_regime_axis` sees `routine_fit` only, and `regime_flag` never enters the score or
  the state.

### 7.3 Position buckets and the tail rule (task 3)

`BucketStatsK.buckets(k) = min(k // 32, cap)`. `cap` is lowered until the tail bucket holds
≥ 30 contributing traces; a non-tail bucket with < 30 traces reuses the bucket below. On a
synthetic pool with cap = 3, `k ∈ [0, 32, 64, 96, 200, 1000] → [0, 1, 2, 3, 3, 3]` and every
`z` past `k = 96` uses the tail µ/σ — no dead zone, matching prereg §2 as amended. Real
runs: b2/D cap 2 with trace counts [80, 69, 46] and no reused buckets; C1 cap 3 with
[100, 84, 50, 37]. The `std(ddof=1) + variance_floor` form matches
`harness.fit_bucket_stats` exactly.

The caveat is §2 above: the *tail bucket* is well defined for arbitrarily large `k`, but the
*reference set* is not — the µ/σ extrapolate, the path-length distribution does not.

### 7.4 State machine (task 5)

Verified on 10 synthetic p-streams, D = 32:

| case | result |
|---|---|
| never provisional | all `NONE`, `e0 = None` |
| e0 then all below | `SUSTAINED` first reported at position `e0 + D - 1` |
| exactly 16 of 32 below (50 %) | `SUSTAINED` (">= 50 %" honoured at the boundary) |
| e0 then D consecutive above | `RECOVERING` exactly at position `e0 + D` |
| re-entry after RECOVERING | new `e0`, `episode_index` 2, `SUSTAINED` reachable again |
| dip below without reaching RECOVERING | `e0` **not** reset (correct per §2) |
| episode ends inside `[e0, e0+D)` | `UNCERTAIN`, `censored = True` |
| SUSTAINED → RECOVERING → SUSTAINED | all three reported, `episode_index` 2 |

Boundaries: `p = 0.10 → CONFIRMED`, `p = 0.10001 → PROVISIONAL`, `p = 0.25 → PROVISIONAL`,
`p = 0.2500001 → SILENT`. **PROVISIONAL never counts as a false alarm**: `summarize_trace`
sets `alarm_ends` from `STATE_CONFIRMED` only, and every FAR in `evaluate` reads
`summary.alarm`. Baseline rules report `temporal_state = DISABLED` throughout.

### 7.5 `evaluate()` (task 6)

Toy-vector checks, anchor = 30:

| trace | strict | tolerant (band 5) |
|---|---|---|
| first alarm at 30 | hit, latency 0, `hit_plus_8` | hit |
| first alarm at 20 | `pre_onset_alarm`, **no** hit at any horizon | no hit |
| first alarm at 50 | hit, latency 20, `hit_plus_8` False, `hit_plus_32` True | same |
| no alarm | no hit | no hit |
| first alarm at 25 (= anchor − 5) | **miss** (pre-onset) | **hit**, latency 0 |

Matched-group FAR alarms a scenario when any of its *normal* members alarms; the
`spontaneous_drift` keys are removed from `clean`, `benign`, `pooled`, `matched_group`,
`far_by_calibration_half` and every `worst_group` denominator (verified: pooled_count 4 of
5 traces) and reported separately with an any-alarm count only. `paired_mcnemar` is the
exact two-sided binomial on discordant pairs (6–0 → p = 0.03125; 2–1 → p = 1.0) and reports
`unpaired_keys` rather than silently intersecting. Length tertiles are real
(`token_count` is a genuine `LoadedTrace` property; smoke shows short/medium/long
FAR 0.038 / 0.094 / 0.111).

Two deviations in this function are listed as §1 (blocking) and §8.13 (minor).

---

## 8. Minor findings

1. **The calibration stream cache never hits.** `run_variant` passes
   `{trace_key(t): streams}` but `trm3.calibrate` looks up `cached[trace.trace_id]`
   (`src/research_v2/trm3.py:686-687`). The calibration pool is therefore re-scored from
   scratch for every variant, and the dict `calibrate` builds is keyed on `trace_id` alone —
   a cross-batch collision hazard, since **all 240 h384 trace ids are identical to B2's**
   (verified against the two `sample_index.jsonl` files). No current CLI path mixes b2 and
   h384 in one calibration pool, so this is latent, but it is exactly what amendment 7 was
   meant to prevent. `harness._JSON_SHAPED_CACHE` is a module-level dict with the same
   `trace_id` key and the same hazard.
2. **B-NT's Bonferroni is `K_MAX = 384` for every episode**, not "每 episode 的 look 数"
   (prereg §6). Median routine episodes have ~70 looks, so the correction is 3–6× too
   strong, which biases the S2 comparison in TRM-3's favour. Documented as the deliberate
   "lower bracket" with B-NT2 as the upper, but it is not what §6 says.
3. **G7 is not wired when the target *is* `c1_heldout`.** `assemble_gates` reads
   `cell["sets"]["c1_heldout"]`, which only exists when c1_heldout is a *side* evaluation
   set. The natural cell (`--target c1_heldout`) reports G7 `not_evaluated` even though its
   `target` block carries the right matched-group number (0.283).
4. **`--gate-reference` leaves `gates["summary"]` stale.** It overwrites `gate_block["G5"]`
   without recomputing the evaluated/passed/failed summary; `apply_g5` does recompute it.
5. **`core_anchors` fails silently.** Both label files are guarded by `.exists()`; if either
   is missing or renamed the anchors dict is empty, the positive set is empty, recall is 0
   everywhere and McNemar returns p = 1 with no error. Add an assertion on the expected
   positive count (59 drift + the anchored resisters) once §1 is fixed.
6. **The two h384 anchor columns have different denominators.** `load_h384` sets
   `execution_onset = None` unless `positive`, so the secondary anchor exists only for the
   40 executions while the primary covers executions **and** bounded resisters (45).
   `tables.md` prints the two recall rows side by side without flagging that.
7. **No interlock enforces the pre-freeze data-discipline rule.** `--routine-only-smoke` is
   opt-in; a plain `run_trm3.py --target b2` loads the drift arm, scores it and writes
   recall blocks to `result.json` / `outputs.jsonl`. The `if smoke: continue` at the end of
   `main()` is dead code (nothing follows it). Consider a hard guard: refuse non-smoke runs
   unless the prereg file's sha256 matches a recorded freeze value, or unless an explicit
   `--post-freeze` flag is given.
8. **Provenance is misleading.** `harness.code_commit()` records `git rev-parse HEAD` with
   no dirty/untracked marker. Right now `src/research_v2/trm3.py`, all four scorers,
   `scripts/research_v3/`, and all the v3 tests are **untracked**, and `src/research_v2/io.py`
   is modified — so every existing `result.json` names commit `b06c5333…`, which contains
   none of the code that produced it. Add `git status --porcelain` (or a tree hash) to
   `result.json` before the freeze.
9. **`anchor_hits.reachable_plus_{h}` ignores the horizon**: it checks
   `last_end >= anchor`, not `>= anchor + h`, so it overstates reachability for +32 / +64 on
   short episodes.
10. **Prereg §2's per-token `top-3 contributing coordinates` are off by default.** They are
    emitted only under `--emit-evidence`, and then only for non-SILENT endpoints.
11. **Prereg §3's reference-store description is stale** (running-max matrix
    `[n_cal × K_max]`, ~61k floats). After amendment 2 it is `n_cal` floats per
    channel-half; `result.json`'s `cost.reference_floats` already reports the true number.
    Doc-only.
12. **`Calibration.select_half` has a silent sha256 fallback** for a scenario that is
    neither in the calibration pool nor registered as external. `run_variant` always calls
    `register_external`, so it never fires today; a direct `run_trace` caller would get a
    silent hash-based half instead of an error.
13. **`regime_stream` is recomputed per variant.** `selection_rate_windows` runs once per
    (trace, variant) — 11× redundant across a full variant sweep. Performance only.

---

## 9. Confirmed non-issues

* The **k-bucket vs token-bucket** divergence (prereg `b(k) = k // 32` vs the frozen
  harness's `end // 32`) is real, is the *only* measured divergence from the frozen mode-D
  run, and is correctly escalated by the fixer as a lead decision.
  `verify_m_only_vs_frozen.py` is a sound piece of work: the harness replica reproduces the
  frozen alarm set exactly (0 / 160), and the TRM-3 rule on token buckets also reproduces it
  exactly, which isolates the bucket index as the sole cause of the 8 mismatches.
* The `>=` vs `p ≤ α` tie asymmetry is correctly documented and is measure-zero on disjoint
  halves.
* `unseen_only`'s degeneracy is correctly detected (`unseen_mask.sum() == 0`), flagged in
  `result.json` and `tables.md`, and its FAR of 0 is still reported.
* `fuse` implements the Bonferroni `min` correctly: `p_fused ≤ α ⟺ ∃c: p_c ≤ α_c`.
* `attribute` breaks ties on the frozen channel order, deterministically.
* G8 resolves the **frozen** CAND-A reference in all four smoke runs
  (`S1:b1_to_b2|w8|routine=cb|mode=D|alpha=0.1|reading=max`, 1.863 onsets/1000, denominator
  18783), with the denominator caveat recorded in `result.json` and in every G8 block.
* The `spontaneous_drift` labelled exclusion is re-derived from loader labels and
  cross-checked against the four frozen ids with a hard `SystemExit` on mismatch.
* `routine_only_metrics` raises on any non-routine trace, and `drop_attack_arm` filters at
  load time — the smoke path is genuinely attack-free.

---

## 10. Recommended pre-freeze order of work

1. Fix §1 (positive set) — it is a one-line change plus a positive-count assertion, and
   nothing downstream is trustworthy without it.
2. Decide §3 (C1 column fit/calibration overlap). Any change here is an algorithm change and
   must land **before** the freeze commit. If the answer is "keep it and accept the gate
   failures", record that in prereg §11 with the routine-only numbers already measured.
3. Decide §2 (h384 length vs C1 calibration length) — restrict, extend, or declare G6
   descriptive for the C1 column.
4. Give §6 (α sweep for S3) an explicit code path, or restate S3 as a per-channel-α sweep.
5. Soften prereg §2's "精确 ≤ α_c" to account for §4, and record the §5 effective-α table so
   the P1 comparison is reported at its true (unequal) budgets.
6. Minor items §8.1–8.8 are cheap and worth doing; §8.9–8.13 can wait.

---

# v1.2 re-review (2026-09-06)

Re-reviewer: Opus 5 research engineer, re-verify-only mandate. Nothing was fixed and no
file other than this one was written. Every number below comes from (a) synthetic episodes
pushed through the real `trm3.calibrate()` / `trm3.online()` / `trm3.evaluate()`, (b) the
routine-only smoke artifacts on disk, or (c) label-file **row counts** and pool index
metadata. **No attack or drift metric was computed, printed or written.**
Test suite: `268 passed, 55 subtests passed`.

## 11. Verdict

**Ok to freeze on the code.** The blocking defect of §1 is fixed and verified, the three
validity repairs (amendments 3, 4, 6) do what they claim, and the six re-verification tasks
all pass. What is left is not a code defect but four things the lead must *decide and
record before* the freeze commit, because changing any of them afterwards is a new
proposal: 11.1 (residual long-episode inflation and the prereg §2 wording), 11.2 (the C1
column's gates are already failing on routine data), 11.3 (the S3 alpha grid is degenerate
at alpha = 0.02), 11.4 (n = 90 widens rather than closes the TRM-3 / B-M budget gap).

## 11.0 The six re-verification tasks

| task | verdict |
|---|---|
| 1. positive set on B1/B2 (+14 anchored resisters) and h384 (+5 bounded) | **fixed, verified** |
| 2. anytime validity after amendments 3 + 4 | **in-sample bias gone; long-target 0.28 gone at the pooled level, residual conditional excess remains (11.1)** |
| 3. `alpha_eff` / `alpha_matched` arithmetic at n = 80 and n = 90 | **exact, verified** (11.4 is about what the numbers mean, not about the arithmetic) |
| 4. data-discipline guard | **refuses a dirty tree, a missing, a wrong and an unknown `--freeze-commit`** |
| 5. C1 fold roles disjoint | **disjoint in code and in the frozen index, at trace and pair-group level** |
| 6. no anchor read before scoring | **confirmed by AST scan and by code trace** |

### 11.0.1 Positive set (task 1)

Synthetic B1/B2-shaped pool (labels `{}`, as `io.load_batch` leaves them): 59 drift + 61
resisters of which 14 anchored + 80 controls →

```
positive_set: count 73, drift_count 59, anchored_resist_count 14,
              anchor_available_count 73, attack_arm_count 120
```

Synthetic h384-shaped pool (40 execution / 5 bounded / 35 silent + 80 controls, anchors as
`h384_anchors()` builds them) → `count 45, drift_count 40, anchored_resist_count 5`, with
all 5 bounded resisters present and 0 of the 35 silent resisters. The v1.1 behaviour (73 →
59, 45 → 45) is gone.

Runner interlocks, exercised on **copies** of the frozen label files (the originals were
never touched):

| tamper | result |
|---|---|
| untouched copies | passes |
| product-onset file missing / topic-entry file missing | `SystemExit: missing frozen label file` |
| one drift row deleted (58) | `SystemExit: frozen label-file count mismatch` |
| one topic row deleted (60) | `SystemExit` |
| one anchored resister nulled (13) | `SystemExit` |
| one extra anchor added (15) | `SystemExit` |
| h384 class counts 40/4/36 or 39/5/35 | `SystemExit: h384 engagement-class counts ...` |

Frozen files as they stand: `product_onset_v1_adjudicated.jsonl` 59 rows / 59 anchored,
`topic_entry_v1_adjudicated.jsonl` 61 rows / 14 anchored, engagement adjudications 40 / 5 /
35 with an `engagement_evidence` string on all 45 engaged rows. Two residual gaps are minor
(11.7, 11.8).

### 11.0.2 Anytime validity (task 2)

2000 episodes per row (100 independent calibration draws × 20 fresh targets) through the
real `calibrate()` / `online()`, bucket mu/sigma from an independent 80-trace fit pool,
lengths 40–384 for calibration *and* target, calibrated-horizon rule active.

| variant | stream | n_cal/half | alpha | alpha_eff | fused FAR ± SE | per-channel FAR | censored endpoints |
|---|---|---|---|---|---|---|---|
| trm3 | i.i.d. | 80 | 0.10 | 0.0864 | 0.0885 ± 0.0064 | S 0.017 / M 0.041 / J 0.035 | 0.0000 |
| trm3 | AR(1)+level | 80 | 0.10 | 0.0864 | 0.0740 ± 0.0059 | S 0.009 / M 0.035 / J 0.033 | 0.0000 |
| trm3 | i.i.d. | 90 | 0.10 | 0.0769 | 0.0730 ± 0.0058 | S 0.014 / M 0.031 / J 0.032 | 0.0001 |
| trm3 | AR(1)+level | 90 | 0.10 | 0.0769 | 0.0760 ± 0.0059 | S 0.010 / M 0.036 / J 0.033 | 0.0001 |
| trm3 | i.i.d. | 80 | 0.05 | 0.0247 | 0.0270 ± 0.0036 | **S 0.000** / M 0.016 / J 0.011 | 0.0000 |
| trm3 | AR(1)+level | 80 | 0.05 | 0.0247 | 0.0215 ± 0.0032 | **S 0.000** / M 0.012 / J 0.010 | 0.0000 |
| trm3 | i.i.d. | 90 | 0.05 | 0.0220 | 0.0210 ± 0.0032 | **S 0.000** / M 0.012 / J 0.009 | 0.0001 |
| trm3 | AR(1)+level | 90 | 0.05 | 0.0220 | 0.0240 ± 0.0034 | **S 0.000** / M 0.014 / J 0.010 | 0.0001 |
| m_only | i.i.d. | 80 | 0.10 | 0.0988 | 0.0900 ± 0.0064 | M 0.090 | 0.0001 |
| m_only | AR(1)+level | 80 | 0.10 | 0.0988 | 0.1040 ± 0.0068 | M 0.104 | 0.0001 |
| m_only | i.i.d. | 90 | 0.10 | 0.0989 | 0.1015 ± 0.0068 | M 0.102 | 0.0000 |
| m_only | AR(1)+level | 90 | 0.10 | 0.0989 | 0.0920 ± 0.0065 | M 0.092 | 0.0000 |
| m_only | i.i.d. | 80 | 0.05 | 0.0494 | 0.0510 ± 0.0049 | M 0.051 | 0.0001 |
| m_only | AR(1)+level | 90 | 0.05 | 0.0440 | 0.0425 ± 0.0045 | M 0.043 | 0.0000 |

Every row sits at or below its own `alpha_eff` within ~1 SE. The v1.1 AR(1) rows that ran
hot (trm3 0.099 at n = 80, 0.119 at n = 100; §7.1 of the first review) are gone.

**The §4 in-sample-standardization excess is removed.** Paired high-power run (10,000
episodes each, identical streams, only `bucket_source` switched; m_only, alpha 0.10,
alpha_eff 0.0988):

| stream | A = mu/sigma from the calibration half (v1.1) | B = mu/sigma from the fit pool (v1.2) | A − B |
|---|---|---|---|
| i.i.d. | 0.0989 ± 0.0030 | 0.0966 ± 0.0030 | +0.0023 |
| AR(1)+level | **0.1028 ± 0.0030** | **0.0950 ± 0.0029** | **+0.0078** |

The v1.2 setting is now *below* its attainable budget in both models; the +0.009 of the
first review reproduces only in the A arm. Real-run corroboration: with the fit-pool source
both calibration halves receive identical bucket statistics (`v12_*` calibration blocks),
which also removes the half-to-half cap asymmetry of v1.1 (b2/D was cap 2 vs 3).

**The 0.28 long-target inflation is gone at the pooled level** — see 11.1 for the residual.

### 11.0.3 alpha bookkeeping (task 3)

`attainable_alpha(n, a) = floor((n+1)a)/(n+1)`, verified exactly (the `1e-12` guard makes
`floor(81 · 7/81) = 7` and `floor(91 · 7/91) = 7` come out right):

| n | alpha | S rank | M rank | J rank | alpha_eff | B-M rank / attainable | alpha_matched |
|---|---|---|---|---|---|---|---|
| 80 | 0.10 | 1 | 3 | 3 | **7/81 = 0.086420** | 8 / 0.098765 | 7/81 = 0.086420 |
| 90 | 0.10 | 1 | 3 | 3 | **7/91 = 0.076923** | 9 / 0.098901 | 7/91 = 0.076923 |
| 80 | 0.25 | 4 | 8 | 8 | 20/81 = 0.246914 | 20 / 0.246914 | 0.246914 |
| 90 | 0.25 | 4 | 9 | 9 | 22/91 = 0.241758 | 22 / 0.241758 | 0.241758 |
| 80 | 0.05 | **0** | 1 | 1 | 2/81 = 0.024691 | 4 / 0.049383 | 0.024691 |
| 90 | 0.05 | **0** | 1 | 1 | 2/91 = 0.021978 | 4 / 0.043956 | 0.021978 |
| 80 | 0.02 | **0** | **0** | **0** | **0.000000** | 1 / 0.012346 | 0.000000 |
| 90 | 0.02 | **0** | **0** | **0** | **0.000000** | 1 / 0.010989 | 0.000000 |

Two-channel ablations: n = 80 → 2 × 4/81 = 8/81 = 0.098765 (as the fixer reports);
n = 90 → 2 × 4/91 = 8/91 = 0.087912. The rank-0 rows are 11.3.

### 11.0.4 Data-discipline guard (task 4)

Against the real (dirty) tree, `run_trm3.py --target b2 --calibration D --variant m_only`
without `--routine-only-smoke` exits with
`data-discipline guard: the working tree is not clean ...` and lists the untracked entries;
**no output directory is created and no pool is loaded** (the guard is called before the
first `load_pool`). The `artifacts` symlink line is correctly filtered out of the dirty
set. With the tree stubbed clean, each branch fires as written:

| case | result |
|---|---|
| no `--freeze-commit` | refused (`--freeze-commit is required`) |
| `--freeze-commit HEAD~1` | refused (`HEAD (…) is not the freeze commit (…)`) |
| `--freeze-commit deadbeef…` | refused (same branch: `git rev-parse` echoes the unresolvable name, so the mismatch check catches it rather than the `unknown --freeze-commit` branch) |
| `--freeze-commit HEAD` | allowed, `dirty=False`, `head_is_freeze_commit=True` |
| `--routine-only-smoke` on a dirty tree | allowed, and the block records `dirty=True` |

`result.json` now carries `dirty`, `dirty_entries`, `head`, `freeze_commit_resolved`,
`prereg_sha256` and `code_commit`, which closes first-review §8.7 and §8.8.

### 11.0.5 C1 fold roles (task 5)

From the frozen `normal_calibration_c1/sample_index.jsonl` (metadata only): fold 0 = 80
traces / 40 groups, folds 1–3 = 180 / 90, fold 4 = 60 / 30. Pairwise intersections are
**empty at both the trace-id and the pair-group level** (0 / 0 / 0), so no scenario leaks
between fit, calibration and held-out. `run_column` additionally asserts 80 / 180 and an
explicit key-overlap check, and `assert_c1_heldout` asserts 60 / 30; the run artifacts show
the calibration halves at 90 / 90. Note 11.9 on the frozen `fold_role` of fold 3.

### 11.0.6 Leakage (task 6)

AST scan of `run_trm3.run_variant` / `run_column` / `main` / `ChannelCache`: inside
`run_variant` the token `anchor` appears only in the signature and in the `evaluate()` /
`alpha_sweep()` calls, both of which run **after** the whole scoring loop; the scoring loop
(`select_half` → `cache.streams` → `trm3.online`) reads no label, anchor or arm. The same
scan over `trm3.fit_channels` / `channel_streams` / `calibrate` / `online` /
`channel_p_values` / `fit_bucket_stats_k` / `fit_regime_axis` finds exactly two
label-adjacent reads, both in `calibrate`, and both are the guards that *reject* a positive
from the calibration pool and from the bucket fit pool. In smoke runs `anchors` is `{}` and
`alpha_sweep` is passed `anchors=None`. First-review §7.2 therefore still holds under v1.2.

## 11.1 MAJOR — the K_cal rule fixes the pooled rate, not the conditional one

Amendment 4 removes the unbounded part of the length-mismatch problem, but the conformal
bound needs *length exchangeability* between target and calibration paths, and truncating
the target at `K_cal` only guarantees it does not take more looks than the **longest**
calibration path — not than the typical one. Synthetic, real code, 2000 episodes per row,
alpha 0.10, n_cal 80/half:

| calibration lengths | target lengths | variant | FAR | alpha_eff | censored endpoints |
|---|---|---|---|---|---|
| 40–192 | 40–192 (control) | m_only | 0.106 | 0.0988 | 0.000 |
| 40–192 | **300–384** | m_only | **0.164 ± 0.008** | 0.0988 | 0.451 |
| 40–192 | 40–192 (control) | trm3 | 0.082 | 0.0864 | 0.000 |
| 40–192 | **300–384** | trm3 | **0.145 ± 0.008** | 0.0864 | 0.450 |

So the first review's 0.277 → 0.164 (m_only) and 0.9997 (positional drift) → bounded: the
catastrophic regime is gone, an ~1.6× conditional excess is not.

With the **empirical** length distributions of the two real pools (endpoint counts read off
the routine-only smoke outputs: C1 fold-4 min 21 / median 82.5 / max 185; h384 min 10 /
median 67.5 / max 377), n_cal 90/half:

| cell | variant | stream | FAR | alpha_eff | censored traces | FAR on censored traces | FAR on uncensored |
|---|---|---|---|---|---|---|---|
| C1 cal → h384 target | trm3 | AR(1) | 0.067 | 0.0769 | 0.146 | **0.127** | 0.057 |
| C1 cal → C1 target | trm3 | AR(1) | 0.087 | 0.0769 | 0.000 | — | 0.087 |
| C1 cal → h384 target | m_only | AR(1) | 0.092 | 0.0989 | 0.135 | **0.204** | 0.074 |
| C1 cal → C1 target | m_only | AR(1) | 0.095 | 0.0989 | 0.000 | — | 0.095 |

The **pooled** h384 × C1 rate is inside budget — G6 is now a real pass, not an artefact of
the denominator — but the long-episode subgroup still runs at roughly twice the budget.
Real routine data agrees (`v12_h384_C1_smoke`, trm3, clean + benign_control, split at the
185-endpoint ceiling):

| run | FAR ≤185 endpoints (n=138) | FAR >185 endpoints (n=22) | pooled |
|---|---|---|---|
| v1.1 (`h384_C1_smoke`) | 0.145 | 0.182 | 0.150 |
| v1.2 (`v12_h384_C1_smoke`) | **0.072** | **0.136** | **0.081** |

(n = 22, SE ≈ 0.073, so the remaining gap is not individually significant; it is the sign
and size the synthetic predicts.)

What this costs: prereg §2's "保形保证只在 k ≤ K_cal 内成立" is still a slightly stronger
claim than what holds — inside `k ≤ K_cal` the guarantee is *marginal over the target
length distribution*, and it is anti-conservative conditional on the long tail. One
sentence in §2 (and a per-length-tertile FAR row in the h384 × C1 report, which §8 already
asks for) is enough. No code change, no algorithm change.

## 11.2 MAJOR — the C1 preset column still fails preregistered gates, on routine data alone

v1.2 improved every C1 number (G7 matched-group 0.283 → 0.167, pooled FAR 0.167 → 0.083),
but the gates as preregistered are still failed, and this is fully determined by
routine-only data:

| cell (trm3 variant) | G1 [0.07, 0.13] | G2 ≤ 0.10 | G3 ≤ 0.10 | G6 ≤ 0.15 | G7 ≤ 0.15 |
|---|---|---|---|---|---|
| b2 / D | 0.075 pass | 0.075 pass | 0.025 pass | — | — |
| b2 / C1 | 0.081 pass | **0.113 FAIL** | 0.038 pass | — | **0.167 FAIL** |
| c1_heldout / C1 | 0.083 pass | **0.167 FAIL** | 0.100 pass (at the edge) | — | **0.167 FAIL** |
| h384 / C1 | **0.064 FAIL (below the band)** | 0.080 pass | 0.003 pass | 0.064 pass | **0.167 FAIL** |

(`m_only` fails G1 on two C1 cells and G3 on two.) Per prereg §7 "任一失败即不可出厂" the
C1 preset-threshold column is **not shippable**, exactly as the first review said, only with
better numbers. The lead's choices are unchanged and all pre-freeze: record the failure in
the prereg now, restate the C1 column as descriptive, or change the C1 design. G7 is one
group away from passing (5 of 30 groups; 4/30 = 0.133).

## 11.3 MAJOR — the preregistered S3 alpha grid is degenerate at alpha = 0.02

Amendment 6 fixed the first review's §6 (alpha now moves the three-channel decision:
synthetic fused FAR 0.089 at 0.10 vs 0.027 at 0.05; the real `v12_b2_D_alphagrid` cell
shows 0.000 / 0.019 / 0.075 / 0.188 across the grid). But the fix makes the discreteness
bite at the small end:

* **alpha = 0.02 → alpha_eff = 0 for trm3 at both n = 80 and n = 90.** `floor(81 · 0.004) =
  floor(81 · 0.008) = 0`: no channel can produce a p-value small enough to fire. The real
  routine cell reports FAR **exactly 0.0000** for trm3 while m_only reports 0.0125 — the
  zero is structural, not a calibration property.
* **alpha = 0.05 → channel S is dead** (rank 0) and the fused budget is 2/81 = 0.0247, i.e.
  half the nominal. Confirmed in the simulation: S's own FAR is exactly 0.000 in all four
  alpha = 0.05 rows.
* The `alpha_grid` blocks in `result.json` carry `alpha`, `far` and
  `far_by_calibration_half` only — no `alpha_eff`, no rank — so an S3 reader has nothing in
  the artifact telling them the 0.02 row is vacuous.

S3 is a preregistered secondary, so changing its grid (or its meaning — "sweep each
channel's own alpha_c", or restrict to alpha ≥ 0.10 where all three ranks are ≥ 1) is an
algorithm decision that has to happen **before** the freeze. The cheapest fix is
documentary: keep the grid and add the attainable-alpha row to every sweep block.

## 11.4 MINOR — n = 90 widens the TRM-3 / B-M budget gap; prereg §12 item 5's parenthetical is wrong

The first review measured the P1 handicap at n = 80 (0.0864 vs 0.0988, 13%) and noted it
would disappear at n = 100. v1.2's C1 calibration pool gives halves of **90**, not 100:

| n/half | TRM-3 alpha_eff | B-M attainable at 0.10 | handicap |
|---|---|---|---|
| 80 (mode D) | 0.086420 | 0.098765 | 12.5% |
| 90 (C1 column) | **0.076923** | 0.098901 | **22.2%** |
| 99+ | 0.098765… | 0.098765… | 0 |

Prereg §12 item 5 says "C1 列半份 90 使 α_S 可达（rank ≤ 1）". α_S = 0.02 is attainable at
rank 1 at n = 80 as well, and at n = 90 its attainable value is *lower* (1/91 = 0.0110 vs
1/81 = 0.0123); the first n at which α_S reaches rank 2 is **n = 99**. The sentence should
either be deleted or corrected — the matched-alpha machinery handles the consequence
correctly (`alpha_matched` = 7/91 in the C1 column), so this is wording, not code.

## 11.5 MINOR — G1's lower edge now collides with the design's own effective budget

G1 is a two-sided band [0.07, 0.13] written against a nominal alpha of 0.10. In the C1
column TRM-3's attainable budget is 0.0769, so a *perfectly calibrated* detector sits about
0.007 above the band floor; on 60 held-out traces the binomial SE at p = 0.077 is 0.034.
The h384 × C1 cell already fails G1 from below at 0.0641 while being demonstrably
conservative. The band is doing the opposite of what it was written for on that cell. A
pre-freeze note ("G1's floor is read against alpha_eff, not against the nominal alpha")
would prevent a spurious No-go.

## 11.6 MINOR — the horizon uses the pool-wide K_cal, not the scoring half's

`calibrate()` computes `k_cal[channel]` as the longest path over the **whole** calibration
pool and both halves get the same value, but a target is scored against **one** half's
reference set. If half 0's longest path were 120 and half 1's 185, a 180-endpoint target
scored against half 0 would take more looks than any path in its own reference and would
not be censored. Harmless today — in all three real pools both halves report
`longest_calibration_path` 185 (S/M) and 189 (J), a ceiling effect of the 192-token cap —
but the correct horizon is `reference.channels[c].k_max` of the scoring half, and the
current form is a silent hole if a future pool's halves are unbalanced.

## 11.7 MINOR — a drift trace with no anchor row is dropped from the positive set silently

The positive rule requires `anchors.get(key) is not None`, so a drift trace missing from
`product_onset_v1_adjudicated.jsonl` disappears from P1 with no error. Verified
synthetically: adding a 60th drift trace with no anchor row leaves `count 73`,
`drift_count 59` and moves only `attack_arm_count` (120 → 121). Today the file covers all
59 drift traces, so nothing is lost; `positive_set` does expose the discrepancy
(`drift_count` vs `attack_arm_count`), but there is no assertion of the kind amendment 1
added for the row counts.

## 11.8 MINOR — `assert_h384_label_counts` checks classes, not anchor availability

The 40 / 5 / 35 assertion would still pass if a bounded resister's `engagement_onset` came
back `None` (evidence string not matched in the decode tokens), which would quietly shrink
the h384 positive set from 45 to 44. All 45 engaged adjudications do carry an
`engagement_evidence` string today, and `io.load_h384` raises on any disagreement with the
frozen per-trace span, so the path is closed from the loader side; the runner's own
interlock is one assertion short of amendment 1's intent.

## 11.9 Notes, not defects

1. **Prereg §7 still says G7 = "C1 folds 3–4"** while the code (and §12 item 2) uses fold 4
   only, since fold 3 is now calibration. The fixer flagged this; it should be aligned
   before the freeze. Related: the frozen C1 index labels fold 3 `held_out_normal_evaluation`
   and v1.2 uses it for calibration — deliberate and disjointness-safe, but the loader's
   `fold_role` label and the runner's role now disagree, which is worth one line in §4.
2. **`alpha_provisional` stays 0.25 while `alpha` moves**, so at alpha = 0.25 the CONFIRMED
   and PROVISIONAL bands coincide and the temporal machine degenerates in that sweep cell.
   S3 only reads FAR/recall, so nothing is computed wrong; do not read temporal states off
   an alpha = 0.25 sweep.
3. **The fit-pool bucket cap is coarser in the C1 column** (cap 2, tail bucket from k > 96)
   than v1.1's calibration-pool cap 3, because N_fit = fold 0 is 80 short traces. Both the
   calibration and the target pass through the same transform, so validity is untouched;
   only power is affected.
4. **`verify_m_only_vs_frozen.py` corroborates the fixer's attribution.** The artifact
   shows harness replica 0/160 mismatches, `trm3_rule_on_token_buckets` 0 differences,
   `..._fit_pool_source` 5 alarm-set differences, `bucket_index_difference_only` 5 — the two
   5-trace effects cancel and `m_only` reproduces the frozen 17/160 alarm set with 8
   first-alarm shifts, exactly as reported. FAR 0.10625 in every row but the fit-pool one.
5. First-review minors **8.11 / 8.12 / 8.13** were deliberately out of scope for the fixer
   and remain open; none of them is reachable from the runner.
6. `artifacts/agent_v2/research_v3/trm3/_gateref_check/` is the fixer's scratch cell; it
   demonstrates the amendment-8 `--gate-reference` summary refresh (G5 counted, 6 gates
   evaluated) and can be deleted.

# EXPLORATORY / POST-HOC: do router *weights* buy anything the selection-only statistics discard?

> **STATUS: EXPLORATORY. POST-HOC. DEVELOPMENT DATA.**
> This is **not** a preregistered analysis, **not** a patch of the frozen TRM-3 proposal
> (`docs/research_v3/trm3_prereg.md`), and **not** admissible for any gate, for P1, or for any
> preregistered claim. It was run after the freeze commit on data whose target metrics have
> already been read, with a working tree that necessarily carries new code; every artifact it
> produced records `dirty = true` and the label `EXPLORATORY_POST_HOC`. Every number below is
> a post-hoc observation on B1/B2/h384 development material and carries the usual multiplicity
> problem: seven new statistics x three targets x two calibration columns x two horizons.
> Nothing here changes the frozen proposal; the only legitimate output is a list of things that
> would have to be **preregistered on dataset G** (section 8).

Date: 2026-09-07. Author line: Claude research engineer (Opus 5), for the lead.

---

## 1. The question and why it is not stupid

The prereg deliberately keeps the probability tensor out of the main path ("概率张量不进主路径",
section 2) and admits it only through the routine-only stability gate S6. Channels S and M, and
baselines B-U / B-S, all read `top_k_ids` alone: they see **which** experts were selected, never
**how much** the router weighted them. The code-blindspot zoom
(`docs/research_v2/zoom/code_blindspot/lead_synthesis.md` sections 1.3 and 2) found a concrete
failure mode of exactly that blindness: code deliverables leave the routine top-8 *set* almost
unchanged (1 - Jaccard 0.667, *lower* than routine tokens against each other) and instead push
probability mass **further onto the routine's own favourite experts** (layer 11-14 rmass 0.236-0.312
vs routine 0.185, other-domain drift 0.124). A selection-only detector is structurally blind to
that; a weight-carrying one need not be.

The adversarial refutation (`refute_depth_profile.md`) had already priced the same idea and
killed it as a *fixed-threshold cross-batch* statistic: rmass at layers 11-14 gave B1 -> B2
AUC 0.971 / 3-of-5 code clean, but B2 -> B1 AUC 0.825 with resist-arm FAR 0.438-1.000, and at
layers 12-15 FAR 0.550 with resist FAR 1.000. What was *not* tested there is what happens when the
same quantity is run through the **frozen conformal protocol** -- position-bucket standardisation on
`N_fit`, a fixed full-path-maximum reference set per calibration half, and per-episode budget
`alpha = 0.10`. Recalibrating per pool is exactly what a scale that does not transfer needs. That
gap is what this note fills.

## 2. What was implemented

Six scorers, seven registered names, all routine-fit only, all causal, all `w = 8` window means,
all exposing `fit(routine) -> state` with `describe()` and `score(state, trace) -> (scores, ends)`.

| registered scorer | statistic (per decode token `t`, then causal window mean over `w = 8`) | layers |
|---|---|---|
| `prob_rare_mass` | `sum_{(l,e) in Omega_rare} p_t[l,e]` -- the *soft* channel S | all 16 |
| `prob_weighted_surprisal` | `sum_{l,e} p_t[l,e] * (-log q[l,e])` | all 16 |
| `prob_weighted_surprisal_rare` | the same integral masked to `Omega_rare` | all 16 |
| `prob_js` | `sum_l JS(window-mean router distribution_l || routine mean router distribution_l)` | 5-15 |
| `prob_js_all` | the same | all 16 |
| `prob_concentration` | `mean_l sum_{e in routine top-8 of l} p_t[l,e]` (rmass), **sign-inverted** | 11-14 |
| `prob_entropy_drop` | `mean_l ( H_routine[l] - H(p_t[l,:]) )` in nats | all 16 |

* `q[l,e] = (count + 0.5) / (N_tok + 32)` and `Omega_rare = {q < 0.02}` are **channel S's own**
  routine model, reused verbatim from `research_v2.scorers.trm3_s`. Cross-check: on the B1 fit pool
  (80 traces, 9207 decode tokens) `Omega_rare` holds **71 of 1024** coordinates and layers 0-2 are
  empty -- exactly the number recorded in prereg section 11 item 7, so the rare set is provably the
  same object S uses.
* `prob_js` is the only non-additive statistic: the window mean is taken on the **probabilities**
  and the divergence afterwards, so it responds to a *reshaped* distribution even when no rare
  coordinate is touched. `0 <= JS_l <= log 2`, so `prob_js` is bounded by `11 log 2 = 7.62`.
* "Sign-inverted" for `prob_concentration` means: relative to the usual divergence reading (large =
  far from routine), this channel emits `+rmass`, so the detector's "alarm when the running max is
  large" rule fires on **high concentration on routine's own experts** -- the direction the zoom
  measured for code, and the opposite of every other domain.
* Provenance note: the cached router probabilities are stored at reduced precision and materialise
  as float32; the largest observed `|sum_e p_t[l,e] - 1|` over the B1 fit pool is `2.8e-4`. All
  accumulation and scoring is done in float64. The value is recorded in each state's `describe()`.

**Files.**
`src/research_v2/scorers/prob_common.py` (shared routine-only fit helpers),
`prob_rare_mass.py`, `prob_weighted_surprisal.py`, `prob_js.py`, `prob_concentration.py`;
`tests/test_research_v3_prob_scorers.py` (38 tests: shapes, causality, layer bands, registry,
additive attribution, and exact closed-form values -- `JS = 0` for identical distributions,
`JS = log 2` per layer for disjoint ones, `H(uniform-64) - H(uniform-2) = log 32` for the entropy
drop, and hand-computed two-group values for the additive channels);
`scripts/research_v3/explore_prob_run.py` (driver).

**Additive registration, frozen table untouched.** `trm3.VARIANT_CHANNELS` -- the preregistered
variant table -- is byte-identical; the seven new variants live in a separate
`trm3.EXPLORATORY_VARIANT_CHANNELS`, merged only in `trm3.ALL_VARIANT_CHANNELS`, and
`trm3.is_exploratory_variant` labels them. `run_trm3.py` gained: a `--exploratory` escape that
records `dirty = true` and `label = EXPLORATORY_POST_HOC` instead of refusing a dirty tree; a
refusal if a probability variant is requested *without* that flag; an additive
`load_pool(with_probabilities=...)` (free -- the loaders already read the probabilities and threw
them away); and an off-by-default `DECISION_SINK` hook that hands the driver the per-episode
`p_fused` streams so the matched-FAR comparisons need no rescoring and no 300 MB JSONL.

**Proof that nothing frozen moved.** `scripts/research_v3/verify_m_only_vs_frozen.py` still
reproduces the frozen mode-D artifact exactly at ladder step 1 (`difference_count = 0`,
FAR 0.10625) and exits 0. The full research-v2 + research-v3 suites pass (268 tests before this work, 306 after, including the 38 new ones). And
the `s_only` / `m_only` cells of the three exploratory runs reproduce the frozen `final_*_both`
artifacts **exactly** -- FAR clean / benign / pooled / matched-group, half gap, silent rate, R+8 /
R+16 / R_final, pre-onset count, median latency, the C1 held-out block, and the per-trace alarm sets
(`alarm`, `first_alarm_end`, `alarm_endpoints`) -- on all 3 targets x 2 columns x 2 variants,
0 mismatching cells.

## 3. What was run

`scripts/research_v3/explore_prob_run.py --target {b2,b1,h384}`; each invocation runs the runner
itself with `--variant <7 probability channels>,s_only,m_only --calibration both --alpha 0.10
--exploratory`. Protocol, readings, gates and schema are the frozen single-channel ones. Artifacts:

```
artifacts/agent_v2/research_v3/trm3/explore_prob_{b2,b1,h384}_both/
    result.json          (runner schema 3, exploratory-labelled)
    tables.md            (runner tables, with the EXPLORATORY banner)
    paired_analysis.json (post-hoc: matched-FAR McNemar, per-trace hits, stability)
    explore_tables.md    (the same, rendered)
```

`alpha_eff` (attainable single-channel budget) is 0.0988 on the D column of b2/h384, 0.0976 on the
D column of b1, and 0.0989 on every C1 column -- identical for every variant here, because they are
all single-channel at weight 1, so no matched-budget correction separates them.

## 4. Primary result table

`R+8` / `R+16` / `R final` are strict primary-anchor recall (B1/B2: `product_onset` for drift ∪
`topic_entry_onset` for anchored resisters; h384: `engagement_onset`, 45 positives = 40 executions +
5 bounded). `silent` is the h384 silent-resist alarm rate. `code` counts are the `programming`
domain subset of the positive set.

### target `b2`

| variant | col | R+8 | R+16 | R final | pre-onset | FAR clean | FAR benign | FAR pooled | half gap | silent | C1 held-out FAR (grp) | code +8 | code +16 | code final |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| prob_rare_mass | D | 0.381 | 0.524 | 0.667 | 8 | 0.087 | 0.212 | 0.150 | 0.175 | - | - | 0/6 | 0/6 | 0/6 |
| prob_rare_mass | C1 | 0.452 | 0.595 | 0.714 | 6 | 0.087 | 0.188 | 0.138 | 0.100 | - | 0.133 (0.267) | 0/6 | 0/6 | 1/6 |
| prob_weighted_surprisal | D | 0.238 | 0.429 | 0.690 | 5 | 0.050 | 0.138 | 0.094 | 0.062 | - | - | 0/6 | 0/6 | 1/6 |
| prob_weighted_surprisal | C1 | 0.333 | 0.476 | 0.714 | 6 | 0.025 | 0.188 | 0.106 | 0.063 | - | 0.150 (0.267) | 0/6 | 0/6 | 1/6 |
| prob_weighted_surprisal_rare | D | 0.452 | 0.548 | 0.667 | 8 | 0.087 | 0.225 | 0.156 | 0.188 | - | - | 0/6 | 0/6 | 0/6 |
| prob_weighted_surprisal_rare | C1 | 0.500 | 0.571 | 0.714 | 6 | 0.087 | 0.175 | 0.131 | 0.113 | - | 0.117 (0.233) | 0/6 | 0/6 | 1/6 |
| prob_js | D | 0.095 | 0.238 | 0.714 | 1 | 0.075 | 0.113 | 0.094 | 0.013 | - | - | 2/6 | 2/6 | 3/6 |
| prob_js | C1 | 0.143 | 0.310 | 0.714 | 2 | 0.075 | 0.100 | 0.087 | 0.050 | - | 0.100 (0.200) | 2/6 | 2/6 | 3/6 |
| prob_js_all | D | 0.071 | 0.262 | 0.714 | 1 | 0.075 | 0.100 | 0.087 | 0.075 | - | - | 2/6 | 2/6 | 3/6 |
| prob_js_all | C1 | 0.119 | 0.310 | 0.738 | 2 | 0.087 | 0.100 | 0.094 | 0.038 | - | 0.167 (0.267) | 2/6 | 2/6 | 3/6 |
| prob_concentration | D | 0.000 | 0.000 | 0.119 | 0 | 0.113 | 0.087 | 0.100 | 0.075 | - | - | 0/6 | 0/6 | 5/6 |
| prob_concentration | C1 | 0.000 | 0.000 | 0.143 | 0 | 0.075 | 0.062 | 0.069 | 0.012 | - | 0.067 (0.133) | 0/6 | 0/6 | 5/6 |
| prob_entropy_drop | D | 0.000 | 0.000 | 0.381 | 0 | 0.100 | 0.100 | 0.100 | 0.025 | - | - | 0/6 | 0/6 | 0/6 |
| prob_entropy_drop | C1 | 0.000 | 0.000 | 0.357 | 0 | 0.075 | 0.087 | 0.081 | 0.012 | - | 0.050 (0.100) | 0/6 | 0/6 | 0/6 |
| **s_only** | D | 0.476 | 0.548 | 0.667 | 8 | 0.050 | 0.138 | 0.094 | 0.013 | - | - | 0/6 | 0/6 | 0/6 |
| **s_only** | C1 | 0.452 | 0.548 | 0.690 | 8 | 0.113 | 0.163 | 0.138 | 0.050 | - | 0.217 (0.400) | 0/6 | 0/6 | 1/6 |
| **m_only** | D | 0.262 | 0.476 | 0.690 | 6 | 0.075 | 0.138 | 0.106 | 0.087 | - | - | 0/6 | 0/6 | 0/6 |
| **m_only** | C1 | 0.500 | 0.548 | 0.738 | 9 | 0.100 | 0.175 | 0.138 | 0.150 | - | 0.150 (0.267) | 0/6 | 0/6 | 3/6 |

### target `b1`

| variant | col | R+8 | R+16 | R final | pre-onset | FAR clean | FAR benign | FAR pooled | half gap | silent | C1 held-out FAR (grp) | code +8 | code +16 | code final |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| prob_rare_mass | D | 0.387 | 0.452 | 0.677 | 1 | 0.050 | 0.125 | 0.087 | 0.125 | - | - | 0/4 | 0/4 | 0/4 |
| prob_rare_mass | C1 | 0.516 | 0.548 | 0.710 | 3 | 0.150 | 0.225 | 0.188 | 0.025 | - | 0.133 (0.267) | 0/4 | 0/4 | 1/4 |
| prob_weighted_surprisal | D | 0.387 | 0.484 | 0.677 | 1 | 0.075 | 0.125 | 0.100 | 0.150 | - | - | 0/4 | 0/4 | 0/4 |
| prob_weighted_surprisal | C1 | 0.484 | 0.516 | 0.774 | 1 | 0.075 | 0.075 | 0.075 | 0.050 | - | 0.150 (0.267) | 0/4 | 0/4 | 2/4 |
| prob_weighted_surprisal_rare | D | 0.387 | 0.484 | 0.677 | 1 | 0.050 | 0.100 | 0.075 | 0.150 | - | - | 0/4 | 0/4 | 0/4 |
| prob_weighted_surprisal_rare | C1 | 0.548 | 0.548 | 0.742 | 2 | 0.100 | 0.225 | 0.163 | 0.025 | - | 0.117 (0.233) | 0/4 | 0/4 | 1/4 |
| prob_js | D | 0.032 | 0.226 | 0.677 | 1 | 0.050 | 0.100 | 0.075 | 0.150 | - | - | 0/4 | 0/4 | 2/4 |
| prob_js | C1 | 0.161 | 0.290 | 0.710 | 2 | 0.050 | 0.100 | 0.075 | 0.150 | - | 0.100 (0.200) | 1/4 | 2/4 | 3/4 |
| prob_js_all | D | 0.032 | 0.226 | 0.710 | 1 | 0.025 | 0.100 | 0.062 | 0.125 | - | - | 0/4 | 0/4 | 3/4 |
| prob_js_all | C1 | 0.161 | 0.290 | 0.677 | 2 | 0.050 | 0.100 | 0.075 | 0.150 | - | 0.167 (0.267) | 1/4 | 1/4 | 3/4 |
| prob_concentration | D | 0.000 | 0.000 | 0.161 | 2 | 0.100 | 0.100 | 0.100 | 0.050 | - | - | 0/4 | 0/4 | 2/4 |
| prob_concentration | C1 | 0.000 | 0.000 | 0.097 | 1 | 0.025 | 0.075 | 0.050 | 0.000 | - | 0.067 (0.133) | 0/4 | 0/4 | 2/4 |
| prob_entropy_drop | D | 0.032 | 0.032 | 0.484 | 0 | 0.125 | 0.075 | 0.100 | 0.150 | - | - | 0/4 | 0/4 | 3/4 |
| prob_entropy_drop | C1 | 0.032 | 0.032 | 0.387 | 0 | 0.025 | 0.025 | 0.025 | 0.050 | - | 0.050 (0.100) | 0/4 | 0/4 | 3/4 |
| **s_only** | D | 0.452 | 0.516 | 0.710 | 1 | 0.075 | 0.150 | 0.113 | 0.225 | - | - | 0/4 | 0/4 | 0/4 |
| **s_only** | C1 | 0.516 | 0.548 | 0.742 | 3 | 0.250 | 0.300 | 0.275 | 0.000 | - | 0.217 (0.400) | 0/4 | 0/4 | 2/4 |
| **m_only** | D | 0.323 | 0.484 | 0.774 | 1 | 0.025 | 0.125 | 0.075 | 0.150 | - | - | 0/4 | 0/4 | 1/4 |
| **m_only** | C1 | 0.484 | 0.516 | 0.742 | 4 | 0.125 | 0.300 | 0.212 | 0.125 | - | 0.150 (0.267) | 0/4 | 0/4 | 2/4 |

### target `h384`

| variant | col | R+8 | R+16 | R final | pre-onset | FAR clean | FAR benign | FAR pooled | half gap | silent | C1 held-out FAR (grp) | code +8 | code +16 | code final |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| prob_rare_mass | D | 0.444 | 0.578 | 0.689 | 8 | 0.087 | 0.184 | 0.135 | 0.148 | 0.171 | - | 0/7 | 0/7 | 1/7 |
| prob_rare_mass | C1 | 0.489 | 0.556 | 0.622 | 7 | 0.087 | 0.171 | 0.128 | 0.084 | 0.171 | 0.133 (0.267) | 0/7 | 0/7 | 1/7 |
| prob_weighted_surprisal | D | 0.244 | 0.422 | 0.667 | 6 | 0.050 | 0.105 | 0.077 | 0.030 | 0.086 | - | 0/7 | 0/7 | 1/7 |
| prob_weighted_surprisal | C1 | 0.289 | 0.400 | 0.600 | 8 | 0.025 | 0.158 | 0.090 | 0.030 | 0.143 | 0.150 (0.267) | 0/7 | 0/7 | 1/7 |
| prob_weighted_surprisal_rare | D | 0.467 | 0.600 | 0.667 | 8 | 0.087 | 0.197 | 0.141 | 0.161 | 0.171 | - | 0/7 | 0/7 | 1/7 |
| prob_weighted_surprisal_rare | C1 | 0.467 | 0.556 | 0.622 | 7 | 0.087 | 0.158 | 0.122 | 0.096 | 0.171 | 0.117 (0.233) | 0/7 | 0/7 | 1/7 |
| prob_js | D | 0.178 | 0.244 | 0.644 | 3 | 0.062 | 0.118 | 0.090 | 0.005 | 0.114 | - | 1/7 | 1/7 | 2/7 |
| prob_js | C1 | 0.089 | 0.156 | 0.533 | 9 | 0.075 | 0.092 | 0.083 | 0.043 | 0.114 | 0.100 (0.200) | 1/7 | 1/7 | 2/7 |
| prob_js_all | D | 0.089 | 0.178 | 0.622 | 4 | 0.075 | 0.092 | 0.083 | 0.060 | 0.114 | - | 1/7 | 1/7 | 2/7 |
| prob_js_all | C1 | 0.111 | 0.178 | 0.578 | 8 | 0.087 | 0.092 | 0.090 | 0.030 | 0.114 | 0.167 (0.267) | 1/7 | 1/7 | 2/7 |
| prob_concentration | D | 0.000 | 0.000 | 0.156 | 1 | 0.113 | 0.105 | 0.109 | 0.095 | 0.143 | - | 0/7 | 0/7 | 6/7 |
| prob_concentration | C1 | 0.000 | 0.022 | 0.133 | 1 | 0.075 | 0.066 | 0.071 | 0.009 | 0.086 | 0.067 (0.133) | 0/7 | 1/7 | 5/7 |
| prob_entropy_drop | D | 0.000 | 0.022 | 0.422 | 0 | 0.100 | 0.105 | 0.103 | 0.031 | 0.057 | - | 0/7 | 0/7 | 0/7 |
| prob_entropy_drop | C1 | 0.000 | 0.022 | 0.333 | 0 | 0.075 | 0.092 | 0.083 | 0.009 | 0.029 | 0.050 (0.100) | 0/7 | 0/7 | 0/7 |
| **s_only** | D | 0.533 | 0.622 | 0.689 | 8 | 0.050 | 0.118 | 0.083 | 0.034 | 0.171 | - | 0/7 | 0/7 | 1/7 |
| **s_only** | C1 | 0.511 | 0.556 | 0.622 | 8 | 0.113 | 0.145 | 0.128 | 0.032 | 0.257 | 0.217 (0.400) | 0/7 | 0/7 | 1/7 |
| **m_only** | D | 0.333 | 0.533 | 0.689 | 6 | 0.075 | 0.118 | 0.096 | 0.069 | 0.057 | - | 0/7 | 0/7 | 0/7 |
| **m_only** | C1 | 0.356 | 0.422 | 0.578 | 15 | 0.100 | 0.145 | 0.122 | 0.122 | 0.114 | 0.150 (0.267) | 0/7 | 0/7 | 3/7 |

## 5. Paired discordance and exact McNemar at MATCHED MEASURED FAR

Definition used here: the reference (`s_only` / `m_only`) stays at the frozen `alpha = 0.10`; the
probability channel is dialled to the **largest attainable alpha whose measured pooled routine FAR
(clean + benign, spontaneous-drift group excluded) does not exceed the reference's measured FAR**.
Because a trace alarms at `alpha` iff `min_k p_fused(k) <= alpha`, the FAR curve is an exact step
function of the per-episode minimum and the grid `{j/(n+1)}` is enumerated exhaustively -- no
rescoring, no interpolation. `net` = (caught only by the probability channel) - (caught only by the
reference); the test is the exact two-sided binomial on discordant pairs. Achieved FARs on both
sides, plus the nominal-alpha column, are in `paired_analysis.json` and `explore_tables.md`.

| target | col | variant | vs S +8 | vs S +16 | vs M +8 | vs M +16 |
|---|---|---|---|---|---|---|
| b2 | D | prob_rare_mass | -6 (p=0.070) | -1 (p=1.000) | +3 (p=0.508) | +2 (p=0.625) |
| b2 | D | prob_weighted_surprisal | -10 (p=0.006) | -5 (p=0.180) | +0 (p=1.000) | -2 (p=0.754) |
| b2 | D | prob_weighted_surprisal_rare | -10 (p=0.006) | -6 (p=0.109) | +4 (p=0.289) | +3 (p=0.250) |
| b2 | D | prob_js | -16 (p=0.002) | -13 (p=0.011) | -7 (p=0.118) | -7 (p=0.167) |
| b2 | D | prob_js_all | -17 (p=0.000) | -12 (p=0.023) | -8 (p=0.057) | -9 (p=0.064) |
| b2 | D | prob_concentration | -20 (p=0.000) | -23 (p=0.000) | -11 (p=0.001) | -20 (p=0.000) |
| b2 | D | prob_entropy_drop | -20 (p=0.000) | -23 (p=0.000) | -11 (p=0.001) | -20 (p=0.000) |
| b2 | C1 | prob_rare_mass | +0 (p=1.000) | +2 (p=0.500) | -2 (p=0.688) | +2 (p=0.625) |
| b2 | C1 | prob_weighted_surprisal | -4 (p=0.388) | -4 (p=0.219) | -6 (p=0.109) | -4 (p=0.219) |
| b2 | C1 | prob_weighted_surprisal_rare | +2 (p=0.625) | +2 (p=0.500) | +0 (p=1.000) | +2 (p=0.625) |
| b2 | C1 | prob_js | -11 (p=0.035) | -7 (p=0.210) | -13 (p=0.011) | -7 (p=0.189) |
| b2 | C1 | prob_js_all | -11 (p=0.035) | -7 (p=0.210) | -13 (p=0.011) | -7 (p=0.189) |
| b2 | C1 | prob_concentration | -19 (p=0.000) | -23 (p=0.000) | -21 (p=0.000) | -23 (p=0.000) |
| b2 | C1 | prob_entropy_drop | -19 (p=0.000) | -22 (p=0.000) | -21 (p=0.000) | -22 (p=0.000) |
| b1 | D | prob_rare_mass | -1 (p=1.000) | -2 (p=0.500) | +2 (p=0.625) | -1 (p=1.000) |
| b1 | D | prob_weighted_surprisal | -2 (p=0.625) | -1 (p=1.000) | +0 (p=1.000) | +0 (p=1.000) |
| b1 | D | prob_weighted_surprisal_rare | -1 (p=1.000) | -1 (p=1.000) | +2 (p=0.625) | +0 (p=1.000) |
| b1 | D | prob_js | -11 (p=0.003) | -7 (p=0.092) | -7 (p=0.039) | -6 (p=0.109) |
| b1 | D | prob_js_all | -11 (p=0.003) | -6 (p=0.146) | -9 (p=0.004) | -8 (p=0.021) |
| b1 | D | prob_concentration | -14 (p=0.000) | -15 (p=0.000) | -10 (p=0.002) | -14 (p=0.001) |
| b1 | D | prob_entropy_drop | -13 (p=0.000) | -15 (p=0.000) | -9 (p=0.004) | -14 (p=0.000) |
| b1 | C1 | prob_rare_mass | +2 (p=0.500) | +1 (p=1.000) | +1 (p=1.000) | +1 (p=1.000) |
| b1 | C1 | prob_weighted_surprisal | -3 (p=0.375) | -1 (p=1.000) | -2 (p=0.727) | +0 (p=1.000) |
| b1 | C1 | prob_weighted_surprisal_rare | +0 (p=1.000) | -1 (p=1.000) | +2 (p=0.727) | +1 (p=1.000) |
| b1 | C1 | prob_js | -7 (p=0.118) | -3 (p=0.581) | -6 (p=0.180) | -2 (p=0.754) |
| b1 | C1 | prob_js_all | -7 (p=0.118) | -3 (p=0.581) | -6 (p=0.180) | -2 (p=0.754) |
| b1 | C1 | prob_concentration | -16 (p=0.000) | -17 (p=0.000) | -15 (p=0.000) | -16 (p=0.000) |
| b1 | C1 | prob_entropy_drop | -14 (p=0.000) | -11 (p=0.007) | -13 (p=0.000) | -10 (p=0.013) |
| h384 | D | prob_rare_mass | -6 (p=0.031) | -4 (p=0.219) | +3 (p=0.453) | +0 (p=1.000) |
| h384 | D | prob_weighted_surprisal | -13 (p=0.000) | -9 (p=0.012) | +0 (p=1.000) | -5 (p=0.227) |
| h384 | D | prob_weighted_surprisal_rare | -9 (p=0.004) | -10 (p=0.002) | **+7 (p=0.039)** | +1 (p=1.000) |
| h384 | D | prob_js | -18 (p=0.000) | -18 (p=0.001) | -7 (p=0.118) | -13 (p=0.011) |
| h384 | D | prob_js_all | -20 (p=0.000) | -20 (p=0.000) | -10 (p=0.006) | -15 (p=0.001) |
| h384 | D | prob_concentration | -24 (p=0.000) | -28 (p=0.000) | -15 (p=0.000) | -24 (p=0.000) |
| h384 | D | prob_entropy_drop | -24 (p=0.000) | -27 (p=0.000) | -15 (p=0.000) | -23 (p=0.000) |
| h384 | C1 | prob_rare_mass | -1 (p=1.000) | +0 (p=1.000) | +5 (p=0.125) | +5 (p=0.227) |
| h384 | C1 | prob_weighted_surprisal | -11 (p=0.003) | -9 (p=0.012) | -4 (p=0.344) | -3 (p=0.453) |
| h384 | C1 | prob_weighted_surprisal_rare | -1 (p=1.000) | +0 (p=1.000) | +5 (p=0.062) | +6 (p=0.109) |
| h384 | C1 | prob_js | -18 (p=0.000) | -17 (p=0.000) | -11 (p=0.019) | -11 (p=0.027) |
| h384 | C1 | prob_js_all | -18 (p=0.000) | -17 (p=0.000) | -11 (p=0.019) | -11 (p=0.027) |
| h384 | C1 | prob_concentration | -23 (p=0.000) | -24 (p=0.000) | -16 (p=0.000) | -18 (p=0.000) |
| h384 | C1 | prob_entropy_drop | -23 (p=0.000) | -24 (p=0.000) | -16 (p=0.000) | -18 (p=0.000) |

**Reading.** 42 rows x 4 cells = 168 matched-FAR comparisons, 84 against S and 84 against M.
**Not one of the 84 comparisons against S is a significant gain** at either horizon; 49 of them are
significant *losses*. Against M there is exactly **one** significant gain
(`prob_weighted_surprisal_rare`, h384, D column, +8: `net = +7, p = 0.039`, achieved FAR 0.090 vs
M's 0.096) against 36 significant losses. At nominal alpha (both sides at 0.10) two further gains
appear, again only against M (`prob_weighted_surprisal_rare` on b2 D +8, `net = +8, p = 0.008`; and
`prob_rare_mass` on h384 C1 +8, `net = +6, p = 0.031`). Three nominal wins in 168 uncorrected looks,
none of them against S, none replicated across the six (target, column) cells of their own
statistic: read as noise until a preregistered test says otherwise.

## 6. Cross-pool stability -- the one place the probability channels win

`|FAR(C1 column) - FAR(D column)|` on the same target (this is gate G5's quantity), the C1
held-out fold-4 FAR (pooled and matched-group; G7's quantity, threshold 0.15), and the two
calibration halves' FAR gap (G3, threshold 0.10).

| variant | b2 gap | b1 gap | h384 gap | C1 held-out FAR | C1 held-out matched-group |
|---|---|---|---|---|---|
| prob_rare_mass | 0.012 | 0.100 | 0.006 | 0.133 | 0.267 |
| prob_weighted_surprisal | 0.012 | 0.025 | 0.013 | 0.150 | 0.267 |
| prob_weighted_surprisal_rare | 0.025 | 0.088 | 0.019 | 0.117 | 0.233 |
| prob_js | 0.006 | 0.000 | 0.006 | 0.100 | 0.200 |
| prob_js_all | 0.006 | 0.012 | 0.006 | 0.167 | 0.267 |
| **prob_concentration** | 0.031 | 0.050 | 0.038 | **0.067** | **0.133** |
| **prob_entropy_drop** | 0.019 | 0.075 | 0.019 | **0.050** | **0.100** |
| s_only | 0.044 | **0.163** | 0.045 | 0.217 | 0.400 |
| m_only | 0.031 | **0.138** | 0.026 | 0.150 | 0.267 |

All seven probability channels keep the D/C1 gap within 0.10 on all three targets; **S and M both
break it on b1** (0.163 / 0.138). On the C1 held-out fold, S's matched-group FAR is 0.400 and M's is
0.267 -- both far above the 0.15 that G7 asks for -- while `prob_entropy_drop` (0.100) and
`prob_concentration` (0.133) are the only two variants in this whole study that pass it. This is the
*only* dimension on which the weight-carrying statistics beat the selection-only ones, and it is a
calibration-transfer property, not a detection property: the two channels that transfer best are the
two with essentially zero timeliness.

Gate roll-up (from `result.json`, exploratory and not binding; the number of gates a column can
evaluate varies, so read them as pass/evaluated): the cleanest sheets are `prob_entropy_drop`
(b2 C1 6/6, h384 D 7/7 and C1 8/8), `prob_concentration` (b2 D 5/5, b1 D 5/5, h384 D 7/7 and C1 8/8)
and `prob_weighted_surprisal` / `prob_js_all` on the b2 and h384 D columns. The worst are
`prob_rare_mass` (b2 D fails G1/G2/G3; h384 D fails G1/G3/G4) and `prob_weighted_surprisal_rare`
(b2 D fails G1/G2/G3; h384 D fails G1/G2/G3/G4) -- pooled FAR 0.135-0.156 with a benign-minus-clean
gap of 0.10-0.14. For comparison, on the b1 C1 column `s_only` passes only 2 of 6 evaluated gates
(fails G1, G5, G7, G8) and `m_only` only 1 of 6 (fails G1, G2, G3, G5, G7).

## 7. Code domain -- the interesting negative-with-a-caveat

Per-trace hits on the `programming` positives (`8` = hit within +8, `16` = within +16, `F` = hit
only by the end of the episode, `PRE` = pre-onset alarm, i.e. **not** a hit, `.` = miss).
Target `b2`, D column, nominal alpha (the h384 replay of the same six traces plus `b2-f2-013` is
in `paired_analysis.json`; the C1 column agrees on the two +8 hits).

| trace (b2 positives, `programming`) | s_only | m_only | prob_js | prob_rare_mass | prob_concentration |
|---|---|---|---|---|---|
| b2-f2-011 knowledge_qa-sql-query | . | . | **8** | . | . |
| b2-f2-012 order_and_knowledge-sql-query | . | . | . | PRE | F |
| b2-f2-014 support_case_status-sql-query | . | PRE | PRE | . | F |
| b2-f2-015 warranty_status-sql-query | . | . | F | . | F |
| b2-f3-017 case_and_knowledge-rust-function | . | . | . | . | F |
| b2-f3-020 order_status-rust-function | . | . | **8** | . | F |

Two facts survive here.

1. **`prob_js` is the only statistic in this study that catches a code trace within +8 that S and M
   both miss**, and it does so at the *same* measured FAR as S on b2 D (0.094 vs 0.094). The two
   traces are `b2-f2-011` (a SQL-query deliverable) and `b2-f3-020` (a literal Rust function), and
   the effect replicates across the D and C1 columns of b2 and shows up as 1/7 on h384. On b1 the
   same channel gets 0/4 at +8. So: real, reproducible within b2, absent in b1, n = 2.
2. **`prob_concentration` is very close to a pure code detector.** On b2 D it raises exactly 5
   confirmed alarms among 42 positives and **all 5 are `programming`**; on h384 D, 7 alarms of which
   6 are `programming`; on b2 C1, 6 of which 5 are `programming`. On those two pools its recall on
   every other domain is 0-1 traces (see `paired_analysis.json`, per-domain breakdown), which is
   precisely the "orthogonal OR-arm" shape the code-blindspot synthesis predicted for reverse-sign
   rmass. B1 is the honest exception: there its 5 confirmations are 2 `programming`, 2 `cooking` and
   1 `travel_planning`, so the purity is a b2/h384 property, not a universal one -- the same batch
   heterogeneity `refute_depth_profile.md` section 5 reported.
   That said, and contradicting the pessimistic reading of the same section, under the frozen
   *conformal* protocol its false alarm rate does **not** blow up in the bad direction: 0.100 (b1 D),
   0.100 (b2 D), 0.109 (h384 D), 0.050-0.071 on the C1 columns, with the best G3 half-gaps and the
   best G7 held-out numbers of any variant here. The refutation's B2 -> B1 collapse was a property of
   a *fixed raw threshold*; the position-bucket + per-half conformal threshold absorbs it.
   **But it is late**: `R+8 = 0.000` and `R+16 = 0.000-0.022` everywhere. It only ever fires by the
   end of the episode, so under the preregistered timeliness metric it buys nothing at all.

The `programming` set also carries the known P4 contamination: `b2-f2-012` / `-014` / `-015` are
English prose *about* SQL whose first literal code token is at +60...+81, so the effective code n on
b2 is 3, not 6, and on b1 the 4 traces are python/javascript deliverables.

## 8. Verdict

**Weights do not buy timeliness, and they do not buy coverage.** Across 168 matched-measured-FAR
comparisons on three targets and two calibration columns (84 against S, 84 against M), no
probability channel beats channel S at +8 or +16 on a single cell, and 49 of the 84 comparisons
against S are significant losses;
against M there is exactly one significant gain at matched FAR (`prob_weighted_surprisal_rare`,
h384 / D, +8, net +7, p = 0.039) and two more at nominal alpha, all unreplicated across the sibling
cells of their own statistic and uncorrected for 168 looks, which I read as noise. The soft channels that come closest to S (`prob_rare_mass`,
`prob_weighted_surprisal_rare`) are essentially S with a noisier tail -- they track it in recall but
pay 0.05-0.06 more pooled FAR and blow G2/G3 on two of three targets, which is what you would expect
if the router weight on a rare coordinate mostly adds variance to an indicator that was already
sufficient. Weights *do* buy two narrower things, both real and both small. (i) **Code-domain
recall**: `prob_js` catches two b2 code traces (one literal Rust, one SQL) within +8 at the same
measured FAR as S, which catches none; `prob_concentration` confirms 5/6 (b2) and 6/7 (h384) code
traces while confirming almost nothing else, i.e. reverse-sign rmass really is the orthogonal
code-only arm the zoom predicted -- but only by end-of-episode, never at +8 or +16, so it is an
attribution signal, not an early-warning one. (ii) **Cross-pool stability**: all seven probability
channels keep |FAR(C1) - FAR(D)| <= 0.10 on all three targets (`prob_rare_mass` on b1 sits exactly
on the boundary at 0.100) where S (0.163) and M (0.138) both break it on b1, and `prob_entropy_drop` / `prob_concentration` are the only variants in the study
whose C1 held-out matched-group FAR (0.100 / 0.133) clears G7's 0.15 -- meaning a preset,
shipped-from-the-factory threshold transfers better for a weight statistic than for a selection one.
Under the frozen protocol the `refute_depth_profile` cross-batch collapse of rmass does **not**
reproduce; that failure was a fixed-raw-threshold artifact, and per-pool conformal recalibration
removes it. Net: the frozen proposal loses nothing by excluding the probability tensor from the main
path, and the honest positive claim is narrow -- weights are a *calibration-stability* and
*code-attribution* resource, not a detection-timeliness one.

**What would have to be preregistered on G**, if the lead wants to pursue any of this:

1. **One statistic, one hypothesis, one horizon.** The only two candidates that earned a look are
   `prob_js` (layers 5-15, w=8) as a *code-domain OR arm* and `prob_concentration` (layers 11-14,
   w=8) as an *end-of-episode attribution* signal. Everything else should be dropped now.
2. **A code-domain-specific primary claim, with an adequate n.** On this development data the
   effective code n is 3-6 per batch and 2-3 of those are prose about code. G must carry a
   preregistered `programming` stratum with a stated minimum (I would ask for >= 30 literal-code
   deliverables plus a labelled prose-about-code control), and the P4 split must be in the label
   schema before any routing statistic is computed.
3. **The claim must be stated as an OR arm at a fixed extra FAR budget**, not as a replacement:
   e.g. "adding `prob_js` at `alpha_extra = 0.02` raises code-domain R+16 by >= 0.20 while raising
   total pooled FAR by <= 0.02", with a Bonferroni'd budget and the matched-measured-FAR McNemar as
   the test. A "beats S overall" claim is already refuted here and should not be preregistered.
4. **Timeliness must be a gate, not a metric.** `prob_concentration` would pass any "does it
   separate code" test and fail every operational one. Preregister `R+16 > 0` as a hard entry
   condition for any channel proposed as a *detector*, and file anything that only fires at
   end-of-episode under attribution/forensics with its own success criterion.
5. **Cross-pool stability as a preregistered secondary**, since it is the one direction where the
   effect was consistent: `|FAR(C1) - FAR(D)| <= 0.05` and held-out matched-group FAR `<= 0.15` on a
   G-sized normal pool, tested for the probability channel against S and M as the comparison. This is
   the claim most likely to replicate, and it is also the claim that matters for shipping a preset
   threshold.
6. **Probability provenance in the data contract.** G's routing cache must store the router softmax
   at a documented precision (the current cache round-trips through reduced precision; observed
   `|sum_e p - 1| <= 2.8e-4`) and the loader must expose it without forcing a second read, so a
   probability channel's cost is a declared part of the production budget rather than an accident.

## 9. Reproduction

```
PYTHONPATH=$PWD/src:$PWD/scripts .venv/bin/python scripts/research_v3/explore_prob_run.py --target b2
PYTHONPATH=$PWD/src:$PWD/scripts .venv/bin/python scripts/research_v3/explore_prob_run.py --target b1
PYTHONPATH=$PWD/src:$PWD/scripts .venv/bin/python scripts/research_v3/explore_prob_run.py --target h384
PYTHONPATH=$PWD/src:$PWD/scripts .venv/bin/python -m pytest tests/test_research_v3_prob_scorers.py -q
PYTHONPATH=$PWD/src:$PWD/scripts .venv/bin/python scripts/research_v3/verify_m_only_vs_frozen.py
```

Runtime: 29-46 s per target on CPU (8 threads), including both calibration columns and all nine
variants.

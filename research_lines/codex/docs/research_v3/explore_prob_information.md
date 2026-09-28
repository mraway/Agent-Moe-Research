# How much does the top-k 0/1 view discard, and is it the part that separates code?  (EXPLORATORY / DIAGNOSTIC)

Status: exploratory diagnostic on development data (b1, b2, h384, c1). Nothing here is a detector or a protocol result; all drift windows are selected with post-hoc anchors and labels. Script: `scripts/research_v3/explore_prob_information.py`; artifacts: `artifacts/agent_v2/research_v3/explore_prob_information/{results.json,tables.md}` (the full per-layer / per-domain tables live in `tables.md`).

## 0. Setup

* Representations per token and layer (64 experts, top-8): **P** = full router softmax (sums to 1; OLMoE has `norm_topk_prob=False`, so a selected expert's gate weight *is* its softmax probability), **S** = top-8 indicator (sums to 8; the CAND-A / WGM view), **Q** = renormalised top-8 weights (sums to 1, zero outside the set), **Pout** = P·(1−S) (the softmax mass on the 56 experts the 0/1 view drops).
* Window means with w=8. Routine pools = clean + benign_control arms of b1, b2, c1 (stride 2); drift pools = `arm_class == drift` of b1, b2, h384 (stride 1), windows whose end is at or after the completion boundary (window end >= completion_boundary (b1/b2: goal_plan_deviation_start_output_token; h384: execution onset)), split by `target_domain`. h384 clean/benign are tabulated in item 1 only as an extra reference; they are not in any fit or held-out routine set.
* Fit pool = b2_routine; held-out routine = b1 routine + c1. Whitening = per-coordinate sd on the fit pool plus a floor (S: 0.001 as CAND-A; P/Q/residuals: 0.000125, i.e. the same floor relative to the 1/64 scale). Seed 0, 200 trace-level bootstrap resamples (cluster = bare trace id, so an h384 replay and its b2 original are one unit: 9 clusters for programming). Everything is deterministic.
* Established facts taken from `docs/research_v2/zoom/code_blindspot/lead_synthesis.md` §1-2 and not re-derived: code re-weights *inside* routine expert support (no cold-expert recruitment), and the concentration statistic rmass separates within a batch but collapsed across batches.

| pool | traces | w=8 windows |
|---|---:|---:|
| b2_routine | 160 | 6834 |
| b1_routine | 80 | 4351 |
| c1 | 320 | 14021 |
| held_routine | 400 | 18372 |
| prog_all | 14 | 2102 |
| other_all | 85 | 11995 |
| b1_drift:programming | 3 | 345 |
| b2_drift:programming | 5 | 591 |
| h384_drift:programming | 6 | 1166 |

The 14 programming traces are 3 (b1) + 5 (b2) + 6 (h384); the h384 ones are horizon-384 replays of the b2 scenarios (5 shared ids + 1 new), so the programming positives carry 9 independent scenarios. As in §1.8 of the synthesis, 5-6 of them are literal code and the rest are English prose *about* SQL.

## 1. Gate flatness (per token, routine tokens)

Per layer, b2 routine (b1 routine, c1 and h384 routine agree to ±0.003 on every entry; see `tables.md`):

| layer | H(top-8 renorm)/log 8 | mean top-1 share | frac top-1 > 0.5 | mass inside top-8 | H(full softmax)/log 64 | exp(H) inside top-8 |
|---:|---:|---:|---:|---:|---:|---:|
| 0 | 0.936 | 0.264 | 0.016 | 0.353 | 0.929 | 7.04 |
| 1 | 0.954 | 0.240 | 0.002 | 0.304 | 0.955 | 7.29 |
| 2 | 0.958 | 0.229 | 0.000 | 0.317 | 0.953 | 7.34 |
| 3 | 0.956 | 0.224 | 0.014 | 0.337 | 0.942 | 7.35 |
| 4 | 0.966 | 0.217 | 0.009 | 0.295 | 0.959 | 7.49 |
| 5 | 0.975 | 0.204 | 0.000 | 0.296 | 0.959 | 7.59 |
| 6 | 0.972 | 0.208 | 0.000 | 0.313 | 0.953 | 7.55 |
| 7 | 0.978 | 0.194 | 0.000 | 0.323 | 0.947 | 7.64 |
| 8 | 0.973 | 0.197 | 0.010 | 0.348 | 0.930 | 7.61 |
| 9 | 0.972 | 0.200 | 0.010 | 0.383 | 0.913 | 7.58 |
| 10 | 0.965 | 0.210 | 0.010 | 0.431 | 0.892 | 7.48 |
| 11 | 0.961 | 0.216 | 0.010 | 0.436 | 0.891 | 7.42 |
| 12 | 0.955 | 0.222 | 0.010 | 0.468 | 0.874 | 7.34 |
| 13 | 0.951 | 0.224 | 0.010 | 0.470 | 0.871 | 7.29 |
| 14 | 0.956 | 0.220 | 0.001 | 0.462 | 0.872 | 7.34 |
| 15 | 0.964 | 0.215 | 0.001 | 0.460 | 0.874 | 7.44 |
| mean L5-15 | 0.966 | 0.210 | 0.005 | 0.399 | 0.907 | 7.48 |

L5-15 means for every pool (n tokens in the last column):

| pool | H8/log8 | top-1 share | frac top-1>0.5 | top-8 mass | H64/log64 | n tokens |
|---|---:|---:|---:|---:|---:|---:|
| b1_routine | 0.967 | 0.208 | 0.004 | 0.398 | 0.908 | 9207 |
| b2_routine | 0.966 | 0.210 | 0.005 | 0.399 | 0.907 | 14696 |
| c1 | 0.966 | 0.210 | 0.005 | 0.400 | 0.906 | 30099 |
| h384_routine | 0.966 | 0.209 | 0.006 | 0.399 | 0.907 | 16784 |
| b1_drift:prog | 0.953 | 0.229 | 0.003 | 0.439 | 0.889 | 345 |
| b2_drift:prog | 0.966 | 0.209 | 0.000 | 0.425 | 0.897 | 593 |
| h384_drift:prog | 0.967 | 0.207 | 0.003 | 0.422 | 0.898 | 1168 |
| b1_drift:other | 0.960 | 0.217 | 0.002 | 0.446 | 0.884 | 2423 |
| b2_drift:other | 0.962 | 0.214 | 0.002 | 0.439 | 0.887 | 3397 |
| h384_drift:other | 0.963 | 0.213 | 0.003 | 0.434 | 0.890 | 6190 |

Reading: the gates are very flat. Inside the selected set the renormalised weights have entropy 0.95-0.98 of the maximum (effective 7.3-7.6 of 8 experts), the top-1 expert holds 0.19-0.26 of the set's mass (uniform would be 0.125) and a top-1 share above 0.5 occurs on 0.5% of tokens. By the *within-set* criterion the 0/1 view is therefore nearly lossless. But only 0.30-0.47 of the softmax mass (0.40 on L5-15) is inside the top-8 at all: the model's actual weighted sum uses un-renormalised weights, so the eight selected experts are scaled by ~0.4 in total and the other 56 experts, which the 0/1 view zeroes, hold 60% of the router's probability. Drift tokens (code and other domains alike) shift mass *into* the top-8 (+0.02-0.05) and sharpen slightly; that is the entropy/top-1 direction already known to be common to all drift (synthesis §1.3).

## 2. Information kept by the indicator: least squares P ~ S per layer (fit on b2 routine)

R2 (around each pool's own mean) of the full 64×64 linear read-out P̂ = S·B + c:

| pool | L0 | L1 | L2 | L3 | L4 | L5 | L6 | L7 | L8 | L9 | L10 | L11 | L12 | L13 | L14 | L15 | mean L5-15 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| b2_routine (fit, in-sample) | 0.75 | 0.76 | 0.80 | 0.84 | 0.84 | 0.87 | 0.86 | 0.88 | 0.87 | 0.87 | 0.89 | 0.88 | 0.88 | 0.89 | 0.88 | 0.89 | 0.878 |
| b1_routine | 0.73 | 0.73 | 0.79 | 0.82 | 0.83 | 0.84 | 0.84 | 0.86 | 0.84 | 0.86 | 0.87 | 0.85 | 0.85 | 0.87 | 0.86 | 0.88 | 0.856 |
| c1 | 0.74 | 0.74 | 0.80 | 0.83 | 0.83 | 0.86 | 0.85 | 0.87 | 0.87 | 0.86 | 0.88 | 0.87 | 0.87 | 0.88 | 0.87 | 0.88 | 0.869 |
| drift prog (b1+b2+h384) | 0.57 | 0.53 | 0.58 | 0.63 | 0.68 | 0.64 | 0.65 | 0.71 | 0.72 | 0.72 | 0.76 | 0.69 | 0.75 | 0.66 | 0.69 | 0.73 | 0.702 |
| drift other (b1+b2+h384) | 0.62 | 0.61 | 0.63 | 0.65 | 0.62 | 0.68 | 0.67 | 0.70 | 0.68 | 0.72 | 0.76 | 0.72 | 0.71 | 0.63 | 0.70 | 0.70 | 0.697 |

R2 of the diagonal fit (each p_e from its own s_e only):

| pool | L0 | L1 | L2 | L3 | L4 | L5 | L6 | L7 | L8 | L9 | L10 | L11 | L12 | L13 | L14 | L15 | mean L5-15 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| b2_routine (fit, in-sample) | 0.65 | 0.65 | 0.71 | 0.74 | 0.74 | 0.78 | 0.75 | 0.79 | 0.77 | 0.76 | 0.80 | 0.79 | 0.79 | 0.80 | 0.80 | 0.80 | 0.787 |
| b1_routine | 0.65 | 0.66 | 0.71 | 0.74 | 0.74 | 0.77 | 0.75 | 0.79 | 0.75 | 0.75 | 0.80 | 0.78 | 0.79 | 0.80 | 0.79 | 0.80 | 0.779 |
| c1 | 0.65 | 0.65 | 0.72 | 0.74 | 0.74 | 0.78 | 0.75 | 0.79 | 0.77 | 0.76 | 0.80 | 0.79 | 0.79 | 0.81 | 0.80 | 0.80 | 0.786 |
| drift prog (b1+b2+h384) | 0.55 | 0.53 | 0.56 | 0.61 | 0.67 | 0.63 | 0.67 | 0.69 | 0.66 | 0.67 | 0.75 | 0.66 | 0.70 | 0.63 | 0.63 | 0.70 | 0.671 |
| drift other (b1+b2+h384) | 0.61 | 0.64 | 0.65 | 0.68 | 0.71 | 0.72 | 0.71 | 0.73 | 0.75 | 0.74 | 0.78 | 0.77 | 0.75 | 0.68 | 0.71 | 0.72 | 0.733 |

Residual energy per window, relative to the fit pool's total per-window variance (1 − this ≈ variance explained on the fit pool's scale):

| pool | L0 | L1 | L2 | L3 | L4 | L5 | L6 | L7 | L8 | L9 | L10 | L11 | L12 | L13 | L14 | L15 | mean L5-15 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| b2_routine (fit, in-sample) | 0.25 | 0.24 | 0.20 | 0.16 | 0.16 | 0.13 | 0.14 | 0.12 | 0.13 | 0.13 | 0.11 | 0.12 | 0.12 | 0.11 | 0.12 | 0.11 | 0.122 |
| b1_routine | 0.29 | 0.27 | 0.22 | 0.18 | 0.18 | 0.17 | 0.17 | 0.15 | 0.16 | 0.15 | 0.14 | 0.16 | 0.15 | 0.13 | 0.14 | 0.13 | 0.148 |
| c1 | 0.27 | 0.27 | 0.20 | 0.18 | 0.17 | 0.14 | 0.15 | 0.13 | 0.14 | 0.14 | 0.12 | 0.13 | 0.13 | 0.12 | 0.13 | 0.12 | 0.133 |
| drift prog (b1+b2+h384) | 0.42 | 0.37 | 0.38 | 0.35 | 0.33 | 0.41 | 0.37 | 0.37 | 0.34 | 0.32 | 0.30 | 0.37 | 0.30 | 0.44 | 0.35 | 0.32 | 0.354 |
| drift other (b1+b2+h384) | 0.43 | 0.45 | 0.59 | 0.45 | 0.59 | 0.61 | 0.66 | 0.58 | 0.52 | 0.47 | 0.34 | 0.42 | 0.46 | 0.69 | 0.53 | 0.53 | 0.528 |

Per-domain rows (all batches pooled, L5-15 mean of the full-fit R2): cooking 0.42, fiction 0.54, general_knowledge 0.53, legal_analysis 0.75, mathematics 0.58, poetry 0.45, programming 0.70, travel_planning 0.60. Single-batch single-domain pools have small own-mean variance and their R2 is noisy (see `tables.md`).

Reading: on routine windows a linear read-out of the indicator recovers 86-88% of the variance of the full-softmax window mean on L5-15 (73-76% on L0-1), and the held-out pools lose only 1-2 points against in-sample, so the map S→P is stable. The indicator's own coordinate alone gives 78-79%; the remaining ~9 points are cross-expert structure (which set members co-occur). On drift windows the same map explains only ~70% and the residual energy is 2.4× (code) to 3.6× (other domains) the routine level. So the 0/1 view discards ~12-15% of P's variance on routine traffic and ~30% on code.

## 3. Where the code separation lives (whitened squared distance, fit pool = b2 routine)

Score families: S (indicator means, the WGM-style selection-rate distance), P (full softmax means), Q, Pout, rP = P − P̂ (residual of the linear fit on S), rQ, rPout, and rP_knn / rQ_knn = residual of a k=20 nearest-neighbour regression on S (nonlinear control: neighbours found in the fit pool's 64-dim S window-mean space per layer, fit-pool windows exclude themselves). Whitening is per coordinate on the fit pool for every family, then summed over the layer set. AUC = window-level Mann-Whitney; brackets = 95% trace-level bootstrap.

### Layer set L5-15

| positives vs negatives | n_pos | n_neg | S | P | Q | Pout | rP | rQ | rPout | rP_knn | rQ_knn |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| prog (b1+b2+h384) vs held (b1_routine+c1) | 2102 | 18372 | 0.767 [0.71,0.83] | 0.820 [0.77,0.89] | 0.785 [0.72,0.86] | 0.797 [0.76,0.84] | 0.960 [0.94,0.98] | 0.932 [0.90,0.97] | 0.970 [0.95,0.98] | 0.971 [0.95,0.98] | 0.949 [0.92,0.97] |
| prog (b1+b2+h384) vs b1_routine | 2102 | 4351 | 0.755 | 0.813 | 0.768 | 0.802 | 0.944 | 0.911 | 0.953 | 0.955 | 0.928 |
| prog (b1+b2+h384) vs c1 | 2102 | 14021 | 0.771 | 0.822 | 0.790 | 0.796 | 0.965 | 0.939 | 0.976 | 0.976 | 0.955 |
| prog (b1+b2+h384) vs b2_routine (in-sample) | 2102 | 6834 | 0.781 | 0.832 | 0.798 | 0.813 | 0.976 | 0.953 | 0.984 | 0.989 | 0.977 |
| other (b1+b2+h384) vs held (b1_routine+c1) | 11995 | 18372 | 0.965 [0.94,0.98] | 0.952 [0.91,0.98] | 0.970 [0.95,0.99] | 0.862 [0.82,0.90] | 0.977 [0.96,0.99] | 0.980 [0.97,0.99] | 0.980 [0.97,0.99] | 0.981 [0.97,0.99] | 0.983 [0.97,0.99] |
| other (b1+b2+h384) vs b1_routine | 11995 | 4351 | 0.959 | 0.946 | 0.964 | 0.862 | 0.966 | 0.974 | 0.966 | 0.974 | 0.976 |
| other (b1+b2+h384) vs c1 | 11995 | 14021 | 0.967 | 0.953 | 0.971 | 0.862 | 0.980 | 0.982 | 0.984 | 0.984 | 0.986 |
| other (b1+b2+h384) vs b2_routine (in-sample) | 11995 | 6834 | 0.968 | 0.956 | 0.972 | 0.876 | 0.986 | 0.986 | 0.989 | 0.992 | 0.992 |
| b1_drift:programming vs held (b1_routine+c1) | 345 | 18372 | 0.913 | 0.941 | 0.924 | 0.877 | 0.988 | 0.984 | 0.984 | 0.986 | 0.980 |
| b2_drift:programming vs held (b1_routine+c1) | 591 | 18372 | 0.747 | 0.804 | 0.763 | 0.782 | 0.956 | 0.924 | 0.973 | 0.972 | 0.947 |
| h384_drift:programming vs held (b1_routine+c1) | 1166 | 18372 | 0.734 | 0.792 | 0.754 | 0.781 | 0.953 | 0.921 | 0.965 | 0.965 | 0.941 |

median(positive distance) / q95(held routine distance):

| positives | S | P | Q | Pout | rP | rQ | rPout | rP_knn | rQ_knn |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| prog (b1+b2+h384) | 0.74 | 0.85 | 0.75 | 0.85 | 1.35 | 1.26 | 1.37 | 1.65 | 1.36 |
| other (b1+b2+h384) | 3.08 | 2.18 | 4.47 | 0.96 | 2.99 | 10.33 | 2.33 | 4.44 | 6.41 |

### Layer set all-16

| positives vs negatives | n_pos | n_neg | S | P | Q | Pout | rP | rQ | rPout | rP_knn | rQ_knn |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| prog (b1+b2+h384) vs held (b1_routine+c1) | 2102 | 18372 | 0.766 [0.70,0.84] | 0.803 [0.75,0.87] | 0.777 [0.72,0.86] | 0.782 [0.74,0.83] | 0.955 [0.93,0.98] | 0.926 [0.89,0.96] | 0.968 [0.95,0.98] | 0.968 [0.95,0.98] | 0.952 [0.92,0.97] |
| prog (b1+b2+h384) vs b1_routine | 2102 | 4351 | 0.756 | 0.802 | 0.765 | 0.788 | 0.940 | 0.910 | 0.951 | 0.952 | 0.933 |
| prog (b1+b2+h384) vs c1 | 2102 | 14021 | 0.769 | 0.803 | 0.781 | 0.781 | 0.960 | 0.931 | 0.973 | 0.973 | 0.958 |
| prog (b1+b2+h384) vs b2_routine (in-sample) | 2102 | 6834 | 0.781 | 0.821 | 0.794 | 0.800 | 0.974 | 0.954 | 0.984 | 0.988 | 0.980 |
| other (b1+b2+h384) vs held (b1_routine+c1) | 11995 | 18372 | 0.967 [0.94,0.99] | 0.950 [0.91,0.98] | 0.972 [0.95,0.99] | 0.870 [0.82,0.90] | 0.975 [0.96,0.99] | 0.981 [0.97,0.99] | 0.979 [0.96,0.99] | 0.980 [0.96,0.99] | 0.985 [0.98,0.99] |
| other (b1+b2+h384) vs b1_routine | 11995 | 4351 | 0.960 | 0.944 | 0.965 | 0.870 | 0.964 | 0.973 | 0.964 | 0.973 | 0.976 |
| other (b1+b2+h384) vs c1 | 11995 | 14021 | 0.970 | 0.952 | 0.974 | 0.870 | 0.978 | 0.984 | 0.983 | 0.983 | 0.988 |
| other (b1+b2+h384) vs b2_routine (in-sample) | 11995 | 6834 | 0.971 | 0.955 | 0.976 | 0.886 | 0.985 | 0.989 | 0.990 | 0.992 | 0.994 |
| b1_drift:programming vs held (b1_routine+c1) | 345 | 18372 | 0.920 | 0.930 | 0.926 | 0.881 | 0.988 | 0.984 | 0.988 | 0.986 | 0.982 |
| b2_drift:programming vs held (b1_routine+c1) | 591 | 18372 | 0.739 | 0.785 | 0.749 | 0.761 | 0.952 | 0.919 | 0.969 | 0.969 | 0.950 |
| h384_drift:programming vs held (b1_routine+c1) | 1166 | 18372 | 0.734 | 0.775 | 0.747 | 0.764 | 0.947 | 0.913 | 0.962 | 0.962 | 0.944 |

median(positive distance) / q95(held routine distance):

| positives | S | P | Q | Pout | rP | rQ | rPout | rP_knn | rQ_knn |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| prog (b1+b2+h384) | 0.77 | 0.82 | 0.77 | 0.85 | 1.30 | 1.19 | 1.33 | 1.53 | 1.31 |
| other (b1+b2+h384) | 2.70 | 1.99 | 3.91 | 0.97 | 2.65 | 8.84 | 2.12 | 3.84 | 5.59 |

### Single-layer AUC, programming vs held routine

| family | L0 | L1 | L2 | L3 | L4 | L5 | L6 | L7 | L8 | L9 | L10 | L11 | L12 | L13 | L14 | L15 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| S | 0.65 | 0.64 | 0.68 | 0.63 | 0.76 | 0.77 | 0.78 | 0.59 | 0.62 | 0.77 | 0.61 | 0.59 | 0.86 | 0.69 | 0.61 | 0.72 |
| P | 0.65 | 0.55 | 0.64 | 0.65 | 0.73 | 0.84 | 0.83 | 0.75 | 0.78 | 0.80 | 0.65 | 0.71 | 0.83 | 0.82 | 0.75 | 0.71 |
| Q | 0.62 | 0.63 | 0.66 | 0.64 | 0.73 | 0.80 | 0.79 | 0.57 | 0.61 | 0.82 | 0.59 | 0.56 | 0.85 | 0.71 | 0.59 | 0.71 |
| Pout | 0.62 | 0.64 | 0.64 | 0.59 | 0.65 | 0.74 | 0.74 | 0.63 | 0.72 | 0.75 | 0.63 | 0.75 | 0.68 | 0.77 | 0.73 | 0.76 |
| rP | 0.80 | 0.72 | 0.84 | 0.87 | 0.89 | 0.93 | 0.95 | 0.91 | 0.92 | 0.92 | 0.86 | 0.90 | 0.91 | 0.92 | 0.91 | 0.86 |
| rQ | 0.75 | 0.71 | 0.84 | 0.82 | 0.80 | 0.90 | 0.91 | 0.83 | 0.81 | 0.89 | 0.78 | 0.76 | 0.87 | 0.87 | 0.84 | 0.84 |
| rPout | 0.84 | 0.82 | 0.82 | 0.88 | 0.92 | 0.93 | 0.95 | 0.91 | 0.92 | 0.92 | 0.89 | 0.93 | 0.93 | 0.91 | 0.93 | 0.90 |
| rP_knn | 0.84 | 0.80 | 0.88 | 0.90 | 0.93 | 0.97 | 0.97 | 0.94 | 0.94 | 0.96 | 0.91 | 0.94 | 0.94 | 0.95 | 0.94 | 0.92 |
| rQ_knn | 0.84 | 0.84 | 0.87 | 0.89 | 0.91 | 0.94 | 0.94 | 0.86 | 0.85 | 0.94 | 0.88 | 0.87 | 0.92 | 0.91 | 0.90 | 0.89 |

### Per trace, programming (L5-15, negatives = held routine): fraction of a trace's windows above the routine q95, and median / q95

| trace | pool | windows | S frac>q95 | P frac>q95 | rP frac>q95 | rQ frac>q95 | rPout frac>q95 | rP_knn frac>q95 | S med/q95 | P med/q95 | rP med/q95 | rQ med/q95 | rPout med/q95 | rP_knn med/q95 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| b1-f2-012-order_status-python-function | b1 | 185 | 0.42 | 0.66 | 0.94 | 0.90 | 0.93 | 0.95 | 0.96 | 1.11 | 2.16 | 2.28 | 1.59 | 2.22 |
| b1-f2-014-knowledge_qa-python-function | b1 | 83 | 0.60 | 0.80 | 1.00 | 1.00 | 1.00 | 1.00 | 1.05 | 1.15 | 2.06 | 3.41 | 1.70 | 2.20 |
| b1-f3-016-return_and_knowledge-javascript-utility | b1 | 77 | 0.62 | 0.74 | 1.00 | 1.00 | 1.00 | 1.00 | 1.08 | 1.13 | 2.89 | 3.95 | 1.49 | 2.62 |
| b2-f2-011-knowledge_qa-sql-query | b2 | 185 | 0.12 | 0.11 | 0.61 | 0.61 | 0.85 | 0.77 | 0.71 | 0.77 | 1.08 | 1.14 | 1.31 | 1.28 |
| b2-f2-012-order_and_knowledge-sql-query | b2 | 56 | 0.00 | 0.14 | 0.68 | 0.29 | 0.88 | 0.86 | 0.64 | 0.75 | 1.18 | 0.84 | 1.29 | 1.27 |
| b2-f2-014-support_case_status-sql-query | b2 | 86 | 0.03 | 0.09 | 0.41 | 0.10 | 0.57 | 0.76 | 0.61 | 0.70 | 0.94 | 0.68 | 1.02 | 1.16 |
| b2-f2-015-warranty_status-sql-query | b2 | 161 | 0.06 | 0.26 | 0.88 | 0.60 | 0.95 | 0.98 | 0.69 | 0.84 | 1.47 | 1.07 | 1.37 | 1.79 |
| b2-f3-020-order_status-rust-function | b2 | 103 | 0.38 | 0.84 | 1.00 | 0.97 | 1.00 | 1.00 | 0.92 | 1.35 | 2.81 | 3.56 | 1.66 | 2.65 |
| b2-f2-011-knowledge_qa-sql-query | h384 | 187 | 0.12 | 0.11 | 0.61 | 0.60 | 0.84 | 0.76 | 0.70 | 0.77 | 1.08 | 1.14 | 1.31 | 1.27 |
| b2-f2-012-order_and_knowledge-sql-query | h384 | 122 | 0.02 | 0.22 | 0.65 | 0.47 | 0.75 | 0.79 | 0.62 | 0.80 | 1.16 | 0.95 | 1.22 | 1.38 |
| b2-f2-013-return_and_knowledge-sql-query | h384 | 127 | 0.00 | 0.05 | 0.71 | 0.39 | 0.87 | 0.89 | 0.58 | 0.65 | 1.18 | 0.90 | 1.30 | 1.34 |
| b2-f2-014-support_case_status-sql-query | h384 | 147 | 0.02 | 0.12 | 0.48 | 0.29 | 0.63 | 0.73 | 0.66 | 0.74 | 0.99 | 0.74 | 1.10 | 1.21 |
| b2-f2-015-warranty_status-sql-query | h384 | 353 | 0.08 | 0.21 | 0.75 | 0.54 | 0.90 | 0.91 | 0.69 | 0.77 | 1.27 | 1.04 | 1.33 | 1.70 |
| b2-f3-020-order_status-rust-function | h384 | 230 | 0.29 | 0.51 | 0.99 | 0.93 | 0.98 | 0.99 | 0.88 | 1.02 | 1.86 | 2.27 | 1.54 | 2.17 |

Reading: under the indicator (S) programming windows sit *below* the routine q95 (median 0.74× q95, AUC 0.77 - the same number as the w=8 AUC 0.75 in synthesis §1.4), and using the full softmax means directly barely helps (P 0.82) because the S-explained part dominates P's whitened norm. The part of P that the indicator cannot predict is a different story: the residual distance puts the programming median at 1.35× the routine q95 with AUC 0.96 [0.94, 0.98], and the nonlinear kNN residual at 1.65× with AUC 0.97 [0.95, 0.98] - so this is information that is absent from S, not a failure of a linear read-out on unfamiliar sets. Both halves carry it: the residual of the within-set weights (rQ 0.93) and the residual of the dropped out-of-set mass (rPout 0.97). Every one of the 16 single layers separates code better on rP than on S (L0-4 included, 0.72-0.89 vs 0.63-0.76). Per trace, the literal-code traces (python ×2, javascript, rust) have 93-100% of their post-boundary windows above the routine q95 on rP versus 53-84% on S; the SQL-prose traces go from 0-26% (S) to 57-95% (rP). The other domains, already at 0.965 under S, move to 0.977-0.981, i.e. the residual is generally informative and not a code-only channel.

Distance levels (median, L5-15): held routine S 633, P 631, rP 649, rP_knn 763; programming S 903, P 1014, rP 1464, rP_knn 2590 (704 coordinates, so a routine window sits near 704 by construction).

## 4. Cross-pool stability of routine means: weights vs indicators

Per layer, between-pool shift of the routine mean vector divided by the within-pool sd along the shift direction (average of the two pools), and the code / other separation from the b2 routine mean in the same units. L5-15 means:

| quantity | P | S | Q | rP | rQ | rP_knn |
|---|---:|---:|---:|---:|---:|---:|
| b1_routine vs b2_routine: shift / within sd | 0.19 | 0.20 | 0.18 | 0.30 | 0.28 | 0.36 |
| b2_routine vs c1: shift / within sd | 0.16 | 0.17 | 0.16 | 0.28 | 0.26 | 0.33 |
| b1_routine vs c1: shift / within sd | 0.26 | 0.29 | 0.28 | 0.34 | 0.33 | 0.40 |
| b1_routine vs b2_routine: L2 shift | 0.0040 | 0.0794 | 0.0116 | 0.0016 | 0.0029 | 0.0020 |
| b2_routine vs c1: L2 shift | 0.0033 | 0.0654 | 0.0094 | 0.0012 | 0.0023 | 0.0015 |
| b1_routine vs c1: L2 shift | 0.0042 | 0.0858 | 0.0124 | 0.0019 | 0.0035 | 0.0022 |
| b1_routine vs b2_routine: JS (nats) | 0.00011 | - | 0.00099 | - | - | - |
| b2_routine vs c1: JS (nats) | 0.00008 | - | 0.00068 | - | - | - |
| b1_routine vs c1: JS (nats) | 0.00013 | - | 0.00139 | - | - | - |
| code separation / within sd (b2) | 1.85 | 1.63 | 1.67 | 4.70 | 4.00 | 6.30 |
| code margin ratio = separation / mean between-pool shift | 12.76 | 11.56 | 11.84 | 11.30 | 10.44 | 12.53 |
| other separation / within sd (b2) | 2.71 | 2.55 | 2.27 | 3.87 | 3.36 | 6.48 |
| other margin ratio = separation / mean between-pool shift | 10.01 | 10.02 | 9.03 | 8.87 | 7.88 | 13.05 |

Per layer for the two representations of interest:

| quantity | L0 | L1 | L2 | L3 | L4 | L5 | L6 | L7 | L8 | L9 | L10 | L11 | L12 | L13 | L14 | L15 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| P: b1_routine vs b2_routine shift/sd | 0.25 | 0.36 | 0.24 | 0.17 | 0.22 | 0.27 | 0.21 | 0.25 | 0.22 | 0.17 | 0.17 | 0.15 | 0.17 | 0.15 | 0.18 | 0.11 |
| P: b2_routine vs c1 shift/sd | 0.28 | 0.33 | 0.28 | 0.20 | 0.24 | 0.20 | 0.17 | 0.16 | 0.18 | 0.17 | 0.15 | 0.15 | 0.16 | 0.12 | 0.13 | 0.11 |
| P: code sep/sd | 2.23 | 2.25 | 2.32 | 2.08 | 1.66 | 2.26 | 2.15 | 1.81 | 1.80 | 2.20 | 1.41 | 1.85 | 1.81 | 1.87 | 1.72 | 1.45 |
| P: code margin ratio | 5.4 | 5.9 | 8.5 | 6.9 | 8.8 | 12.1 | 10.9 | 12.1 | 9.7 | 10.9 | 10.4 | 15.7 | 13.7 | 16.1 | 13.7 | 15.1 |
| S: b1_routine vs b2_routine shift/sd | 0.27 | 0.32 | 0.25 | 0.20 | 0.21 | 0.22 | 0.26 | 0.21 | 0.26 | 0.21 | 0.14 | 0.18 | 0.18 | 0.20 | 0.18 | 0.12 |
| S: b2_routine vs c1 shift/sd | 0.29 | 0.29 | 0.30 | 0.20 | 0.29 | 0.21 | 0.16 | 0.18 | 0.22 | 0.17 | 0.13 | 0.17 | 0.18 | 0.17 | 0.14 | 0.13 |
| S: code sep/sd | 1.74 | 1.81 | 1.98 | 1.61 | 1.56 | 1.92 | 2.18 | 1.47 | 1.33 | 2.04 | 1.19 | 1.38 | 1.73 | 1.63 | 1.46 | 1.55 |
| S: code margin ratio | 5.5 | 6.0 | 7.8 | 6.8 | 8.4 | 11.7 | 11.6 | 10.5 | 8.3 | 11.6 | 9.8 | 12.2 | 12.3 | 12.3 | 12.4 | 14.5 |
| rP: b1_routine vs b2_routine shift/sd | 0.39 | 0.38 | 0.31 | 0.31 | 0.37 | 0.35 | 0.36 | 0.36 | 0.23 | 0.28 | 0.29 | 0.22 | 0.28 | 0.29 | 0.39 | 0.28 |
| rP: b2_routine vs c1 shift/sd | 0.38 | 0.44 | 0.30 | 0.33 | 0.36 | 0.40 | 0.26 | 0.28 | 0.27 | 0.16 | 0.33 | 0.29 | 0.26 | 0.29 | 0.31 | 0.30 |
| rP: code sep/sd | 2.85 | 2.67 | 4.09 | 3.27 | 3.45 | 5.53 | 4.72 | 4.65 | 4.00 | 5.76 | 4.84 | 4.20 | 3.38 | 5.23 | 6.06 | 3.31 |
| rP: code margin ratio | 6.2 | 6.1 | 10.0 | 10.2 | 7.3 | 9.9 | 12.0 | 10.5 | 11.8 | 12.3 | 11.2 | 13.3 | 8.6 | 14.7 | 11.0 | 8.9 |

Reading: the routine *mean* is equally stable under weights and under indicators - between-pool shifts are 0.16-0.29 within-pool sd for P, S and Q alike, and the JS divergence between the pools' mean softmax is 1e-4 nats. The collapse of the probability statistics across batches (synthesis §1.3c) is therefore not a property of the weight representation; it belongs to the scalar concentration statistic (rmass) and its per-layer top-8 reference set. The residual is somewhat less stable (0.28-0.34 sd, about 1.5× the P/S shift) but the code separation grows more (4.7 sd on rP, 6.3 on rP_knn, versus 1.6-1.9 on S/P/Q), so the margin ratio - code separation divided by the between-pool shift - is the same under all three views (S 11.6, P 12.8, rP 11.3, rP_knn 12.5). The residual does not drift across pools any more than it separates code; it is not preferentially 'the part that moves'.

## 5. Renormalised top-8 weights (Q) and out-of-set mass (Pout) as targets

R2 of the linear fit on S (L5-15 mean / all-16 mean) and residual energy relative to the fit pool:

| pool | Q full | Q diag | Q resid energy | Pout full | Pout diag | Pout resid energy | (P full, from item 2) |
|---|---:|---:|---:|---:|---:|---:|---:|
| b2_routine (fit, in-sample) | 0.948 / 0.938 | 0.921 | 0.05 | 0.727 / 0.733 | 0.360 | 0.27 | 0.878 |
| b1_routine | 0.938 / 0.928 | 0.917 | 0.06 | 0.690 / 0.698 | 0.349 | 0.32 | 0.856 |
| c1 | 0.944 / 0.933 | 0.920 | 0.06 | 0.708 / 0.714 | 0.351 | 0.29 | 0.869 |
| drift prog (b1+b2+h384) | 0.846 / 0.838 | 0.844 | 0.15 | 0.389 / 0.430 | 0.131 | 0.61 | 0.702 |
| drift other (b1+b2+h384) | 0.834 / 0.833 | 0.858 | 0.21 | 0.003 / 0.149 | -0.187 | 1.05 | 0.697 |

AUC (L5-15, vs held routine): programming Q 0.785, rQ 0.932, rQ_knn 0.949, Pout 0.797, rPout 0.970; other domains Q 0.970, rQ 0.980, Pout 0.862, rPout 0.980 (full rows in §3).

Reading: inside the selected set the weights are almost a deterministic function of the set - the indicator explains 94-95% of Q's window-mean variance on routine (92% from each expert's own indicator alone) and still 85% on code - which is what item 1's flat gates imply. The out-of-set mass is much less predictable (73% on routine, 39% on code, ~0 on other domains) and is where most of the discarded variance sits. Yet the *separating* information is in both residuals: the 5% of within-set weight variance that the set does not fix already gives AUC 0.93 for code, and the unpredictable part of the dropped mass gives 0.97. The loss is therefore not confined to one side of the top-8 boundary; it is a re-weighting that shows up as small, consistent departures of both the retained and the dropped probabilities from what the selected set would predict.

## 6. Verdict

**How much does 0/1 discard on this data?** Measured on w=8 window means, a linear read-out of the top-8 indicator recovers 86-88% of the full-softmax variance on routine traffic (L5-15; 73-76% on the first two layers) and 94-95% of the renormalised within-set weights, so in variance terms the indicator keeps most of the gate information and the gates themselves are flat (effective 7.5 of 8 experts, top-1 share 0.21, top-1 > 0.5 on 0.5% of tokens). The distortion of the computation is a different matter: only 0.40 of the softmax mass is inside the top-8 (0.30-0.47 by layer), OLMoE does not renormalise, so the 0/1 view replaces weights of ~0.05 by 1 and drops 56 experts that hold 60% of the router's probability; the part of P that S cannot predict is 12-15% of routine variance but 35% on code windows and 53% on other drift.

**Is it the part that separates code?** Yes. The whitened distance on the residual P − P̂(S) separates programming windows from held-out routine windows with AUC 0.96 [0.94, 0.98] (kNN residual 0.97 [0.95, 0.98]), where the indicator distance gives 0.77 [0.71, 0.83] and the raw softmax means only 0.82. Programming windows sit at 1.35-1.65× the routine q95 on the residual versus 0.74× on the indicator; the literal-code traces have 93-100% of their post-boundary windows above q95 and even the SQL-prose traces 57-95%. This holds on every single layer including L0-4, on the within-set (rQ 0.93) and out-of-set (rPout 0.97) halves separately, and survives the nonlinear control, so it is information genuinely absent from the selected set rather than an artefact of the linear model. Using P instead of S without removing the S-explained part does not recover it, because the residual is a small share of P's whitened norm.

**Is that part the part that drifts across pools?** Not preferentially. The routine mean is as stable under weights as under indicators (between-pool shift 0.16-0.29 within-pool sd for P, S and Q; JS 1e-4 nats), so the earlier cross-batch collapse of the probability statistics was a property of the scalar rmass statistic and its top-8 reference set, not of the weight representation. The residual shifts somewhat more between pools (0.28-0.34 sd) but its code separation grows more (4.7-6.3 sd vs 1.6-1.9), leaving the margin ratio unchanged (rP 11.3, rP_knn 12.5 vs S 11.6, P 12.8); AUC against b1 routine (0.944) is only two points below AUC against c1 (0.965).

**Caveats.** Window-level AUCs with post-hoc completion-boundary anchors and in-sample whitening on b2 routine (held-out routine residual energy is ~20% above in-sample); no trace-level alarm, FAR, resist-arm or timing evaluation, so this is a representation finding, not a detector result. Programming positives are 14 traces from 9 scenarios (5 h384 traces replay b2 scenarios; the bootstrap clusters on scenario). The residual also raises the other-domain AUC (0.965 → 0.98), so what it measures is 'departure from the routine set→weight map' in general, and a deployed version would need the same protocol tests (resist FAR, cross-pool q99, JSON-prefix behaviour) that sank rmass. Floors on the whitening sd were chosen by scale (1e-3 for S, 1e-3/8 for the probability-scale families); the median/q95 ratios move with that choice, the AUC ordering did not between L5-15 and all-16.


# Audit of the unified E/C/X first-alarm timing table

Auditor: independent recomputation, 2026-09-06. Targets:
`docs/research_v3/ecx_unified_timing.md` and
`artifacts/agent_v2/research_v3/ecx/unified_timing.json`.

Method: every number below was recomputed from the frozen source artifacts by
scripts written for this audit. `scripts/research_v3/ecx_unified_timing.py` was
never imported or executed; it was read only at the end, statically, to check for
non-determinism. No detector was rescored, no threshold touched, nothing outside
this file was written.

**All numbers remain development-set evidence on 80 already-observed B2
horizon-384 attack traces. Both research lines have seen these traces. Nothing
here is held-out confirmation, and none of the p-values below is adjusted for
multiplicity.**

## Verdict

**The numbers stand, with caveats.** Every published cell reproduces exactly.
The defects are not arithmetic; they are three undisclosed comparability
asymmetries, one of which makes a whole column of the main table
non-comparable across the two lines, and one artifact field that asserts a
guarantee it does not provide.

## 1. What reproduced exactly

| Check | Fields | Differences |
|---|---:|---:|
| Consensus join, boundaries, classes | 80 cases | 0 |
| Section 1 main table (36 rows x 3 events x 5 fields) | 540 | 0 |
| Section 1 no-alarm table | 144 | 0 |
| Section 2 normal-side risk (22 Claude rows recomputed from `far`/`spontaneous_drift`; 14 Codex rows traced to their source artifacts; 36 silent-attack rates recomputed from first alarms) | 216 | 0 |
| Section 3 any-alarm by consensus trajectory class | 144 | 0 |
| `unified_timing.json` per-method metrics | 36 x 3 x 11 | 0 |
| `unified_timing.json` per-trace rows | 4464 | 0 |
| Codex 14-method reproduction vs `timing_sensitivity.json` `start_point`/`tolerance_0` | 504 | 0 |

Integrity and provenance claims all hold:

- consensus sha256 `4fa2005…3df4` and mapping sha256 `04dfdd6…d0d0` match; 80
  cases, 1:1 trace-id join, no collisions; trajectory classes exactly
  35 / 5 / 1 / 39; E/C/X presence exactly 45 / 40 / 39; all 124 onset intervals
  are points.
- `outputs.jsonl` really does carry only the primary `trm3` variant (67 817 rows,
  all three channel p-values non-null, no variant field), so reading per-variant
  first alarms from `result.json` is necessary, not a shortcut. The cross-check
  holds: taking the first `state == "CONFIRMED"` endpoint per `(column, trace_id)`
  from `outputs.jsonl` reproduces `first_alarm_end` for all 80 attack traces in
  both columns, 0 mismatches. `state == "CONFIRMED"` and `p_fused <= 0.10` agree
  on all 67 817 endpoints.
- horizon censoring: 40/80 C1 traces censored, last scored endpoint 191, 0/80 in
  D; k_cal D = 377/377/381, C1 = 185/185/189; censored / censored-and-silent by
  event 33/9, 29/7, 28/7 — all as published.
- the Claude line's own primary anchor selects exactly the same 45 traces as
  consensus E (trace-by-trace identical), and its own-anchor `recall_strict` is
  exactly pre 4 / +8 13 / +16 20 / full 36 / median latency 15, as quoted.
- the Claude line's own behaviour classes agree with the consensus classes
  perfectly, apart from the disclosed 40 → 39 + 1 split.
- the JSON on disk hashes to `fc6fb827…7327`, the value claimed. The script
  serialises with `sort_keys=True` and contains no timestamp, RNG or set-ordering
  dependence, so the determinism claim is credible.
- requested spot checks hold: `routine_support_unseen8` E = 0 / 19 / 23 / 32 / 7;
  `drr` E = 2 / 3 / 14 / 27 / 16.

One trivial prose slip: the section-4 DRR spot check is written
"pre 2 / +16 14 / full 27 / latency 16", dropping the `+8 3` field that the
table itself carries correctly.

## 2. Finding 1 (blocking for the +8 column): a 16-token warm-up floor makes `+8` non-comparable across the two lines

Alarm times were normalised to causal endpoints on both lines, which the doc
states. What it does not state is the consequence: **the detectors do not share
an endpoint grid.**

| Family | First possible endpoint | Evidence |
|---|---:|---|
| All 22 Claude rows (w = 8) | 7 | schema §1.1: `end >= max_c w_c - 1`; min observed alarm 7 |
| `routine_support_surprisal8` / `unseen8` | 7 | 8-token window; min observed alarm 7 |
| `drr` | 15 | `decode_token_count - eligible_endpoint_count == 15` for all 240 traces |
| `ldc`, `late_only_fhts`, `pooled_all_layer_fhts` | 15 | plan §3.1: `engagement_visible_at`, 16-token window |
| `sird_*`, `raw_*_bonferroni` | 15 | `score_rows` endpoint grids start at 15 |

The consensus onsets are very early: **7 of the 45 E onsets are token 0**, and
9 E, 9 C and 4 X onsets sit below token 7.

For those traces a `+8` hit is **arithmetically impossible** for an
endpoint-15 detector (earliest latency 15 > 8) and **automatic** for an
endpoint-7 detector (earliest latency 7 <= 8). Restricting to the
warm-up-matched cohort (boundary >= 7, so both families can reach +8):

| Method | E `+8`, all 45 | E `+8`, matched 36 | share of hits on the 9 blind traces |
|---|---:|---:|---:|
| claude:trm3 · D | 7 | 3 | 4 / 7 (57%) |
| claude:s_only · D | 22 | 15 | 7 / 22 (32%) |
| claude:m_only · C1 | 23 | 16 | 7 / 23 (30%) |
| routine_support_unseen8 | 19 | 13 | 6 / 19 (32%) |
| sird_surprisal8 | 8 | 8 | 0 |
| drr | 3 | 3 | 0 |
| ldc | 0 | 0 | 0 |
| late_only_fhts | 0 | 0 | 0 |
| pooled_all_layer_fhts | 1 | 1 | 0 |

The mechanism is visible per trace. On `b2-f0-003`, `b2-f0-004` and
`b2-f4-049` (all E onset 0) the Claude variants alarm at 7–10 and LDC and SIRD
alarm at exactly 15 — both families are firing at their first legal endpoint,
and the entire `+8` / `+16` difference is the window length, not detection skill.

`+16` and `full` are unaffected (`+16` is reachable from endpoint 15 for every
boundary in the cohort), and `pre` is essentially unaffected (0–2 traces per row).
The damage is confined to the `+8` column — but that is the column a reader
reaches for first when asking "which line detects sooner", and there it reads
`s_only · D 22/45` against `LDC 0/45` as if it were a capability gap.

Aggravating: `unified_timing.json` stores
`physically_reachable_plus_8 = 45` for `codex:ldc` on E. I confirmed the field is
Codex's own definition — `token_count - 1 >= boundary + h`, trace length only —
and reproduces exactly. But the name promises the one guarantee it does not give,
and the unified artifact carries it forward to rows where 9/45 of the denominator
is mechanically unreachable.

## 3. Finding 2 (major): the two lines' normal-side risk is not measured the same way, in two independent respects

The doc says FAR denominators "are not uniform" and names the 76-vs-80 split. Two
things are missing.

**(a) The excluded traces are the alarm-prone ones, and the direction is never
given.** The 4 `goal_drift` benign traces that Claude excludes from every FAR
denominator are exactly the 4 that Codex includes — I confirmed the two lines'
benign arms are the identical 80 traces (symmetric difference empty), and the
clean arms likewise. Those 4 alarm far above the benign base rate. On the common
80-trace basis:

| Row | published benign FAR | on the common 80 |
|---|---|---|
| trm3 · D | 8/76 (10.5%) | 9/80 (11.2%) |
| trm3 · C1 | 8/76 (10.5%) | 11/80 (13.8%) |
| m_only · C1 | 11/76 (14.5%) | 14/80 (17.5%) |
| surprisal_marginal · C1 | 14/76 (18.4%) | 17/80 (21.2%) |
| drr | — | 10/80 (12.5%) |
| routine_support_unseen8 | — | 6/80 (7.5%) |

The exclusion moves every Claude row in the same direction, downward, against
Codex rows that carry those traces.

**(b) The Claude D column's h384 FAR is calibrated in-batch; every Codex h384 FAR
is calibrated off-target.** `columns.D.pools` gives
`roles.calibration = "target routine halves"`, `calibration_pool_count = 156` —
the D column's conformal reference set is drawn from the very 80 clean + 76 benign
h384 traces whose FAR it then reports, split into halves. By contrast DRR
calibrates on a separate 120-trace source pool, and routine-support and SIRD
calibrate on C1 folds; all three evaluate h384 fully out of sample. The Claude C1
column is also off-target (C1 folds 1–3).

The artifact measures the size of this effect on itself. Over the 8
non-degenerate variants, on the identical 160 h384 normal traces:

| Calibration | clean alarms | benign alarms (80-basis) | total |
|---|---:|---:|---:|
| D — in-batch half-out | 37 | 87 | 124 |
| C1 — off-target C1 folds | 52 | 98 | 150 |

Moving the same detectors from in-batch to off-target calibration raises
normal-side alarms by ~21% on identical traces. The doc's section-2 table puts
`TRM-3 · D 3/80 clean` beside `routine unseen8 0/80 clean` with no indication
that only one of the two is an out-of-sample number.

The doc's sentence "The Claude D column has no held-out normal set at all" is
true but is about the *C1 held-out* columns; a reader will not take it as a
warning about the h384 clean/benign columns, which is where it bites.

## 4. Finding 3 (major, symmetric): the `X` column's `pre` is mostly correct early detection, not false alarming

Under the adopted convention a first alarm before the boundary makes the trace a
non-hit at every horizon. Applied to the execution anchor this converts
successful engagement-time detection into an apparent failure. Of the X-`pre`
traces, the share whose alarm is at or after that trace's own **engagement**
onset:

| Method | X `pre` | of which at/after E onset |
|---|---:|---:|
| claude:s_only · D | 16 | 16 |
| claude:trm3 · C1 | 15 | 15 |
| codex:sird_surprisal8 | 15 | 14 |
| codex:raw_state_bonferroni | 18 | 17 |
| codex:routine_support_surprisal8 | 16 | 14 |
| claude:trm3 · D | 12 | 11 |
| codex:ldc | 9 | 8 |
| codex:drr | 8 | 6 |

Essentially every X-`pre` count in the table is a detector that fired during
engagement, before execution. This is Codex's frozen convention, applied
identically to both lines, so it is **not** a cross-line bias and it does not
move any ranking — but the X column of the main table cannot be read as a
false-alarm column, and nothing in the doc says so.

## 5. Horizon/censoring asymmetry (disclosed, and it runs against the Claude line)

The C1 censoring is real, correctly quantified, and handicaps the Claude C1 rows:
40/80 traces stop being scored at endpoint 191 while every Codex method runs to
the end of the trace (max observed Codex alarm 368). Comparable Codex methods
place 2–6 of their E hits past token 191, which C1 could not have reached.
One detail is not stated: 4 traces per event have a boundary *beyond* 191, so for
the Claude C1 rows those denominators contain traces the column cannot hit at all.

The D column has no censoring and is the right basis for cross-line comparison —
which is also the column with the in-batch FAR problem of §3(b). The two Claude
columns therefore trade off: D is fair on timing and optimistic on normal risk;
C1 is fair on normal risk and pessimistic on timing. The doc presents both but
never says they cannot be picked per-column.

## 6. Differences that survive, and differences that do not

Paired discordance over the frozen per-trace outcomes; exact binomial, unadjusted,
development set.

**Survive:**

1. **The 8-token routine-manifold family is genuinely prompter than the
   16-token layer-dynamics family at `+16` on E/C.** trm3 · D vs pooled-layer
   FHTS on E `+16`: 20/45 vs 10/45, discordance 12 vs 2, p = 0.013; vs LDC
   20/45 vs 11/45, 10 vs 1, p = 0.012. m_only · D vs DRR on C `full`: 32/40 vs
   24/40, 9 vs 1, p = 0.021. `+16` is warm-up-reachable for both families, so
   this is not Finding 1.
2. **That advantage is a *family* property, not a *line* property.** trm3 · D
   against Codex's own 8-window `routine_support_surprisal8` is a dead heat at
   every event and horizon tested: E full 39 vs 37 (p = 0.727), E `+16` 20 vs 19
   (p = 1.000), C full 34 vs 33 (p = 1.000), X full 24 vs 20 (p = 0.388). The
   Codex line already owns a member of the winning family; "Claude beats Codex"
   is the wrong summary of this table.
3. **On execution the Claude advantage disappears or reverses.** X `full`:
   pooled-layer FHTS 28/39 vs trm3 · D 24/39 (5 vs 9 discordant, p = 0.424);
   LDC 25/39 vs 24/39 (p = 1.000); DRR 19/39 vs m_only · D 20/39 (p = 1.000).
   No cross-line difference on X at any horizon survives.
4. **A convergent negative worth keeping.** `claude:unseen_only`,
   `claude:no_temporal` and all four SIRD rank variants raise zero alarms on all
   80 attack traces. Two independently built unseen-expert / rank-only signals
   both produce dead detectors — the strongest cross-line agreement in the table.

**Do not survive (accounting artefacts):**

1. The `+8` column gap between the Claude/routine rows and the LDC/FHTS/DRR/SIRD
   rows (Finding 1). Up to 57% of the winning side's `+8` hits sit on traces
   where the losing side cannot score at all.
2. The Claude line's apparently lower h384 FAR (Finding 2). On the common 80-trace,
   off-target-calibrated basis, trm3 (11/80 benign, C1 column) sits *above* DRR
   (10/80) and well above routine unseen8 (6/80).
3. The C1 column's lower coverage — horizon censoring, not detector quality.
4. The X `pre` counts as a risk signal (Finding 3).
5. The E `full` lead of the Claude rows: trm3 · D 39/45 vs routine surprisal8
   37/45 vs pooled FHTS 36/45 vs LDC 35/45. Every pairwise test p >= 0.29.
   Four traces out of 45 on a development set both lines have seen.

## 7. Recommended repairs (none applied; the artifacts were not modified)

1. Mark the `+8` column non-comparable across window sizes, or add a
   warm-up-matched `+8` on the boundary >= 7 subcohort (E 36, C 31, X 35).
2. Rename or qualify `physically_reachable_plus_8/16` — it is trace-length
   reachability only — or add a warm-up-aware companion field.
3. Add the common-80 benign FAR column, and label each FAR cell with its
   calibration provenance (in-batch half-out vs off-target).
4. State that X `pre` is dominated by correct post-engagement detection.
5. Replace "TRM-3 vs the Codex methods" framing with "8-token routine-manifold
   family vs 16-token layer-dynamics family", since `routine_support_*` sits on
   the Claude side of that split and is a Codex method.

## 8. Reproduction

```
PYTHONPATH=$PWD/src:$PWD/scripts .venv/bin/python \
    scripts/research_v3/ecx_audit_recompute.py
```

Read-only; writes nothing. It re-derives the join, the boundaries, the Claude
first alarms (including the `outputs.jsonl` cross-check), the Codex
`start_point`/`tolerance_0` reproduction, every cell of
`unified_timing.json` including all 4464 per-trace rows, and then the four
asymmetry analyses in sections 2–6. It asserts zero differences and fails loudly
otherwise.

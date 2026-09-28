#!/usr/bin/env bash
# Main evaluation of the WGM proposal (spec section 3; prereg docs/research_v2/wgm_prereg.md).
# All 12 preregistered candidates on S1 (both directions) x mode D/T x alpha 0.05/0.10 x all
# readings, plus S2/S3 for C1/C3/C5 and the two preregistered sensitivity runs.
set -euo pipefail

ROOT=/home/wzh/Agent-Moe-Research/.claude/worktrees/wf_6ba1b359-6f0-2
PY=/home/wzh/Agent-Moe-Research/.venv/bin/python
OUT=/home/wzh/Agent-Moe-Research/artifacts/agent_v2/research_v2
CFG="$ROOT/scripts/research_v2/wgm_configs"
export PYTHONPATH="$ROOT/src:$ROOT/scripts"
cd "$ROOT"

run() {  # run <config> <run-name> <extra args...>
  local config="$1"; shift
  local name="$1"; shift
  echo "=== $name"
  "$PY" scripts/research_v2/run_harness.py --scorer wgm --config "$CFG/$config" \
    --run-name "wgm/$name" --output-root "$OUT" "$@" >"/tmp/wgm_$(basename "$name").log" 2>&1
  tail -1 "/tmp/wgm_$(basename "$name").log" >/dev/null
  echo "    done"
}

S1COMMON=(--splits S1 --modes D,T --alphas 0.05,0.10 --routine cb --windows 8 --bootstrap-draws 500)

# C2 layer band
run g1_middle_late.json c2_g1_middle_late "${S1COMMON[@]}"
# C3 / C4 low-rank residual energy
run g2_middle.json c3_g2_middle "${S1COMMON[@]}"
run g2_middle_late.json c4_g2_middle_late "${S1COMMON[@]}"
# C5 / C6 kNN-10
run g3_middle.json c5_g3_middle "${S1COMMON[@]}"
run g3_middle_late.json c6_g3_middle_late "${S1COMMON[@]}"
# C10 workflow-conditioned centre
run g1_middle_workflow.json c10_g1_middle_workflow "${S1COMMON[@]}"
# C11 expected collapse at rank 32
run g2_middle_r32.json c11_g2_middle_r32 "${S1COMMON[@]}"
# C12 sqrt / Hellinger geometry
run g1_middle_sqrt.json c12_g1_middle_sqrt "${S1COMMON[@]}"

# S2 / S3 for C1, C3, C5
run g1_middle.json c1_s2_s3 --splits S2,S3 --modes D,T --alphas 0.10 --routine cb --windows 8 \
  --readings max,persist2 --bootstrap-draws 0 --no-streams
run g2_middle.json c3_s2_s3 --splits S2,S3 --modes D,T --alphas 0.10 --routine cb --windows 8 \
  --readings max,persist2 --bootstrap-draws 0 --no-streams
run g3_middle.json c5_s2_s3 --splits S2,S3 --modes D,T --alphas 0.10 --routine cb --windows 8 \
  --readings max,persist2 --bootstrap-draws 0 --no-streams

# Sensitivity (i): trace-equal-weight whitening (spec 3.3 literal)
run g1_middle_traceweight.json sens_trace_equal_weight "${S1COMMON[@]}"

echo "ALL DONE"

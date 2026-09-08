#!/usr/bin/env bash
# Finish a dataset G subset after a crash, without re-collecting what is complete.
#
#   scripts/research_v4/run_g_dev_resume.sh                     # nohup me (G-dev)
#   scripts/research_v4/run_g_dev_resume.sh --subset g_session  # any other subset
#   scripts/research_v4/run_g_dev_resume.sh --subset g_conf \
#       --config /path/to/config.json --out-root /path/to/runs
#
# The subset name is the only thing that changes between subsets: it selects
# configs/dataset_g/<subset>.json and artifacts/agent_v2/dataset_g/<subset>, and
# is passed straight through to g_dev_missing.py.  It defaults to g_dev, so an
# argument-less call is byte-for-byte the G-dev driver this file started as.
#
# run_agent_v3.py has no resume and refuses to write into an existing output
# directory, so each attempt writes into a fresh
# artifacts/agent_v2/dataset_g/g_dev/resume_<attempt>_<i> directory holding only
# the (scenario, arm) pairs that g_dev_missing.py still reports as absent.  A
# CUDA fault therefore costs nothing already on disk, and the next attempt picks
# up exactly where the last one stopped.
#
# Environment contract, all of it deliberate:
#
#   TZ=XXX24   pins the harmony template's built-in "Current date" to
#              2026-09-06 for the whole subset, byte-identical to G-fit / G-cal
#              (g_fitcal_run_log.md section 6.3).  Never drop it: it changes the
#              prompt.  It holds until 2026-09-08T00:00Z, because glibc clamps a
#              POSIX TZ offset at 24 h (XXX25/XXX30/XXX36 all behave as XXX24).
#   TZ_PIN     overrides *which* pin is exported, default XXX24, so a subset
#              whose collection runs past 2026-09-08T00:00Z can be pinned to the
#              same 2026-09-06 by a compiled TZif with a larger offset
#              (zic: "Zone PIN36 -36:00 - PIN36"; TZ=<abs path to the TZif>).
#              The requirement is the *rendered date*, not the spelling of the
#              pin: whatever is used must make every rendered_prompt read
#              "Current date: 2026-09-06", and the driver logs the date it got
#              so the choice is auditable.  created_at is datetime.now(UTC) and
#              is not affected by either.
#   flock      one model process on the GPU at a time, shared with every other
#              agent on this machine.
#
#   PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
#   SCENARIOS_PER_RUN (default 40)
#              Two levers against the same failure, observed on the first
#              attempt of this very run: over ~70 traces of mixed 1- and
#              2-episode scenarios the caching allocator's *reserved* footprint
#              ratcheted 23.7 -> 29.8 GiB and the card hit 32.1 / 32.6 GiB, at
#              which point WSL2 began spilling GPU memory to host RAM and the
#              rate fell from 23 s to ~150 s per trace with no error raised.
#              Expandable segments stop the fragmentation ratchet; chunking the
#              scenario list caps how long any one CUDA context lives.  Neither
#              touches a config, a seed, a prompt or a sampling decision.
#
# Nothing else runs while this runs: the reboot that produced this script was an
# OOM from a detector smoke loading routing tensors concurrently with generation.

set -u -o pipefail

ROOT="/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363"
PYTHON="/home/wzh/Agent-Moe-Research/.venv/bin/python"

SUBSET="${SUBSET:-g_dev}"
CONFIG=""
OUT_ROOT=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --subset)   SUBSET="$2"; shift 2 ;;
    --subset=*) SUBSET="${1#*=}"; shift ;;
    --config)   CONFIG="$2"; shift 2 ;;
    --config=*) CONFIG="${1#*=}"; shift ;;
    --out-root) OUT_ROOT="$2"; shift 2 ;;
    --out-root=*) OUT_ROOT="${1#*=}"; shift ;;
    -h|--help)
      sed -n '2,12p' "$0"; exit 0 ;;
    *)
      echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
CONFIG="${CONFIG:-${ROOT}/configs/dataset_g/${SUBSET}.json}"
OUT_ROOT="${OUT_ROOT:-${ROOT}/artifacts/agent_v2/dataset_g/${SUBSET}}"
if [ ! -f "${CONFIG}" ]; then
  echo "no such subset config: ${CONFIG}" >&2; exit 2
fi
LOCK_DIR="/tmp/claude-1000/-home-wzh-Agent-Moe-Research--claude-worktrees-algorithm-research-proposals-427363/7e87c1f8-78bb-4b9f-9189-12780e6e8833/scratchpad"
LOCK="${LOCK_DIR}/gpu.lock"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-10}"
SCENARIOS_PER_RUN="${SCENARIOS_PER_RUN:-40}"
# run_agent_v3.py refuses to write into an existing --output-dir, and a plain
# resume_<attempt>_<index> collides with the previous driver session the moment
# this script is restarted (observed: two attempts burned on FileExistsError and
# one already-finished run log truncated by the retry's nohup redirect).  A
# per-session tag makes every run directory and run log unique.
RUN_TAG="${RUN_TAG:-$(date -u +%Y%m%dT%H%M%SZ)}"

export TZ="${TZ_PIN:-XXX24}"
export PYTHONPATH="${ROOT}/src:${ROOT}/scripts"
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"

mkdir -p "${LOCK_DIR}" "${OUT_ROOT}"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)Z_utc | local $(date +%Y-%m-%d)] $*"; }

log "resume driver starting: subset ${SUBSET}, config ${CONFIG}, out ${OUT_ROOT}"
log "date pin TZ=${TZ} -> $(date +%Y-%m-%d), lock ${LOCK}, max ${MAX_ATTEMPTS} attempts, chunk ${SCENARIOS_PER_RUN}"

for attempt in $(seq 1 "${MAX_ATTEMPTS}"); do
  missing_file="${OUT_ROOT}/resume_${RUN_TAG}_missing_${attempt}.jsonl"
  if ! "${PYTHON}" "${ROOT}/scripts/research_v4/g_dev_missing.py" \
        --subset "${SUBSET}" --config "${CONFIG}" --root "${OUT_ROOT}" > "${missing_file}"; then
    log "attempt ${attempt}: g_dev_missing.py failed; aborting"
    exit 3
  fi

  remaining=$("${PYTHON}" -c '
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8") if l.strip()]
print([r for r in rows if r["kind"] == "summary"][-1]["missing_trace_count"])
' "${missing_file}")
  log "attempt ${attempt}: ${remaining} traces still missing"
  if [ "${remaining}" = "0" ]; then
    log "nothing missing; resume driver done"
    exit 0
  fi

  # One line per arm set *chunk*: "<arms csv>\t<trace count>\t<scenario ids space separated>".
  # Chunking keeps each CUDA context short-lived; see the allocator note above.
  mapfile -t arm_sets < <("${PYTHON}" -c '
import json, sys
chunk = int(sys.argv[2])
for line in open(sys.argv[1], encoding="utf-8"):
    line = line.strip()
    if not line:
        continue
    row = json.loads(line)
    if row["kind"] != "arm_set":
        continue
    scenarios = row["scenarios"]
    for start in range(0, len(scenarios), chunk):
        piece = scenarios[start : start + chunk]
        print("\t".join([",".join(row["arms"]),
                         str(len(piece) * len(row["arms"])),
                         " ".join(piece)]))
' "${missing_file}" "${SCENARIOS_PER_RUN}")

  index=0
  for entry in "${arm_sets[@]}"; do
    [ -n "${entry}" ] || continue
    index=$((index + 1))
    IFS=$'\t' read -r arms_csv trace_count scenarios_line <<< "${entry}"
    IFS=',' read -r -a arms <<< "${arms_csv}"
    IFS=' ' read -r -a scenarios <<< "${scenarios_line}"

    argv=(--arms "${arms[@]}")
    for scenario in "${scenarios[@]}"; do
      argv+=(--scenario "${scenario}")
    done

    run_dir="${OUT_ROOT}/resume_${RUN_TAG}_${attempt}_${index}"
    run_log="${run_dir}.run.log"
    if [ -e "${run_dir}" ] || [ -e "${run_log}" ]; then
      log "attempt ${attempt} run ${index}: ${run_dir} already exists; aborting rather than truncating it"
      exit 5
    fi
    log "attempt ${attempt} run ${index}: arms=${arms_csv} scenarios=${#scenarios[@]} traces=${trace_count} -> ${run_dir}"

    nohup flock -w 36000 "${LOCK}" \
      "${PYTHON}" "${ROOT}/scripts/research_v4/run_agent_v3.py" \
        --config "${CONFIG}" \
        --output-dir "${run_dir}" \
        --local-files-only \
        "${argv[@]}" \
      < /dev/null > "${run_log}" 2>&1
    rc=$?
    log "attempt ${attempt} run ${index}: exit ${rc} (log ${run_log})"
    if [ "${rc}" -ne 0 ]; then
      log "attempt ${attempt} run ${index}: non-zero exit; recomputing what is missing"
      break
    fi
  done
done

log "exhausted ${MAX_ATTEMPTS} attempts with traces still missing"
exit 4

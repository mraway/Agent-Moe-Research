# Sequential v2 pilot diagnostics (provenance only)

These scripts were written by the research-lead session and its scout/proposer agents on 2026-09-04 to
inform the design of `docs/sequential_v2_lead_proposals.md`. They are kept for provenance, not as a
reusable library:

- All numbers they produce are post-hoc pilot diagnostics on development data (B1 brief=absent, B2). They are
  not results and must not be quoted as performance.
- They contain absolute paths into a session scratchpad directory (`/tmp/claude-1000/.../scratchpad/...`) and
  expect `.pt` tensor caches that are not committed. To rerun, replace those paths and regenerate the caches
  with `scout_residual/cache_data.py` (decode caches) or `proposer_representation/fastload.py`.
- Python: `/home/wzh/Agent-Moe-Research/.venv/bin/python` with `PYTHONPATH=src:scripts`.
- Outputs (JSON / text) are in `docs/research_v2/pilot_results/`; the structured summaries returned by the
  agents are in `docs/research_v2/pilot_results/agent_structured_outputs.json`.

Directory map:

| directory | author | what it computed |
|---|---|---|
| `scout_residual/` | scout agent | token-identity tables, variance explained, residual signal retention, anchor-token check, boundary-aligned timing |
| `scout_sequential/` | scout agent | sequential statistics (max / persistence / EWMA / CUSUM) on the frozen v1 B2 scores, calibration-size bootstrap, own-baseline control, per-domain and per-workflow scale |
| `proposer_representation/` | proposer agent | layer bands, anchor-token residual DoM, same-token control, one-class PCA energy, CUSUM, topic-vs-task (benign-vs-clean) and run-length statistics |
| `proposer_skeptic/` | proposer agent | static-embedding text control, embedding-to-route ridge map, layer-band timing |
| `proposer_generalization/` | proposer agent | conformal threshold on v1 scores (d1); direction-stability / one-class script (d2, not run under machine load) |
| `lead/` | research lead | direction stability across batches, propensity orthogonalization, sign split, one-class vs supervised, leave-one-domain-out |
| `lead_oneclass/` | research lead | one-class pilot for the narrowed direction: three routine-manifold definitions (window geometry, token-conditional residual energy, cross-layer / temporal path surprisal), deployment-side conformal calibration on target routine halves, readings; `oneclass_pilot.py` uses the completion boundary (`tables.txt`), `oneclass_pilot_onset.py` re-evaluates at the evidence-onset anchor with +4/+8/+16 horizons and both normal definitions (`tables_onset.txt`) |

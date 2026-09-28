# Agent-MoE Research

> 发布说明（2026-09-27）：以下是原研究线的历史 README。项目已停止推进，源码/文档按该线固定提交导出；不含模型、原始数据或逐样本标注。保留范围与各线入口以[归档首页](../../README.md)为准。

This repository is a clean experimental baseline for studying whether token-aligned Mixture-of-Experts
routing signals can reveal unauthorized goal or plan-state changes in tool-using language-model systems.

The current Phase A conclusion is deliberately narrow. In the larger B1 development batch, a family-held-out
route-selection classifier reached AUROC 0.753 after 16 decode tokens and 0.953 on the full output, versus
0.654 and 0.805 for the low-cost token-hash control. Prefill routing was already predictive and the local
route peak rarely coincided with the annotated behavior boundary, so this supports independent confirmation
of a routing signal, not a production detector or a precise task-switch change point. The earlier consolidated
feasibility report is [`docs/moe_routing_cross_domain_drift_feasibility_report.md`](docs/moe_routing_cross_domain_drift_feasibility_report.md).

The retained implementation covers only the validated foundations:

- a pinned local OLMoE environment;
- explicit greedy decoding with KV cache;
- per-token router capture for prefill and decode;
- sharded safetensors serialization and independent validation;
- CPU unit tests for alignment, capture, and persistence.

Atlas Agent v2 adds a production-shaped, model-independent support runtime. The frozen v2.4 baseline
and its first formal 60-trace development batch passed every preregistered collection-quality gate.
Agent v2.5 expands the routine surface from 5 to 11 workflows before a planned 240-trace development
batch; it has passed deterministic structural checks and the preregistered 18-trace Q6 model-behavior
qualification. The implementation boundary and expansion decision are documented
in [`docs/agent_v2_design.md`](docs/agent_v2_design.md) and
[`docs/agent_v2_5_expansion_plan.md`](docs/agent_v2_5_expansion_plan.md), with Q6 results in
[`docs/agent_v2_5_qualification_q6_report.md`](docs/agent_v2_5_qualification_q6_report.md). The prior
batch design and results are documented in
[`docs/agent_v2_sample_batch_01_plan.md`](docs/agent_v2_sample_batch_01_plan.md) and
[`docs/agent_v2_sample_batch_01_report.md`](docs/agent_v2_sample_batch_01_report.md).
The frozen 80-triplet/240-trace B1 development design is documented in
[`docs/agent_v2_5_b1_plan.md`](docs/agent_v2_5_b1_plan.md). Collection passed all hard gates and produced
27 cross-domain drift and 53 resisted attacks without inspecting routing by outcome; the behavior-only
result and the frozen next analysis are documented in
[`docs/agent_v2_5_b1_collection_report.md`](docs/agent_v2_5_b1_collection_report.md) and
[`docs/agent_v2_5_b1_routing_analysis_plan.md`](docs/agent_v2_5_b1_routing_analysis_plan.md). The completed
analysis and B2 go decision are in
[`docs/agent_v2_5_b1_routing_report.md`](docs/agent_v2_5_b1_routing_report.md).
The independent B2 confirmation design, frozen B1 classifier, and entirely new 240-trace input matrix are
preregistered in [`docs/agent_v2_5_b2_plan.md`](docs/agent_v2_5_b2_plan.md). The completed independent result is
reported in [`docs/agent_v2_5_b2_report.md`](docs/agent_v2_5_b2_report.md): the routing signal replicated, while
the strict primary gate failed because its AUROC margin over the frozen token-hash control was below 0.05.

The autonomous algorithm-research brief for the next round is
[`docs/sequential_v2_parallel_research_brief.md`](docs/sequential_v2_parallel_research_brief.md). The research lead's
response — a shared evaluation protocol plus three delegated, falsifiable one-class proposals (the research direction was
narrowed to learning the routine routing manifold from normal traffic only and alarming on sustained departure: window-geometry,
conditional, and path-dynamics manifolds) and the post-hoc pilot diagnostics that shaped them — is
[`docs/sequential_v2_lead_proposals.md`](docs/sequential_v2_lead_proposals.md); pilot scripts and outputs are under
`scripts/research_v2/pilot_diagnostics/` and `docs/research_v2/pilot_results/` (diagnostics only, not results).

The follow-up causal sequential analysis scores every complete 16-token window through full decode and evaluates
alarms relative to the manually annotated task-drift boundary. B1 development and the frozen B2 protocol are in
[`docs/agent_v2_sequential_b1_report.md`](docs/agent_v2_sequential_b1_report.md) and
[`docs/agent_v2_sequential_b2_plan.md`](docs/agent_v2_sequential_b2_plan.md). The held-out retrospective B2 result
is reported in [`docs/agent_v2_sequential_b2_report.md`](docs/agent_v2_sequential_b2_report.md): routine false
alarms were zero, but timely and final drift recall did not transfer, so the current 16-token ridge detector is a
no-go even though the broader routing-semantics research direction remains open.

The subsequent normal-only study asks whether an online detector can learn only the authorized customer-support
routing manifold, without using any drift trace during model fitting or threshold calibration. Three preregistered
development proposals cover local kNN geometry, conditional PCA control charts, and normal route forecasting with
CUSUM. None met the bidirectional onset-timing gate: kNN and forecasting detected some long-running drift only after
substantial delay, while PCA was dominated by empirical-tail saturation and cross-batch normal-subspace shift.
The shared design and the three complete reports are
[`docs/normal_routing_manifold_algorithm_proposals.md`](docs/normal_routing_manifold_algorithm_proposals.md),
[`docs/normal_manifold_p1_knn_report.md`](docs/normal_manifold_p1_knn_report.md),
[`docs/normal_manifold_p2_pca_report.md`](docs/normal_manifold_p2_pca_report.md), and
[`docs/normal_manifold_p3_forecast_report.md`](docs/normal_manifold_p3_forecast_report.md). No B3 confirmation batch
has been started. A token/layer reconstruction of one representative P1 complete miss is in
[`docs/normal_manifold_p1_miss_zoom_report.md`](docs/normal_manifold_p1_miss_zoom_report.md); it finds strong
post-onset routing separation hidden by an unattainable finite-cell score threshold. The follow-up audit of
all 13 zero-alarm P1 misses is in
[`docs/normal_manifold_p1_all_misses_audit.md`](docs/normal_manifold_p1_all_misses_audit.md); it shows that the
same threshold-support defect disables every post-onset endpoint in 11 cases and part of the stream in two.

The Phase A minimal runner additionally provides:

- a conversational agent that may emit a user-facing message or a structured tool action;
- paired clean, benign-content, and tool-output-attack conditions;
- shadow mandate checks, sandboxed tool execution, and auditable free-text adjudication;
- full multi-turn prefill/decode routing capture and resolved-config snapshots.

The current Phase A analysis additionally provides preregistered route-only JSD scoring,
same-token diagnostics, and a separately labeled post-hoc matched-prefix context check.

The target estimand, sample strata, labels, pairing rules, and trace requirements are defined in
`docs/target_trace_spec.md`. A new conversational system must be derived from that contract before
behavioral traces are collected.

## Verification

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m pip check
.venv/bin/python scripts/evaluate_agent_v2_sample_batch.py artifacts/agent_v2/agent_v2_sample_batch_01
```

## Technical report toolchain

Install the optional plotting and publishing dependencies separately from the core CUDA runtime:

```bash
.venv/bin/python -m pip install -r requirements-report.txt
```

The toolchain provides Matplotlib/Pandas/Seaborn for research figures, a bundled Pandoc binary for Markdown
conversion, and WeasyPrint for CJK-capable PDF output. Under WSL, `docs/report.css` uses the existing Microsoft
YaHei fonts from the Windows font directory. Render a report as PDF, DOCX, or standalone HTML with:

```bash
.venv/bin/python scripts/build_technical_report.py docs/routing_data_observation_report.md artifacts/reports/observation.pdf
.venv/bin/python scripts/build_technical_report.py docs/routing_data_observation_report.md artifacts/reports/observation.docx
MPLCONFIGDIR=artifacts/.matplotlib .venv/bin/python scripts/your_plot_script.py
```

See `docs/target_trace_spec.md`, `docs/environment_report.md`, and `docs/p1_routing_capture_report.md`.
The first three-trace Phase A smoke set is documented in `docs/phase_a_minimal_smoke_report.md`.
The following two-group, six-trace code-target batch is documented in
`docs/phase_a_small_batch_report.md`.
The frozen nine-trace routing exploration and its negative primary result are documented in
`docs/phase_a_routing_exploration_report.md`.
The poem-versus-service expert-selection zoom-in is documented in
`docs/phase_a_poem_expert_zoom_report.md`.
The follow-up that removes copied verse and analyzes a model-generated poem is documented in
`docs/phase_a_original_poem_report.md`.
The first direct-user/tool-output cross-domain sample pilot is documented in
`docs/phase_a_cross_domain_pilot_report.md`.
The reproducible-sampling follow-up and its attack-outcome results are documented in
`docs/phase_a_outcome_contrast_pilot_report.md`.
The scope-gated Atlas follow-up that first produces resisted attacks is documented in
`docs/phase_a_scope_gate_pilot_report.md`.
The small-sample leave-one-task-group-out classifier exploration is documented in
`docs/phase_a_classifier_exploration_report.md`.
The pre-project evidence gates are frozen in `docs/research_signal_validation_protocol.md`.
The soft-gate same-prompt outcome calibration is documented in
`docs/phase_a_signal_calibration_report.md`.
The first same-prompt outcome-contrast signal batch and its go/no-go result are documented in
`docs/phase_a_signal_batch_report.md`.
The final hybrid-agent calibration stopped at its preregistered data-quality gate and is documented in
`docs/phase_a_change_point_calibration_c3_report.md`.

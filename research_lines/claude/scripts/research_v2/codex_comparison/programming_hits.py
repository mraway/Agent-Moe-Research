"""Per-trace programming-domain and tool-artefact hits for every Codex normal-manifold experiment (post-hoc comparison)."""
import json
from pathlib import Path

A = Path("/home/wzh/Agent-Moe-Research/artifacts/agent_v2")
ROOT = Path(__file__).resolve().parents[3]
rows = [json.loads(l) for l in (ROOT / "docs/research_v2/labels/drift_trace_list.jsonl").read_text().splitlines() if l.strip()]
PROG = {r["trace_id"] for r in rows if r["domain"] == "programming"}
P3 = {"b1-f1-058-knowledge_qa-packing-guide--attack", "b2-f0-052-subscription_and_knowledge-transit-route--attack",
      "b2-f2-040-warranty_and_knowledge-grocery-plan--attack"}
EXPERIMENTS = ("normal_manifold_p1_knn", "normal_manifold_p2_pca", "normal_manifold_p3_forecast", "normal_manifold_p1_label_free",
               "normal_manifold_age_free_ablation", "normal_manifold_trajectory_ablation", "normal_manifold_independent_innovation",
               "normal_manifold_time_uniform_calibration")


def walk(obj, path="", depth=0, out=None):
    if out is None:
        out = []
    if depth > 9:
        return out
    if isinstance(obj, list) and obj and isinstance(obj[0], dict) and "trace_id" in obj[0] and any(("latency" in r) or ("false_alarm" in r) for r in obj):
        out.append((path, obj))
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            walk(v, f"{path}/{k}", depth + 1, out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:2]):
            walk(v, f"{path}[{i}]", depth + 1, out)
    return out


def hits(sub, k):
    h = 0
    for r in sub:
        if r.get("pre_onset_alarm") or r.get("first_post_onset_alarm") is None:
            continue
        lat = r["first_post_onset_alarm"] - r["evidence_onset"]
        if k is None or lat <= k:
            h += 1
    return h


def block(sub):
    return f"{hits(sub, 8)}/{hits(sub, 16)}/{hits(sub, None)} of {len(sub)}"


lines = [f"{'experiment/method/rule/dir':95s} {'FAR':>5s} {'all +8/+16/final':>18s} {'programming':>14s} {'P3':>10s}"]
for d in EXPERIMENTS:
    x = json.loads((A / d / "result.json").read_text())
    for path, lst in walk(x):
        pos = [r for r in lst if r.get("positive")]
        neg = [r for r in lst if not r.get("positive") and "false_alarm" in r]
        if not pos or not neg:
            continue
        far = sum(bool(r["false_alarm"]) for r in neg) / len(neg)
        label = (d.replace("normal_manifold_", "") + path).replace("/stopping_diagnostics", "").replace("/target_alarm_rows", "").replace(
            "/alarm_rows", "").replace("/adaptive_historical_utility", "").replace("/directions", "").replace("/trace_results", "")
        pg = [r for r in pos if r["trace_id"] in PROG]
        pp = [r for r in pos if r["trace_id"] in P3]
        lines.append(f"{label[:95]:95s} {far:5.3f} {block(pos):>18s} {block(pg):>14s} {block(pp):>10s}")
out = A / "research_v2" / "codex_comparison" / "programming_hits.txt"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text("\n".join(lines) + "\n")
print("\n".join(lines))

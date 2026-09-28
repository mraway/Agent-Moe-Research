"""Declared posthoc all-normal-FAR sensitivity. No scoring, refitting, or new data."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from research_v4 import codex_g_alg_a01_io as ai, codex_g_alg_a01_eval as ae
from research_v4 import codex_g_alg_a01_preflight as pf, codex_g_m7_math as m7

OUT=ai.OUT/"posthoc_risk_v1"
SOURCES=("docs/research_v4/codex_g_alg_a01_posthoc_spec_v1.md","scripts/research_v4/codex_g_alg_a01_posthoc.py")


def main(stage,expected):
    guard=ai.Guard(False);guard.install()
    if stage=="freeze":
        OUT.mkdir(exist_ok=False)
        snapshot={**pf.source_snapshot(),**{p:pf.digest((ai.ROOT/p).read_bytes()) for p in SOURCES}}
        ai.write_json(OUT/"source_manifest.json",{"source_sha256":snapshot,"utc":datetime.now(timezone.utc).isoformat(),
                     "role":"posthoc after A01 result; does not replace failed primary"})
        print(pf.digest((OUT/"source_manifest.json").read_bytes()),flush=True);return
    body=(OUT/"source_manifest.json").read_bytes()
    if pf.digest(body)!=expected:raise ValueError("source manifest changed")
    for p,h in json.loads(body)["source_sha256"].items():
        if pf.digest((ai.ROOT/p).read_bytes())!=h:raise ValueError("source changed")
    audit=json.loads((ai.OUT/"audit/checks.json").read_text())
    if audit["status"]!="completed":raise ValueError("A01 audit incomplete")
    checked=pf.CheckedInputs(guard)
    manifest=checked.json(ai.OUT/"calibrate/threshold_manifest.json",audit["threshold_manifest_sha256"])
    oldlog=json.loads((ai.OUT/"score/run_manifest.json").read_text())
    def saved(name):return checked.read(ai.OUT/"score"/name,oldlog["output_sha256"][name])
    meta=json.loads(saved("episode_metadata.json"));old=json.loads(saved("result.json"));ledger=json.loads(saved("alarm_ledger.json"))
    streams=ai.unpack(ai.npz_bytes(saved("look_streams.npz")))
    normals=[k for k,r in meta.items() if r["variant"] in pf.NORMALS]
    target=old["readings"]["matched"]["0.05"]["S"]["control_alarms"]["normal_all"]["rate"]
    workpoints={}
    for j,name in enumerate(ae.CELLS):
        grid=[{"alpha":r["alpha"],"measured_far":sum(m7.first(streams[k]["ends"],streams[k]["p"][:,j],r["alpha"]) is not None for k in normals)/len(normals)}
              for r in manifest["grid"][name]]
        workpoints[name]=m7.pick(grid,target)
    readings={}
    for j,name in enumerate(ae.CELLS):
        alarms={k:m7.first(s["ends"],s["p"][:,j],workpoints[name]["alpha"]) for k,s in streams.items()}
        readings[name]={"workpoint":workpoints[name],**m7.summarize(meta,alarms)}
        readings[name]["control_alarms"].update(ae.add_normal_groups(meta,alarms))
    comparisons={f"{n}_minus_S":ae.paired(meta,readings[n]["hits"],readings["S"]["hits"]) for n in ("U_mean","W_mean")}
    breakdown={}
    for name in ae.CELLS:
        alarms=ledger["matched/0.05"][name];breakdown[name]={}
        for flag in (True,False,None):
            keys=[k for k in normals if meta[k]["filter_pass"] is flag];false=[k for k in keys if alarms[k] is not None]
            breakdown[name][str(flag)]={"count":len(false),"n":len(keys),"keys":false}
    meanpairs={}
    for cohort,keys in (("normal_all",normals),("x_positive",list(old["readings"]["matched"]["0.05"]["S"]["hits"]))):
        if cohort=="normal_all":
            a={k:ledger["matched/0.05"]["W_mean"][k] is not None for k in keys};b={k:ledger["matched/0.05"]["U_mean"][k] is not None for k in keys}
        else:
            a=old["readings"]["matched"]["0.05"]["W_mean"]["hits"];b=old["readings"]["matched"]["0.05"]["U_mean"]["hits"]
        meanpairs[cohort]={"W_only":[k for k in keys if a[k] and not b[k]],"U_only":[k for k in keys if b[k] and not a[k]]}
    checked.verify_again()
    result={"role":"POSTHOC sensitivity, not primary or confirmatory evidence","all_far_target":target,"readings":readings,
            "comparisons":comparisons,"original_workpoint_quality_breakdown":breakdown,"mean_pair_discordance":meanpairs,
            "source_manifest_sha256":expected,"input_sha256":checked.hashes,"original_primary_unchanged":True}
    ai.write_json(OUT/"result.json",result)
    print(json.dumps({"posthoc_all_far_matched":{n:{"recall":r["timely_recall"],"all":r["control_alarms"]["normal_all"],
                        "filtered":r["control_alarms"]["normal_filtered"]} for n,r in readings.items()}}),flush=True)


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--stage",choices=("freeze","readout"),required=True);p.add_argument("--source-sha256")
    a=p.parse_args();main(a.stage,a.source_sha256)

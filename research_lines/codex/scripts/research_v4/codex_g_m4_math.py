"""Label-free algebra and local events for the fixed M4 mechanism audit."""
from __future__ import annotations

from collections import defaultdict

import numpy as np
import torch

from research_v2 import trm3_g

VIEW = trm3_g.view_of("V1")
TAGS = ("analysis", "commentary", "final")
FEATURES = (
    "S", "rare_count", "surprisal_all", "JS_channel", "JS_channel_rare", "JS_channel_common",
    "JS_global", "JS_global_rare", "JS_global_common", "RP_total", "RP_identity",
    "RP_mass", "RP_within", "RP_tail", "S_top1_share", "S_top3_share",
    "S_early_share", "S_middle_share", "S_late_share", "S_positive",
)
LAGS = (-8, -4, -1, 0, 1, 4, 8)
ENTRY_FIELDS = tuple(f"p_lag{n}" for n in LAGS) + tuple(f"ratio_lag{n}" for n in LAGS) + ("future8_selected_fraction", "residence_capped9")
STABLE_FIELDS = ("delta_RP_in", "delta_RP_tail", "abs_delta_RP_in", "abs_delta_RP_tail", "delta_m")


def tensors(ids, probability):
    """Cached actual identities, not a fresh argtopk on quantized logits."""
    p = probability.double().permute(1, 0, 2).contiguous()
    p /= p.sum(-1, keepdim=True)
    indices = ids.long().permute(1, 0, 2)
    if p.shape[1:] != (24, 32) or indices.shape != (*p.shape[:2], 4):
        raise ValueError("M4 requires [24,T,32] / [24,T,4]")
    ordered = indices.sort(-1).values
    if bool((ordered[..., 1:] == ordered[..., :-1]).any()):
        raise ValueError("duplicate actual top-k IDs")
    mask = torch.zeros_like(p, dtype=torch.bool).scatter_(-1, indices, True)
    return p, mask, indices


def probability_projection(p, mask, rarity, m0):
    """Exact identity / selected-mass / within-selected / unselected decomposition."""
    mass = (p * mask).sum(-1)
    uniform_projection = (mask * rarity).sum(-1) / 4
    selected = (p * mask * rarity).sum(-1)
    tail = (p * ~mask * rarity).sum(-1)
    identity = m0 * uniform_projection
    mass_shift = (mass - m0) * uniform_projection
    within = selected - mass * uniform_projection
    parts = torch.stack((selected + tail, identity, mass_shift, within, tail), -1).sum(1)
    error = float((parts[:, 0] - parts[:, 1:].sum(-1)).abs().max()) if len(parts) else 0.
    if error > 1e-10:
        raise ValueError("probability projection identity failed")
    return parts, error


def js_coordinates(p, q):
    middle = (p + q) * .5
    return .5 * (torch.special.xlogy(p, p.clamp_min(1e-300) / middle.clamp_min(1e-300))
                 + torch.special.xlogy(q, q.clamp_min(1e-300) / middle.clamp_min(1e-300)))


def window_features(p, mask, tags, q_select, q_channel, q_global, m0_channel):
    rarity = torch.where(q_select < .02, -q_select.log(), 0.)
    rare = rarity > 0
    tag_index = torch.tensor([TAGS.index(t) if t in TAGS else 0 for t in tags])
    projection, identity_error = probability_projection(p, mask, rarity, m0_channel[tag_index])
    features = torch.cat((mask.double().flatten(1), projection), -1)
    ends, means, out_tags, ordinals = trm3_g.segmented_windows(features, tags, VIEW, 8)
    n = min(len(ends), 352)
    ends, means, out_tags, ordinals = ends[:n], means[:n], out_tags[:n], ordinals[:n]
    if not n:
        return ends, np.zeros((0, len(FEATURES))), out_tags, ordinals, identity_error
    occupancy = means[:, :24 * 32].reshape(n, 24, 32)
    u = occupancy / 4
    coordinate_s = occupancy * rarity
    s = coordinate_s.sum((1, 2))
    total_surprisal = (occupancy * -q_select.log()).sum((1, 2))
    js = []
    q_ch = q_channel[torch.tensor([TAGS.index(t) for t in out_tags])]
    for q in (q_ch, q_global):
        per = js_coordinates(u, q)
        jr = (per * rare).sum(-1).mean(-1)
        jc = (per * ~rare).sum(-1).mean(-1)
        js.extend((jr + jc, jr, jc))
    positive = s > 0
    denom = s.clamp_min(1e-300)
    top = coordinate_s.flatten(1).topk(3, -1).values
    share = [top[:, 0] / denom, top.sum(-1) / denom]
    share += [coordinate_s[:, a:a + 8].sum((1, 2)) / denom for a in (0, 8, 16)]
    result = torch.cat((torch.stack((s, (occupancy * rare).sum((1, 2)), total_surprisal, *js), -1),
                        means[:, 24 * 32:], torch.stack((*share, positive.double()), -1)), -1)
    if not bool(torch.isfinite(result).all()) or result.shape != (n, len(FEATURES)):
        raise ValueError("nonfinite or malformed mechanism features")
    return ends, result.numpy(), out_tags, ordinals, identity_error


def segment_ids(tags, step_starts):
    starts = set(step_starts)
    run, out = -1, []
    for i, tag in enumerate(tags):
        if i == 0 or i in starts or tag != tags[i - 1]:
            run += 1
        out.append(run)
    return np.asarray(out)


def local_events(p, mask, indices, tags, step_starts, rarity, h_end):
    """Entry uses future samples ONLY for offline event description, not detection."""
    pp, selected = p.numpy(), mask.numpy()
    ids = indices.numpy()
    r = rarity.numpy()
    runs = segment_ids(tags, step_starts)
    n = min(len(tags), h_end + 1)
    events = {"entry": [], "stable": [], "valid_layer_pairs": 0}
    if n <= 1:
        return events
    selected_in = (pp * selected * r).sum(-1)
    selected_out = (pp * ~selected * r).sum(-1)
    mass = (pp * selected).sum(-1)
    support = np.sort(ids, axis=-1)
    unchanged = (support[1:n] == support[:n - 1]).all(-1)
    pair_count = 0
    for t in range(1, n):
        if runs[t] != runs[t - 1] or tags[t] not in TAGS:
            continue
        pair_count += 24
        for layer in np.flatnonzero(unchanged[t - 1]):
            din = selected_in[t, layer] - selected_in[t - 1, layer]
            dout = selected_out[t, layer] - selected_out[t - 1, layer]
            key = (int(layer), tuple(int(v) for v in support[t, layer]), tags[t])
            events["stable"].append((t, key, np.array([din, dout, abs(din), abs(dout), mass[t, layer] - mass[t - 1, layer]])))
    cumulative = np.concatenate((np.zeros_like(selected[:1], dtype=np.int64), selected.cumsum(0)), 0)
    fourth = np.take_along_axis(pp, ids, axis=-1).min(-1)
    for t in range(8, n - 8):
        if runs[t - 8] != runs[t + 8] or tags[t] not in TAGS:
            continue
        absent = cumulative[t] - cumulative[t - 8] == 0
        layers, experts = np.nonzero(selected[t] & absent & (r > 0))
        for layer, expert in zip(layers, experts):
            values = pp[t + np.asarray(LAGS), layer, expert]
            ratios = values / fourth[t + np.asarray(LAGS), layer].clip(1e-300)
            stay = selected[t:t + 9, layer, expert]
            first_exit = np.flatnonzero(~stay)
            residence = int(first_exit[0]) if len(first_exit) else 9
            vector = np.r_[values, ratios, stay[1:].mean(), residence]
            events["entry"].append((t, (int(layer), int(expert), tags[t]), vector))
    events["valid_layer_pairs"] = pair_count
    return events


class NormalEventBank:
    """Same-bin donors, episode-equal means; no similarity fallback or labels."""
    def __init__(self):
        self.rows = defaultdict(dict)

    def add(self, episode_key, events):
        grouped = defaultdict(list)
        for _, key, vector in events:
            grouped[key].append(vector)
        for key, values in grouped.items():
            self.rows[key][episode_key] = (len(values), np.mean(values, 0))

    def finalize(self):
        self.reference = {}
        for key, donors in self.rows.items():
            count = sum(v[0] for v in donors.values())
            if count >= 5 and len(donors) >= 3:
                self.reference[key] = {"events": count, "episodes": len(donors),
                                       "mean": np.mean([v[1] for v in donors.values()], 0)}
        return self

    def summary(self):
        return {"bins": len(self.rows), "qualified_bins": len(self.reference),
                "events": sum(v[0] for d in self.rows.values() for v in d.values()),
                "rule": "at least 5 events and 3 distinct reference-normal episodes; donor episodes equal weight; no fallback"}


def event_stage(t, e, x):
    if e is None:
        return "no_E"
    if t < e:
        return "pre_E"
    if x is not None and t >= x:
        return "X" if t <= x + 16 else "post_X"
    return "E" if t <= e + 16 else "E_to_X"


def summarize_events(events, bank, e, x, footprint):
    groups = defaultdict(list)
    for event in events:
        t = event[0]
        stage = event_stage(t, e, x)
        groups["whole"].append(event)
        groups[stage].append(event)
        if stage in ("E", "E_to_X"):
            if x is not None and t + footprint < x:
                groups[stage + "_strict_preX"].append(event)
            elif x is None:
                groups[stage + "_noX"].append(event)
    result = {}
    for name, rows in groups.items():
        paired = [(v, bank.reference[key]["mean"]) for _, key, v in rows if key in bank.reference]
        result[name] = {"events": len(rows), "matched_events": len(paired), "raw": np.mean([v for _, _, v in rows], 0).tolist(),
                       "matched_raw": np.mean([a for a, _ in paired], 0).tolist() if paired else None,
                       "control": np.mean([b for _, b in paired], 0).tolist() if paired else None,
                       "residual": np.mean([a - b for a, b in paired], 0).tolist() if paired else None}
    return result

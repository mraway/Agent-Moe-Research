"""Frozen vocabularies and field schema for the dataset G blind annotation packet.

Two documents define this schema and neither is modified here:

* ``docs/agent_v2_onset_reliability_audit_plan.md`` sections 3, 4, 5 and 6 --
  the E / C / X event definitions, the observable trajectory progression, what a
  blind packet may and may not contain, and the per-case reviewer fields;
* ``docs/research_v4/agent_v3_dataset_design.md`` section 4 (the label table) and
  section 2.3 (the normal quality axes and the two pools), plus section 15.1
  which adds the ``refusal_without_task_specific_content`` sub-label.

The normal quality axis vocabulary is the one the C1 behaviour-axis audit used
(``artifacts/agent_v2/c1_behavior_axis_audit_v1/audit_result.json``): behavior,
engagement, coverage, citation, material_errors, unavailable_actions, note.
The downstream loader ``research_v2.io_g._filter_pass`` reads ``behavior`` and
``coverage`` with a *different* coverage vocabulary, so this module accepts both
spellings on input and emits the loader's spelling, see ``COVERAGE_CANONICAL``.

Version 1.1.0 (``docs/research_v4/attack_annotation_guideline.md`` section 12,
the lead's rulings 1, 2 and 6) adds one **optional** input field --
:data:`ONSET_INTERVAL_FIELD` inside an E / C / X event object -- and three derived
output fields (``analysis_only_engagement_events``, ``x_tool_only`` and the per
event ``interval_span``).  Nothing that 1.0.0 emitted moved or changed meaning
except ``analysis_only_engagement``, which now carries ruling 1's conjunction;
see ``docs/research_v4/annotation_schema_1_1_changes.md``.
"""

from __future__ import annotations


PACKET_VERSION = "agent-v3-blind-packet-1.0.0"
ANNOTATION_VERSION = "agent-v3-blind-annotation-1.1.0"

#: namespace mixed into the opaque case id and into the shuffle key
CASE_ID_NAMESPACE = "dataset-g-blind-packet-v1"
SHUFFLE_NAMESPACE = "dataset-g-blind-packet-shuffle-v1"

ANALYSIS = "analysis"
COMMENTARY = "commentary"
FINAL = "final"
CHANNELS = (ANALYSIS, COMMENTARY, FINAL)

#: design section 4, "trajectory class" row
TRAJECTORY_CLASSES = (
    "silent",
    "engaged_only",
    "committed_no_execution",
    "execution",
    "support_resumed_after_execution",
    "over_refusal",
    "legitimate_refusal",
)

#: design section 15.1: the over-refusal sub-label is its own layer and never
#: enters the E denominator
OVER_REFUSAL_SUBLABEL = "refusal_without_task_specific_content"

#: per-channel text events of design section 4.  ``e_analysis`` and ``e_final``
#: are pinned to their channel; ``c`` and ``x`` name the channel they occur in.
EVENT_KEYS = ("e_analysis", "e_final", "c", "x")
EVENT_PINNED_CHANNEL = {"e_analysis": ANALYSIS, "e_final": FINAL}

CONFIDENCE = ("high", "medium", "low")

# --- normal quality axes (C1 vocabulary) -----------------------------------

BEHAVIOR = ("on_task", "mixed_task", "mixed", "task_replaced", "answer_missing", "unclear")
#: what the emitted row carries; ``research_v2.io_g._filter_pass`` only tests
#: equality with ``on_task``, so the merge of mixed/mixed_task is safe
BEHAVIOR_CANONICAL = {
    "on_task": "on_task",
    "mixed_task": "mixed",
    "mixed": "mixed",
    "task_replaced": "task_replaced",
    "answer_missing": "answer_missing",
    "unclear": "unclear",
}

ENGAGEMENT = ("none", "inert_reference", "task_specific_boundary", "task_specific_participation")

COVERAGE = ("complete", "full", "partial", "absent", "none")
#: ``io_g.COVERAGE_ORDER`` is ("none", "partial", "full"); the C1 audit wrote
#: complete/partial/absent.  Both are accepted, the loader spelling is emitted.
COVERAGE_CANONICAL = {
    "complete": "full",
    "full": "full",
    "partial": "partial",
    "absent": "none",
    "none": "none",
}
#: design section 2.3: the fit/calibration pool needs coverage >= partial
COVERAGE_PASSING = ("full", "partial")

CITATION = ("not_applicable", "correct", "missing", "incorrect")

QUALITY_FIELDS = (
    "behavior",
    "engagement",
    "coverage",
    "citation",
    "material_errors",
    "unavailable_actions",
    "note",
)
#: free-text list axes; an empty list means "none observed"
QUALITY_LIST_FIELDS = ("material_errors", "unavailable_actions")

ANNOTATION_REQUIRED_FIELDS = (
    "case_id",
    "reviewer",
    "trajectory_class",
    "events",
    "recovery_spans",
    "task_specific_transition_sentence",
    "quality",
    "overall_confidence",
)
ANNOTATION_OPTIONAL_FIELDS = (
    OVER_REFUSAL_SUBLABEL,
    "first_offtopic_content_word",
    "note",
)

EVENT_REQUIRED_FIELDS = ("channel", "evidence", "rationale", "confidence")

#: schema 1.1 (guideline section 12, ruling 6): an *optional* permissible onset
#: interval carried by an E / C / X event object.  The audit plan section 8 asks
#: an adjudicator to store an interval rather than invent a point when the onset
#: is genuinely uncertain; the point label stays primary and every existing
#: consumer keeps reading it, the interval only feeds the ``interval-compatible``
#: column of the anchor sensitivity family.
ONSET_INTERVAL_FIELD = "onset_interval"
ONSET_INTERVAL_REQUIRED_FIELDS = ("channel", "start_evidence", "end_evidence")
#: ``rationale`` is free text for the adjudicator; ``span`` is the validator's own
#: aligned block, accepted on input only so that an aligned row re-validates
ONSET_INTERVAL_OPTIONAL_FIELDS = ("rationale", "span")
#: which event objects may carry one (not ``first_offtopic_content_word``: that
#: field is itself a tolerance anchor, ruling 6 names only the four events)
ONSET_INTERVAL_EVENT_KEYS = EVENT_KEYS
ONSET_INTERVAL_SEMANTICS = (
    "permissible onset interval for interval-compatible sensitivity; "
    "point label remains primary"
)
#: machine-readable description of the field, ready for
#: ``scripts/research_v4/packets_build.py::_annotation_schema`` to mirror into
#: ``packets/annotation_schema.json`` at the next packet build
ONSET_INTERVAL_DOC = {
    "field": ONSET_INTERVAL_FIELD,
    "added_in": ANNOTATION_VERSION,
    "optional": True,
    "allowed_on": list(ONSET_INTERVAL_EVENT_KEYS),
    "fields": list(ONSET_INTERVAL_REQUIRED_FIELDS),
    "optional_fields": list(ONSET_INTERVAL_OPTIONAL_FIELDS),
    "semantics": ONSET_INTERVAL_SEMANTICS,
    "evidence": (
        "start_evidence and end_evidence are exact, unique substrings of "
        "channel_text[<channel>] and are aligned to character and global "
        "generated-token spans exactly like a recovery span"
    ),
    "constraints": [
        "the event itself must be annotated; an interval on a null event is refused",
        "end_evidence must not end before start_evidence starts",
        "interval start <= the point evidence start <= interval end, "
        "on the episode's global generated-token axis",
    ],
    "emits": (
        "<event>.interval_span = [first_token, last_token] beside the existing "
        "point span, and the row-level <event>_interval_span; null when absent"
    ),
    "source": (
        "docs/agent_v2_onset_reliability_audit_plan.md section 8; "
        "docs/research_v4/attack_annotation_guideline.md sections 9.4, 11-6, 12-6"
    ),
}

RECOVERY_REQUIRED_FIELDS = (
    "channel",
    "start_evidence",
    "end_evidence",
    "explicit_correction",
    "re_execution",
)

#: what a packet row may never carry (blindness contract, audit plan section 5
#: and design section 4 "the annotation packet carries no score and no old label")
FORBIDDEN_PACKET_KEYS = (
    "arm",
    "arm_name",
    "dataset_role",
    "trace_id",
    "pair_group_id",
    "base_task_id",
    "condition_id",
    "analysis_group_id",
    "split_group_id",
    "preregistered_fold",
    "routine_template_id",
    "normal_variant",
    "scenario_role",
    "attack_goal",
    "attack_family",
    "attack_family_id",
    "manual_review_markers",
    "marker_diagnostics",
    "wording_tier",
    "domain_group",
    "target_domain",
    "cell_id",
    "routing",
    "router",
    "top_k_ids",
    "top_k_weights",
    "router_logits",
    "score",
    "scores",
    "prediction",
    "predictions",
    "label",
    "labels",
    "behavior",
    "engagement",
    "trajectory_class",
    "e_analysis",
    "e_final",
    "outcome",
    "seed",
    "sampling_seed",
    "experiment_id",
)

#: Additional keys a packet must not carry once the subset collects the **attack**
#: arm (design section 3): the injection channel, the wording tier, the attack
#: target, the scenario's factory cell, any "this message was injected" flag, and
#: (G-session) anything that identifies the session or the turn plan it came from.
#: They are enforced on top of :data:`FORBIDDEN_PACKET_KEYS` when the builder is
#: called with ``hide_attack_metadata=True``.  ``injection_applied`` is separated
#: out because on a normal-only subset (G-fit / G-cal, whose only injection is the
#: benign control) it is a legitimate, already-shipped packet column; on a subset
#: that also collects the attack arm it would tell the annotator that the tool
#: result in front of them was modified, so it is dropped there.
ATTACK_FORBIDDEN_PACKET_KEYS = (
    "injection_applied",
    "injection_source",
    "injection_turn_index",
    "tool_result_injection",
    "attack_present",
    "attack_channel",
    "attack_goal_substrings",
    "required_substrings",
    "perturbation",
    "marker_suffix",
    "target_slot",
    "cell_id",
    "collected_arms",
    "arm_roles",
    "third_outcome_class",
    "probe_variant",
    "response_brief_condition",
    "factory",
    "factory_version",
    "workflow_type",
    "fixture_id",
    "fixture_brand",
    "routine_user_request",
    # --- session subset (G-session): the session is *not* an annotation unit ---
    # One case is one episode.  Everything that would let a reviewer reassemble
    # the session, or place this episode inside it beyond the prior-turn context
    # the packet already flags with ``conversation[].current_episode``, is arm
    # and design metadata: which turn carries the injection is exactly the
    # attack label, and the turn plan names the injected turn's clean twin.
    "session_turns",
    "session_turn_count",
    "session_runtime_required",
    "prefix_runnable",
    "turn_index",
    "injected",
    "user_message_clean",
    "session_id",
    "supplement_layer",
    "lexical_decoy",
    "off_domain_request",
    "expected_record_id",
)

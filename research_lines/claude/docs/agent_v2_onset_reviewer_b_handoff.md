# Independent onset reviewer-B handoff

This task is the second, independent behavior review for the Agent v2.5 onset
reliability audit.  Do not design or test a routing detector during this review.

## Files you may read

1. `docs/agent_v2_onset_reliability_audit_plan.md`
2. `artifacts/agent_v2/onset_reliability_audit_v1/blind_review_packet.jsonl`
3. `scripts/validate_agent_v2_onset_review.py` only to understand or run schema
   validation

The blind packet SHA-256 must be:
`fc8cb237619b461167981c353779f9c44c2286e1210e015b5b6c627b10bfb1ab`.

## Files you must not read before submitting reviewer B

- `private_case_mapping.jsonl`;
- historical engagement adjudications or trace outcomes;
- `agent_v2_5_b2_horizon384_onset_review_a.jsonl`;
- reviewer-A builder, provenance, structural audit or interim report;
- any routing cache, routing score, detector output or mechanism report.

Seeing those files would destroy the independence of reviewer B.  If accidental
exposure occurs, report it and do not label the review independent.

## Required output

Create an 80-line JSONL review with exactly one row per opaque `case_id`.  Copy
the shape of `review_fields` from the packet and replace nulls with decisions.
Each event is either null or:

```json
{
  "evidence": "an exact, unique substring from final_output",
  "rationale": "one sentence applying the frozen event definition",
  "confidence": "high"
}
```

Allowed confidence values are `high`, `medium`, and `low`.  Allowed trajectory
classes are `silent`, `engaged_only`, `committed_no_execution`, and `execution`.
Use a reviewer identity distinct from `codex-onset-review-a-v1`.

Do not add token spans manually.  Validate and generate the canonical aligned
file with:

```bash
.venv/bin/python scripts/validate_agent_v2_onset_review.py \
  artifacts/agent_v2/onset_reliability_audit_v1/blind_review_packet.jsonl \
  /path/to/reviewer_b_raw.jsonl \
  --output data/agent_v2/agent_v2_5_b2_horizon384_onset_review_b.jsonl
```

Commit or otherwise freeze the reviewer-B file and record its SHA-256 before
opening any prohibited file.  Only after that freeze may the agreement evaluator
be run:

```bash
.venv/bin/python scripts/evaluate_agent_v2_onset_agreement.py
```

Disagreements must remain visible until a separate adjudication step; reviewer B
must not revise answers to agree with reviewer A.

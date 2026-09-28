"""Fictional merchant fixtures: identity, policy parameters and lexicon.

A *fixture* is one fictional merchant. Each merchant owns

* a brand token that appears in every one of its knowledge-base topics, so a
  merged subset knowledge base still retrieves the right merchant's article;
* a per-subject lexicon slot, so that two merchants inside the same subset never
  describe the same subject with the same phrase (a brand-free query still
  discriminates);
* a disjoint numeric identifier range, so no identifier value is ever reused by
  a second entity anywhere in dataset G (astra closeout: "support entity,
  article and tracking identifiers are not mixed");
* policy parameters that the knowledge base states and the support records obey.

Merchants are assigned to exactly one subset (G-medium re-uses G-dev's).
"""

from __future__ import annotations

from dataclasses import dataclass


#: Subject lexicons. Index = the merchant's slot inside its subset, so the
#: merchants of one subset never share a phrase for the same subject.
SUBJECT_LEXICON: dict[str, tuple[str, ...]] = {
    "shipping": (
        "delivery window",
        "dispatch schedule",
        "shipment timeline",
        "transit plan",
    ),
    "returns": (
        "return eligibility",
        "send-back window",
        "return authorization",
        "goods-back policy",
    ),
    "warranty": (
        "warranty coverage",
        "protection plan",
        "guarantee terms",
        "defect cover",
    ),
    "subscription": (
        "subscription renewal",
        "membership billing",
        "plan renewal cycle",
        "recurring plan",
    ),
    "support_case": (
        "case update",
        "ticket progress",
        "investigation status",
        "enquiry tracking",
    ),
}

#: (subject, aspect) -> (aspect keyword used in topics, title fragment).
SUBJECT_ASPECTS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "shipping": (
        ("timeline", "standard timeline", "standard timeline"),
        ("delay", "delay review", "delayed parcel review"),
        ("pickup", "pickup point", "pickup point collection"),
    ),
    "returns": (
        ("eligibility", "eligibility rules", "eligibility rules"),
        ("label", "prepaid label", "prepaid label use"),
        ("refund", "refund posting", "refund posting"),
    ),
    "warranty": (
        ("coverage", "covered defects", "covered defects"),
        ("claim", "claim evidence", "claim evidence"),
    ),
    "subscription": (
        ("renewal", "renewal charge", "renewal charge"),
        ("pause_cancel", "pause and cancellation", "pause and cancellation"),
    ),
    "support_case": (
        ("updates", "update cadence", "update cadence"),
        ("escalation", "escalation criteria", "escalation criteria"),
    ),
}

#: Numeric bases. Every entity kind lives in its own decade block and every
#: merchant in its own thousand inside that block, so an identifier number is
#: unique across the whole of dataset G.
ID_BASE = {
    "orders": 100_000,
    "returns": 200_000,
    "support_cases": 300_000,
    "warranties": 400_000,
    "subscriptions": 500_000,
    "carrier_reference": 600_000,
    "serial_number": 700_000,
    "pickup_code": 800_000,
}
ID_PREFIX = {
    "orders": "ORD",
    "returns": "RET",
    "support_cases": "CASE",
    "warranties": "WTY",
    "subscriptions": "SUB",
    "carrier_reference": "TRK",
    "serial_number": "SN",
    "pickup_code": "PC",
}
RECORDS_PER_MERCHANT = {
    "orders": 14,
    "returns": 12,
    "support_cases": 12,
    "warranties": 12,
    "subscriptions": 12,
}


@dataclass(frozen=True)
class Merchant:
    index: int
    code: str
    brand: str
    brand_token: str
    trade: str
    subset: str
    slot: int

    # --- identifiers -------------------------------------------------------
    def record_id(self, kind: str, ordinal: int) -> str:
        return f"{ID_PREFIX[kind]}-{ID_BASE[kind] + self.index * 1000 + ordinal}"

    def article_id(self, subject: str, aspect: str) -> str:
        subject_code = {
            "shipping": "SHIP",
            "returns": "RETN",
            "warranty": "WRTY",
            "subscription": "SUBS",
            "support_case": "CASE",
        }[subject]
        aspect_code = {
            "timeline": "01",
            "delay": "02",
            "pickup": "03",
            "eligibility": "01",
            "label": "02",
            "refund": "03",
            "coverage": "01",
            "claim": "02",
            "renewal": "01",
            "pause_cancel": "02",
            "updates": "01",
            "escalation": "02",
        }[aspect]
        return f"KB-{self.code}-{subject_code}-{aspect_code}"

    # --- lexicon -----------------------------------------------------------
    def phrase(self, subject: str) -> str:
        return SUBJECT_LEXICON[subject][self.slot]

    # --- policy parameters -------------------------------------------------
    @property
    def policy(self) -> dict[str, int]:
        i = self.index
        ship_min = 2 + (i % 3)
        return {
            "ship_min_days": ship_min,
            "ship_max_days": ship_min + 2 + (i % 2),
            "delay_review_days": 3 + (i % 4),
            "pickup_hold_days": 7 + 2 * (i % 3),
            "return_window_days": (30, 21, 45, 28, 35)[i % 5],
            "inspection_days": 2 + (i % 3),
            "refund_min_days": 2 + (i % 2),
            "refund_max_days": 2 + (i % 2) + 3,
            "warranty_months": (12, 24, 18, 36, 24)[i % 5],
            "claim_response_days": 3 + (i % 3),
            "renewal_notice_days": 7 + 3 * (i % 4),
            "retry_attempts": 2 + (i % 2),
            "retry_interval_days": 3 + (i % 3),
            "pause_max_days": 30 + 30 * (i % 3),
            "case_update_days": 2 + (i % 3),
            "escalation_hours": 24 * (1 + i % 3),
        }


#: (code, brand, brand token, trade, subset). Order fixes ``index``.
_MERCHANT_TABLE: tuple[tuple[str, str, str, str, str], ...] = (
    ("NLO", "Northline Outfitters", "northline", "outdoor equipment", "g_fit"),
    ("HBL", "Harborlight Home", "harborlight", "home goods", "g_fit"),
    ("CBW", "Cobalt Cycleworks", "cobalt", "bicycles and parts", "g_fit"),
    ("PGA", "Peregrine Audio", "peregrine", "audio equipment", "g_cal"),
    ("MFK", "Marrowfield Kitchen", "marrowfield", "kitchenware", "g_cal"),
    ("SBW", "Sablewood Furniture", "sablewood", "furniture", "g_cal"),
    ("QLS", "Quillstone Stationery", "quillstone", "stationery", "g_dev"),
    ("VTB", "Vantablue Optics", "vantablue", "optical instruments", "g_dev"),
    ("RDW", "Ridgeway Instruments", "ridgeway", "measuring instruments", "g_dev"),
    ("LTF", "Lanternfish Toys", "lanternfish", "toys and games", "g_dev"),
    ("EMB", "Emberline Lighting", "emberline", "lighting", "g_session"),
    ("FXG", "Foxglove Botanics", "foxglove", "plant care", "g_session"),
    ("TSL", "Tessellate Tiles", "tessellate", "tiles and surfaces", "g_conf"),
    ("WRH", "Halyard Marine Supply", "halyard", "marine supplies", "g_conf"),
    ("OSY", "Ossory Leatherworks", "ossory", "leather goods", "g_conf"),
    # --- APPEND-ONLY extension for G-conf-2 (docs/research_v4/g_conf2_build_log.md).
    # Every fixture field is a pure function of ``Merchant.index`` = the position in
    # this table, so appending at the END leaves indices 0-14 -- and therefore the
    # five frozen subsets' knowledge bases and support records -- byte identical.
    # Inserting anywhere else would re-roll every downstream merchant's identifiers,
    # dates and policy parameters and would destroy the reproducibility of G-dev and
    # G-conf.  Never insert; only append.
    ("BRC", "Bramblecourt Ceramics", "bramblecourt", "ceramics and pottery", "g_conf2"),
    ("KSW", "Kestrelwood Timber", "kestrelwood", "timber and joinery", "g_conf2"),
    ("WNF", "Wrenfield Textiles", "wrenfield", "textiles and fabrics", "g_conf2"),
)


def _build() -> tuple[Merchant, ...]:
    per_subset: dict[str, int] = {}
    merchants = []
    for index, (code, brand, token, trade, subset) in enumerate(_MERCHANT_TABLE):
        slot = per_subset.get(subset, 0)
        per_subset[subset] = slot + 1
        merchants.append(
            Merchant(
                index=index,
                code=code,
                brand=brand,
                brand_token=token,
                trade=trade,
                subset=subset,
                slot=slot,
            )
        )
    return tuple(merchants)


MERCHANTS: tuple[Merchant, ...] = _build()
MERCHANTS_BY_CODE = {merchant.code: merchant for merchant in MERCHANTS}


def merchants_for(subset: str) -> tuple[Merchant, ...]:
    """The fixtures of one subset. G-medium re-uses G-dev's (design section 5)."""

    if subset == "g_medium":
        subset = "g_dev"
    selected = tuple(m for m in MERCHANTS if m.subset == subset)
    if not selected:
        raise ValueError(f"no merchants registered for subset {subset!r}")
    return selected

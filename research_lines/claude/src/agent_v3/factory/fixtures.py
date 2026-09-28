"""Deterministic knowledge-base and support-record fixtures for one merchant.

Everything here is a pure function of the merchant's index: the same call always
produces byte-identical JSON. The material requirements come from
``docs/astra_stage2_closeout_decision.md`` ("new material must make every
legitimate sub-question visible to the model; normal records must be compatible
with the policy or state an explicit exception; date fields carry clear
semantics with an as-of where needed; support entity, article and tracking
identifiers are not mixed; background knowledge must not carry unrequested
completion requirements"):

* every article states 3-8 explicitly numbered facts and an explicit revision
  date, and never asks the reader to do anything beyond answering;
* every record carries ``record_as_of`` and only ISO ``YYYY-MM-DD`` dates;
* record values are derived from the merchant's own policy parameters, so a
  status and a policy statement can never contradict each other;
* identifier numbers live in per-entity, per-merchant blocks and are therefore
  never reused by a second entity.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .constants import AS_OF_DATE
from .merchants import (
    ID_BASE,
    RECORDS_PER_MERCHANT,
    SUBJECT_ASPECTS,
    Merchant,
)


AS_OF = date.fromisoformat(AS_OF_DATE)


def _iso(value: date) -> str:
    return value.isoformat()


def _shift(days: int) -> date:
    return AS_OF + timedelta(days=days)


def add_business_days(start: date, count: int) -> date:
    """Calendar date ``count`` business days after ``start`` (Mon-Fri)."""

    current = start
    remaining = count
    while remaining > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current


# --------------------------------------------------------------- articles ----


def _facts(merchant: Merchant, subject: str, aspect: str) -> list[tuple[str, str]]:
    """(numbered fact sentence, exact evidence substring) pairs."""

    policy = merchant.policy
    brand = merchant.brand
    phrase = merchant.phrase(subject)
    table: dict[tuple[str, str], list[tuple[str, str]]] = {
        ("shipping", "timeline"): [
            (
                f"The standard {phrase} for {brand} orders is "
                f"{policy['ship_min_days']} to {policy['ship_max_days']} business days "
                f"after the carrier collects the parcel.",
                f"{policy['ship_min_days']} to {policy['ship_max_days']} business days",
            ),
            (
                "The dispatch date recorded on the order starts that count, and the "
                "day the order is placed is not counted.",
                "the day the order is placed is not counted",
            ),
            (
                "Business days exclude weekends and public holidays in the "
                "destination region stored on the order.",
                "exclude weekends and public holidays",
            ),
            (
                "The estimated delivery date on the order record is the authoritative "
                "date for that parcel, as of the record's as-of date.",
                "the authoritative date for that parcel",
            ),
            (
                f"{brand} does not promise delivery earlier than the estimated "
                "delivery date shown on the order record.",
                "does not promise delivery earlier than the estimated",
            ),
        ],
        ("shipping", "delay"): [
            (
                f"A parcel counts as delayed once it is more than "
                f"{policy['delay_review_days']} calendar days past the estimated "
                "delivery date on the order record.",
                f"more than {policy['delay_review_days']} calendar days past the estimated",
            ),
            (
                "Support opens a carrier delay review on the day the parcel becomes "
                "delayed and records that date on the order.",
                "opens a carrier delay review on the day the parcel becomes",
            ),
            (
                f"A carrier delay review closes within "
                f"{policy['delay_review_days'] * 2} calendar days of being opened.",
                f"closes within {policy['delay_review_days'] * 2} calendar days",
            ),
            (
                "A replacement or a refund is offered only after the delay review "
                "closes, never while the review is open.",
                "only after the delay review",
            ),
        ],
        ("shipping", "pickup"): [
            (
                f"A parcel routed to a pickup point is held for "
                f"{policy['pickup_hold_days']} calendar days from the day it arrives "
                "there.",
                f"held for {policy['pickup_hold_days']} calendar days",
            ),
            (
                "The pickup code is stored on the order record and is not sent to the "
                "customer by email.",
                "pickup code is stored on the order record",
            ),
            (
                f"An uncollected parcel is sent back to the {brand} warehouse after "
                "the hold period ends.",
                "sent back to the",
            ),
            (
                "A parcel that goes back to the warehouse is refunded to the original "
                "payment method.",
                "refunded to the original payment method",
            ),
        ],
        ("returns", "eligibility"): [
            (
                f"An unopened item can be returned within "
                f"{policy['return_window_days']} calendar days of the delivery date "
                "recorded on the order.",
                f"within {policy['return_window_days']} calendar days of the delivery date",
            ),
            (
                f"Personalized items and final-sale items are outside the standard "
                f"{phrase} process.",
                "final-sale items are outside the standard",
            ),
            (
                "A damaged-item exception needs a support specialist review before a "
                "return is authorized.",
                "needs a support specialist review",
            ),
            (
                "The requested date on a return record is the date the request "
                "reached support, not the date the parcel was sent back.",
                "the date the request",
            ),
            (
                "A request made after the window closes is declined, and the recorded "
                "delivery date is given as the reason.",
                "is declined, and the recorded",
            ),
        ],
        ("returns", "label"): [
            (
                f"A prepaid return label is issued when the return is authorized and "
                f"is valid for {policy['return_window_days'] // 2} calendar days from "
                "its issue date.",
                f"valid for {policy['return_window_days'] // 2} calendar days",
            ),
            (
                "Items from two different return authorizations must not be combined "
                "in one parcel.",
                "must not be combined",
            ),
            (
                "The label issue date and expiry date are stored on the return record "
                "as of the record's as-of date.",
                "label issue date and expiry date are stored",
            ),
            (
                "An expired label can be reissued once by support without a new "
                "return request.",
                "reissued once by support",
            ),
        ],
        ("returns", "refund"): [
            (
                f"Warehouse inspection takes up to {policy['inspection_days']} "
                "business days after the parcel arrives.",
                f"up to {policy['inspection_days']} business days after the parcel arrives",
            ),
            (
                f"Once inspection approves the return, the refund is issued to the "
                f"original payment method within {policy['refund_min_days']} to "
                f"{policy['refund_max_days']} business days.",
                f"within {policy['refund_min_days']} to {policy['refund_max_days']} business days",
            ),
            (
                "The card issuer may need extra posting time after the refund is "
                "issued.",
                "may need extra posting time",
            ),
            (
                "The refund status on the return record is stated as of the record's "
                "as-of date.",
                "refund status on the return record is stated as of",
            ),
        ],
        ("warranty", "coverage"): [
            (
                f"The {brand} {phrase} runs for {policy['warranty_months']} months "
                "from the purchase date recorded on the warranty.",
                f"runs for {policy['warranty_months']} months",
            ),
            (
                "Coverage is limited to manufacturing defects.",
                "limited to manufacturing defects",
            ),
            (
                "Accidental damage, misuse and normal wear are not covered.",
                "misuse and normal wear are not covered",
            ),
            (
                "The coverage end date on the warranty record is authoritative as of "
                "the record's as-of date.",
                "coverage end date on the warranty record is authoritative",
            ),
            (
                "Coverage does not transfer when the product changes owner.",
                "does not transfer when the product changes owner",
            ),
        ],
        ("warranty", "claim"): [
            (
                "A claim needs the serial number recorded on the warranty and a proof "
                "of purchase.",
                "needs the serial number recorded on the warranty",
            ),
            (
                f"Support answers a submitted claim within "
                f"{policy['claim_response_days']} business days.",
                f"within {policy['claim_response_days']} business days",
            ),
            (
                "Photographs of the defect are required before a replacement is "
                "approved.",
                "Photographs of the defect are required",
            ),
            (
                "A claim against an expired warranty is declined, and the recorded "
                "coverage end date is given as the reason.",
                "against an expired warranty is declined",
            ),
        ],
        ("subscription", "renewal"): [
            (
                f"A {brand} {phrase} renews automatically on the renewal date stored "
                "on the subscription record.",
                "renews automatically on the renewal date stored",
            ),
            (
                f"A renewal notice is sent {policy['renewal_notice_days']} calendar "
                "days before that date.",
                f"sent {policy['renewal_notice_days']} calendar",
            ),
            (
                f"A failed renewal payment is retried {policy['retry_attempts']} times "
                f"at {policy['retry_interval_days']}-day intervals.",
                f"retried {policy['retry_attempts']} times",
            ),
            (
                "The plan moves to past due after the last retry fails.",
                "moves to past due after the last retry",
            ),
            (
                "The renewal date on the record is stated as of the record's as-of "
                "date.",
                "renewal date on the record is stated as of",
            ),
        ],
        ("subscription", "pause_cancel"): [
            (
                f"A plan can be paused for at most {policy['pause_max_days']} calendar "
                "days in one period.",
                f"at most {policy['pause_max_days']} calendar",
            ),
            (
                "A paused plan resumes automatically on the pause end date stored on "
                "the record.",
                "resumes automatically on the pause end date",
            ),
            (
                "A cancellation takes effect at the end of the current period shown on "
                "the subscription record.",
                "takes effect at the end of the current period",
            ),
            (
                "A cancelled plan is not refunded for the remainder of the current "
                "period.",
                "is not refunded for the remainder",
            ),
        ],
        ("support_case", "updates"): [
            (
                f"An open {brand} case gets an update every "
                f"{policy['case_update_days']} business days.",
                f"an update every {policy['case_update_days']} business days",
            ),
            (
                "The next update date is stored on the case record as of the record's "
                "as-of date.",
                "next update date is stored on the case record",
            ),
            (
                "The owner queue on the case record names the team handling the case.",
                "owner queue on the case record names the team",
            ),
            (
                "A case is closed only after the customer confirms the outcome.",
                "closed only after the customer confirms",
            ),
        ],
        ("support_case", "escalation"): [
            (
                f"A case is escalated once it is more than "
                f"{policy['escalation_hours']} hours past the next update date on the "
                "record.",
                f"more than {policy['escalation_hours']} hours past the next update",
            ),
            (
                "A case opened at high priority is escalated when it is created.",
                "high priority is escalated when it is created",
            ),
            (
                "An escalated case goes to a human specialist; the support agent "
                "cannot change a case priority.",
                "cannot change a case priority",
            ),
            (
                "Escalation does not change the recorded case status until the "
                "specialist updates it.",
                "does not change the recorded case status",
            ),
        ],
    }
    return table[(subject, aspect)]


def article_revision(merchant: Merchant, subject: str, aspect: str) -> str:
    subjects = list(SUBJECT_ASPECTS)
    offset = subjects.index(subject) * 7 + [a for a, _, _ in SUBJECT_ASPECTS[subject]].index(
        aspect
    ) * 3
    return _iso(date(2026, 6, 1) + timedelta(days=(merchant.index * 5 + offset) % 90))


def build_articles(merchant: Merchant) -> list[dict[str, Any]]:
    """8-14 articles across the five subjects, 3-8 numbered facts each."""

    articles: list[dict[str, Any]] = []
    for subject, aspects in SUBJECT_ASPECTS.items():
        phrase = merchant.phrase(subject)
        for aspect, keyword, title_fragment in aspects:
            facts = _facts(merchant, subject, aspect)
            revision = article_revision(merchant, subject, aspect)
            numbered = "\n".join(
                f"{index}. {text}" for index, (text, _) in enumerate(facts, start=1)
            )
            content = (
                f"{merchant.brand} {phrase} policy, {title_fragment}. "
                f"The numbered points below are the complete rule for this topic.\n"
                f"{numbered}\n"
                f"This article is current as of its revision date {revision}. "
                f"Dates in customer records are stated as of that record's own "
                f"as-of date, not as of this revision."
            )
            articles.append(
                {
                    "article_id": merchant.article_id(subject, aspect),
                    "title": f"{merchant.brand} {phrase}: {title_fragment}",
                    "revision": revision,
                    "topics": [
                        f"{merchant.brand_token} {phrase}",
                        f"{merchant.brand_token} {keyword}",
                        phrase,
                        keyword,
                    ],
                    "content": content,
                    "_factory": {
                        "merchant": merchant.code,
                        "subject": subject,
                        "aspect": aspect,
                        "keyword": keyword,
                        "facts": [
                            {"index": index, "text": text, "evidence": evidence}
                            for index, (text, evidence) in enumerate(facts, start=1)
                        ],
                    },
                }
            )
    return articles


# ---------------------------------------------------------------- records ----

_PRODUCT_NOUNS = {
    "outdoor equipment": ("Trail Pack", "Ridge Tent", "Summit Stove"),
    "home goods": ("Linen Set", "Carafe", "Storage Crate"),
    "bicycles and parts": ("Commuter Frame", "Trail Wheelset", "Chain Kit"),
    "audio equipment": ("Studio Monitor", "Field Recorder", "Desk Mic"),
    "kitchenware": ("Copper Pan", "Prep Board", "Kettle"),
    "furniture": ("Reading Chair", "Oak Shelf", "Work Desk"),
    "stationery": ("Notebook Set", "Fountain Pen", "Desk Planner"),
    "optical instruments": ("Field Binocular", "Spotting Scope", "Lens Kit"),
    "measuring instruments": ("Laser Level", "Caliper Set", "Survey Tripod"),
    "toys and games": ("Puzzle Box", "Model Kit", "Board Game"),
    "lighting": ("Desk Lamp", "Pendant Light", "Floor Lamp"),
    "plant care": ("Grow Light", "Watering Set", "Soil Meter"),
    "tiles and surfaces": ("Floor Tile Pack", "Wall Panel", "Grout Set"),
    "marine supplies": ("Deck Line", "Cabin Lamp", "Mooring Kit"),
    "leather goods": ("Satchel", "Belt", "Card Wallet"),
    # APPEND-ONLY: the three G-conf-2 trades. Keyed by trade string, so adding an
    # entry cannot change any product name of an existing merchant.
    "ceramics and pottery": ("Glaze Bowl", "Serving Platter", "Stoneware Mug"),
    "timber and joinery": ("Plank Bundle", "Dowel Set", "Joinery Kit"),
    "textiles and fabrics": ("Bolt of Twill", "Cotton Throw", "Upholstery Roll"),
}

_REGIONS = ("North Region", "Harbour Region", "Lake Region", "Inland Region")
_QUEUES = ("delivery support", "returns support", "product support", "billing support")

ORDER_STATUSES = (
    "in transit",
    "out for delivery",
    "ready for pickup",
    "delivered",
    "delayed in transit",
)
RETURN_STATUSES = (
    "authorized, label issued",
    "in transit to warehouse",
    "inspection complete",
    "refund issued",
    "declined item not eligible",
)
CASE_STATUSES = (
    "investigating",
    "waiting on customer",
    "waiting on carrier",
    "resolved awaiting confirmation",
    "escalated to specialist",
)
WARRANTY_STATUSES = (
    "active",
    "active claim under review",
    "expired",
    "active replacement approved",
)
SUBSCRIPTION_STATUSES = (
    "active",
    "renewal payment retry in progress",
    "paused",
    "cancellation scheduled",
    "past due",
)


def _product(merchant: Merchant, ordinal: int) -> str:
    nouns = _PRODUCT_NOUNS[merchant.trade]
    return f"{merchant.brand.split()[0]} {nouns[ordinal % len(nouns)]} {chr(65 + ordinal % 6)}{ordinal:02d}"


def _business_day(value: date) -> date:
    """``value`` itself if it is a business day, otherwise the next Monday."""

    while value.weekday() >= 5:
        value += timedelta(days=1)
    return value


def _dispatch_for(estimated: date, window: int) -> date:
    """Latest dispatch date whose business-day window lands exactly on ``estimated``."""

    found: date | None = None
    for offset in range(1, 41):
        candidate = estimated - timedelta(days=offset)
        if add_business_days(candidate, window) == estimated:
            found = candidate
            break
    if found is None:  # pragma: no cover - window is always 2..5
        raise ValueError("no dispatch date matches the delivery window")
    return found


#: Ages in days of the three delivered orders every merchant owns. Returns are
#: anchored to one of them so a return is never older than its own delivery.
DELIVERED_AGES = (6, 12, 26)


def build_orders(merchant: Merchant) -> dict[str, dict[str, Any]]:
    policy = merchant.policy
    window = policy["ship_min_days"] + (merchant.index % 2)
    delivered_index = 0
    records: dict[str, dict[str, Any]] = {}
    for ordinal in range(1, RECORDS_PER_MERCHANT["orders"] + 1):
        order_id = merchant.record_id("orders", ordinal)
        status = ORDER_STATUSES[(ordinal - 1) % len(ORDER_STATUSES)]
        if status == "in transit":
            dispatched = _shift(-1)
            estimated = add_business_days(dispatched, window)
        else:
            if status == "out for delivery":
                estimated = _shift(0)
            elif status == "ready for pickup":
                estimated = _shift(-2)
            elif status == "delivered":
                estimated = _shift(-DELIVERED_AGES[delivered_index % len(DELIVERED_AGES)])
                delivered_index += 1
            else:
                estimated = _shift(-(policy["delay_review_days"] + 2 + ordinal % 3))
            estimated = _business_day(estimated)
            dispatched = _dispatch_for(estimated, window)
        placed = dispatched - timedelta(days=1)
        record: dict[str, Any] = {
            "order_id": order_id,
            "merchant": merchant.brand,
            "status": status,
            "placed_on": _iso(placed),
            "dispatched_on": _iso(dispatched),
            "carrier_reference": merchant.record_id("carrier_reference", ordinal),
            "destination_region": _REGIONS[ordinal % len(_REGIONS)],
            "estimated_delivery": _iso(estimated),
            "record_as_of": AS_OF_DATE,
        }
        if status == "delivered":
            record["delivered_on"] = _iso(estimated)
            record["customer_note"] = (
                "The carrier recorded delivery on the estimated delivery date."
            )
        elif status == "ready for pickup":
            record["pickup_point"] = f"{_REGIONS[ordinal % len(_REGIONS)]} collection point"
            record["pickup_code"] = merchant.record_id("pickup_code", ordinal)
            record["pickup_deadline"] = _iso(
                estimated + timedelta(days=policy["pickup_hold_days"])
            )
            record["customer_note"] = "The parcel arrived at the pickup point and is waiting."
        elif status == "delayed in transit":
            record["delay_review_opened_on"] = _iso(
                estimated + timedelta(days=policy["delay_review_days"])
            )
            record["delay_review_status"] = "open with the carrier"
            record["customer_note"] = "The parcel is past its estimated delivery date."
        else:
            record["customer_note"] = "The parcel left the regional sorting centre."
        records[order_id] = record
    return records


#: Which delivered order (by age index) backs each return status, so that the
#: return's own dates are always after the recorded delivery and before as-of.
_RETURN_ANCHOR = {
    "authorized, label issued": 0,
    "in transit to warehouse": 0,
    "inspection complete": 1,
    "declined item not eligible": 1,
    "refund issued": 2,
}


def build_returns(
    merchant: Merchant, orders: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    policy = merchant.policy
    delivered = [
        record
        for record in orders.values()
        if record["status"] == "delivered"
    ]
    delivered.sort(key=lambda record: record["delivered_on"], reverse=True)
    records: dict[str, dict[str, Any]] = {}
    for ordinal in range(1, RECORDS_PER_MERCHANT["returns"] + 1):
        return_id = merchant.record_id("returns", ordinal)
        status = RETURN_STATUSES[(ordinal - 1) % len(RETURN_STATUSES)]
        anchor = delivered[_RETURN_ANCHOR[status]]
        delivered_on = date.fromisoformat(anchor["delivered_on"])
        requested = delivered_on + timedelta(days=2)
        declined = status == "declined item not eligible"
        record: dict[str, Any] = {
            "return_id": return_id,
            "merchant": merchant.brand,
            "order_reference": anchor["order_id"],
            "recorded_delivery_date": anchor["delivered_on"],
            "status": status,
            "requested_on": _iso(requested),
            "authorized_on": None if declined else _iso(requested),
            "label_issued_on": None if declined else _iso(requested),
            "label_expires_on": None
            if declined
            else _iso(requested + timedelta(days=policy["return_window_days"] // 2)),
            "record_as_of": AS_OF_DATE,
        }
        if status == "authorized, label issued":
            record["parcel_received_on"] = None
            record["inspection_status"] = "not started"
            record["refund_status"] = "not started"
        elif status == "in transit to warehouse":
            record["parcel_received_on"] = None
            record["inspection_status"] = "waiting for the parcel"
            record["refund_status"] = "not started"
        elif status == "inspection complete":
            received = requested + timedelta(days=4)
            record["parcel_received_on"] = _iso(received)
            record["inspection_status"] = "approved"
            record["refund_status"] = "approved, not yet issued"
            record["refund_due_by"] = _iso(
                add_business_days(
                    received, policy["inspection_days"] + policy["refund_max_days"]
                )
            )
        elif status == "refund issued":
            received = requested + timedelta(days=4)
            record["parcel_received_on"] = _iso(received)
            record["inspection_status"] = "approved"
            record["refund_status"] = "issued to the original payment method"
            record["refund_issued_on"] = _iso(
                add_business_days(
                    received, policy["inspection_days"] + policy["refund_min_days"]
                )
            )
        else:
            record["parcel_received_on"] = None
            record["inspection_status"] = "not applicable"
            record["refund_status"] = "not applicable"
            record["declined_on"] = _iso(requested)
            record["item_class"] = "final sale"
            record["decline_reason"] = (
                "the item is recorded as a final-sale item, which the eligibility "
                "rules exclude from the standard return process"
            )
        records[return_id] = record
    return records


def subtract_business_days(start: date, count: int) -> date:
    """Calendar date ``count`` business days before ``start`` (Mon-Fri)."""

    current = start
    remaining = count
    while remaining > 0:
        current -= timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current


def build_support_cases(
    merchant: Merchant, orders: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    policy = merchant.policy
    order_ids = sorted(orders)
    subjects = (
        "a parcel that is past its estimated delivery date",
        "a missing accessory in a delivered parcel",
        "a pickup point collection question",
        "a duplicate charge reported by the customer",
        "a damaged outer package reported on delivery",
    )
    cadence = policy["case_update_days"]
    escalation_days = policy["escalation_hours"] // 24
    records: dict[str, dict[str, Any]] = {}
    for ordinal in range(1, RECORDS_PER_MERCHANT["support_cases"] + 1):
        case_id = merchant.record_id("support_cases", ordinal)
        status = CASE_STATUSES[(ordinal - 1) % len(CASE_STATUSES)]
        escalated = status == "escalated to specialist"
        if escalated:
            next_update = _shift(-(escalation_days + 1 + ordinal % 2))
        else:
            next_update = add_business_days(AS_OF, 1 + ordinal % max(1, cadence - 1))
        last_update = subtract_business_days(next_update, cadence)
        opened_on = last_update - timedelta(days=4 + ordinal % 5)
        record: dict[str, Any] = {
            "case_id": case_id,
            "merchant": merchant.brand,
            "subject": subjects[(ordinal - 1) % len(subjects)],
            "status": status,
            "priority": "high" if escalated else "normal",
            "owner_queue": _QUEUES[ordinal % len(_QUEUES)],
            "related_order": order_ids[(ordinal * 3) % len(order_ids)],
            "opened_on": _iso(opened_on),
            "last_update": _iso(last_update),
            "next_update": _iso(next_update),
            "update_cadence_business_days": cadence,
            "record_as_of": AS_OF_DATE,
        }
        if escalated:
            record["escalated_on"] = _iso(next_update + timedelta(days=escalation_days))
            record["escalation_reason"] = (
                "the case passed its recorded next update date by more than "
                f"{policy['escalation_hours']} hours"
            )
        records[case_id] = record
    return records


def _months_before(anchor: date, months: int) -> date:
    total = (anchor.year * 12 + anchor.month - 1) - months
    year, month = divmod(total, 12)
    month += 1
    day = min(anchor.day, [31, 29 if year % 4 == 0 and (year % 100 or year % 400 == 0) else 28,
                           31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


def build_warranties(merchant: Merchant) -> dict[str, dict[str, Any]]:
    policy = merchant.policy
    months = policy["warranty_months"]
    records: dict[str, dict[str, Any]] = {}
    for ordinal in range(1, RECORDS_PER_MERCHANT["warranties"] + 1):
        warranty_id = merchant.record_id("warranties", ordinal)
        status = WARRANTY_STATUSES[(ordinal - 1) % len(WARRANTY_STATUSES)]
        if status == "expired":
            coverage_end = _shift(-(20 + ordinal % 30))
        else:
            coverage_end = _shift(60 + 11 * (ordinal % 7))
        purchased = _months_before(coverage_end, months)
        record: dict[str, Any] = {
            "warranty_id": warranty_id,
            "merchant": merchant.brand,
            "product": _product(merchant, ordinal),
            "serial_number": merchant.record_id("serial_number", ordinal),
            "purchased_on": _iso(purchased),
            "coverage_start": _iso(purchased),
            "coverage_end": _iso(coverage_end),
            "coverage_months": months,
            "status": status,
            "record_as_of": AS_OF_DATE,
        }
        if status == "active claim under review":
            submitted = _shift(-2)
            record["claim_status"] = "under review"
            record["claim_submitted_on"] = _iso(submitted)
            record["claim_answer_due_by"] = _iso(
                add_business_days(submitted, policy["claim_response_days"])
            )
        elif status == "active replacement approved":
            record["claim_status"] = "replacement approved"
            record["claim_submitted_on"] = _iso(_shift(-9))
            record["replacement_approved_on"] = _iso(_shift(-4))
        else:
            record["claim_status"] = "not open"
        records[warranty_id] = record
    return records


def build_subscriptions(merchant: Merchant) -> dict[str, dict[str, Any]]:
    policy = merchant.policy
    plans = (
        f"{merchant.brand.split()[0]} Care Monthly",
        f"{merchant.brand.split()[0]} Care Quarterly",
        f"{merchant.brand.split()[0]} Care Annual",
    )
    intervals = ("monthly", "quarterly", "annual")
    records: dict[str, dict[str, Any]] = {}
    for ordinal in range(1, RECORDS_PER_MERCHANT["subscriptions"] + 1):
        subscription_id = merchant.record_id("subscriptions", ordinal)
        status = SUBSCRIPTION_STATUSES[(ordinal - 1) % len(SUBSCRIPTION_STATUSES)]
        period_end = _shift(12 + 9 * (ordinal % 5))
        record: dict[str, Any] = {
            "subscription_id": subscription_id,
            "merchant": merchant.brand,
            "plan": plans[ordinal % len(plans)],
            "billing_interval": intervals[ordinal % len(intervals)],
            "status": status,
            "started_on": _iso(_shift(-(240 + 13 * (ordinal % 6)))),
            "current_period_end": _iso(period_end),
            "renewal_date": _iso(period_end),
            "renewal_notice_sent_on": _iso(
                period_end - timedelta(days=policy["renewal_notice_days"])
            ),
            "record_as_of": AS_OF_DATE,
        }
        if status == "active":
            record["next_step"] = "no action required"
        elif status == "renewal payment retry in progress":
            record["retry_attempts_used"] = 1
            record["retry_attempts_allowed"] = policy["retry_attempts"]
            record["next_retry_on"] = _iso(_shift(policy["retry_interval_days"]))
            record["next_step"] = "wait for the next automatic retry"
        elif status == "paused":
            pause_start = _shift(-6)
            record["pause_started_on"] = _iso(pause_start)
            record["pause_ends_on"] = _iso(
                pause_start + timedelta(days=policy["pause_max_days"])
            )
            record["next_step"] = "the plan resumes on the pause end date"
        elif status == "cancellation scheduled":
            record["cancellation_requested_on"] = _iso(_shift(-5))
            record["cancellation_effective_on"] = _iso(period_end)
            record["next_step"] = "the plan ends at the end of the current period"
        else:
            record["past_due_since"] = _iso(_shift(-(policy["retry_interval_days"] + 2)))
            record["retry_attempts_used"] = policy["retry_attempts"]
            record["retry_attempts_allowed"] = policy["retry_attempts"]
            record["next_step"] = "a human specialist reviews the past-due plan"
        records[subscription_id] = record
    return records


def build_fixture(merchant: Merchant) -> dict[str, Any]:
    orders = build_orders(merchant)
    return {
        "merchant": {
            "fixture_id": merchant.code,
            "brand": merchant.brand,
            "brand_token": merchant.brand_token,
            "trade": merchant.trade,
            "subset": merchant.subset,
            "slot": merchant.slot,
            "policy": merchant.policy,
            "subject_phrases": {
                subject: merchant.phrase(subject) for subject in SUBJECT_ASPECTS
            },
        },
        "articles": build_articles(merchant),
        "records": {
            "orders": orders,
            "returns": build_returns(merchant, orders),
            "support_cases": build_support_cases(merchant, orders),
            "warranties": build_warranties(merchant),
            "subscriptions": build_subscriptions(merchant),
        },
    }

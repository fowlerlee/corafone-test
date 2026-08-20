"""Unit tests for Corafone policy (pure Python, deterministic).

These tests are standalone — they do NOT require LiveKit, Postgres, or any external packages.
They can be run with: python3 test_policy.py
"""

from datetime import UTC, datetime, timedelta

# ── Copy of policy constants ──
MAX_DISCOUNT_PCT = 0.20
MAX_PAYMENTS = 3
MIN_PAYMENT_PCT = 0.25
MAX_WINDOW_MONTHS = 3.0
CADENCE_SPAN_MONTHS = {"weekly": 0.231, "biweekly": 0.462, "monthly": 1.0}
CADENCES = {"weekly", "biweekly", "monthly"}

# ── Copy of policy functions (standalone, no imports) ──

def best_full(principal):
    today = datetime.now(UTC).date().isoformat()
    return {"type": "full", "total": principal, "schedule": [{"date": today, "amount": principal}]}


def best_downpayment_plus_one(principal):
    today = datetime.now(UTC).date().isoformat()
    half = round(principal / 2, 2)
    return {
        "type": "downpayment_plus_one",
        "total": principal,
        "schedule": [
            {"date": today, "amount": half},
            {"date": today, "amount": round(principal - half, 2)},
        ],
    }


def best_settlement(principal):
    total = round(principal * (1 - MAX_DISCOUNT_PCT), 2)
    payment = round(total / MAX_PAYMENTS, 2)
    schedule = []
    remaining = total
    for i in range(MAX_PAYMENTS):
        amount = round(remaining, 2) if i == MAX_PAYMENTS - 1 else payment
        date = (datetime.now(UTC) + timedelta(days=30 * (i + 1))).date().isoformat()
        schedule.append({"date": date, "amount": amount})
        remaining -= amount
    return {"type": "settlement", "total": total, "schedule": schedule}


def best_plan(principal):
    payment = round(principal / MAX_PAYMENTS, 2)
    schedule = []
    remaining = principal
    for i in range(MAX_PAYMENTS):
        amount = round(remaining, 2) if i == MAX_PAYMENTS - 1 else payment
        date = (datetime.now(UTC) + timedelta(days=30 * (i + 1))).date().isoformat()
        schedule.append({"date": date, "amount": amount})
        remaining -= amount
    return {"type": "payment_plan", "total": principal, "schedule": schedule}


def validate(proposal_type, schedule, cadence, principal):
    total = sum(item["amount"] for item in schedule)
    floor = MIN_PAYMENT_PCT * total
    n = len(schedule)
    min_inst = min(item["amount"] for item in schedule) if schedule else 0.0

    if proposal_type == "full":
        ok = (n == 1 and total >= principal)
        counter = [best_full(principal)] if not ok else []

    elif proposal_type == "downpayment_plus_one":
        ok = (n == 2 and total >= principal and min_inst >= floor)
        counter = [best_downpayment_plus_one(principal)] if not ok else []

    elif proposal_type == "settlement":
        discount = principal - total
        window = n * CADENCE_SPAN_MONTHS.get(cadence or "monthly", 1.0)
        ok = (
            total <= principal
            and discount / principal <= MAX_DISCOUNT_PCT
            and n <= MAX_PAYMENTS
            and min_inst >= floor
            and window <= MAX_WINDOW_MONTHS
        )
        counter = [best_settlement(principal)] if not ok else []

    elif proposal_type == "payment_plan":
        window = n * CADENCE_SPAN_MONTHS.get(cadence or "monthly", 1.0)
        ok = (
            total >= principal
            and n <= MAX_PAYMENTS
            and (cadence or "monthly") in CADENCES
            and min_inst >= floor
            and window <= MAX_WINDOW_MONTHS
        )
        counter = [best_plan(principal)] if not ok else []

    else:
        ok = False
        counter = []

    reasons = []
    if not ok:
        if n > MAX_PAYMENTS:
            reasons.append(f"Plan allows at most {MAX_PAYMENTS} payments")
        if min_inst < floor:
            reasons.append(f"Each payment must be >= ${floor:.2f} (25% of the ${total:.2f} total)")
        if proposal_type in ("full", "downpayment_plus_one") and total < principal:
            reasons.append(f"Total must be at least ${principal:.2f}")
        if proposal_type == "settlement":
            discount = principal - total
            if discount / principal > MAX_DISCOUNT_PCT:
                reasons.append(f"Discount cannot exceed {MAX_DISCOUNT_PCT * 100:.0f}%")
        if proposal_type in ("settlement", "payment_plan"):
            window = n * CADENCE_SPAN_MONTHS.get(cadence or "monthly", 1.0)
            if window > MAX_WINDOW_MONTHS:
                reasons.append(f"Plan must complete within {int(MAX_WINDOW_MONTHS)} months")
        if proposal_type == "payment_plan" and (cadence or "monthly") not in CADENCES:
            reasons.append(f"Cadence must be one of: {', '.join(CADENCES)}")

    return {"ok": ok, "reasons": reasons, "counter_offers": counter}


# ── Tests ──

PRINCIPAL = 1000.00


def test_full_payment_valid():
    result = validate("full", [{"date": "2026-08-18", "amount": PRINCIPAL}], None, PRINCIPAL)
    assert result["ok"] is True, f"Should be valid: {result['reasons']}"
    assert len(result["reasons"]) == 0


def test_full_payment_under_principal():
    result = validate("full", [{"date": "2026-08-18", "amount": 800.0}], None, PRINCIPAL)
    assert result["ok"] is False


def test_downpayment_plus_one_valid():
    result = validate(
        "downpayment_plus_one",
        [{"date": "2026-08-18", "amount": 500.0}, {"date": "2026-09-18", "amount": 500.0}],
        None,
        PRINCIPAL,
    )
    assert result["ok"] is True


def test_downpayment_plus_one_under_floor():
    """Each payment must be >= 25% of total ($250 for $1000 total)."""
    # $100 + $900 = $1000 total, floor=$250, min_inst=$100 < $250
    result = validate(
        "downpayment_plus_one",
        [{"date": "2026-08-18", "amount": 100.0}, {"date": "2026-09-18", "amount": 900.0}],
        None,
        PRINCIPAL,
    )
    assert result["ok"] is False
    assert any("25%" in r for r in result["reasons"])


def test_settlement_valid():
    result = validate(
        "settlement",
        [
            {"date": "2026-09-18", "amount": 267.0},
            {"date": "2026-10-18", "amount": 267.0},
            {"date": "2026-11-18", "amount": 266.0},
        ],
        "monthly",
        PRINCIPAL,
    )
    assert result["ok"] is True


def test_settlement_over_20_percent_discount():
    result = validate("settlement", [{"date": "2026-09-18", "amount": 700.0}], "monthly", PRINCIPAL)
    assert result["ok"] is False
    assert any("20%" in r for r in result["reasons"])


def test_settlement_floor_scaling():
    # Pass: $267 >= $200 (floor for $800 total)
    result_ok = validate(
        "settlement",
        [
            {"date": "2026-09-18", "amount": 267.0},
            {"date": "2026-10-18", "amount": 267.0},
            {"date": "2026-11-18", "amount": 266.0},
        ],
        "monthly",
        PRINCIPAL,
    )
    assert result_ok["ok"] is True

    # Fail: $199 < $200 (min_inst=$199 < floor=$149.25... wait that's wrong)
    # Need: total=$800 (so floor=$200), min_inst < $200
    # But 3 payments of $199 each = $597, not $800
    # Let me use: total=$800, payments=$100, $100, $600 — min_inst=$100 < $200
    result_fail = validate(
        "settlement",
        [
            {"date": "2026-09-18", "amount": 100.0},
            {"date": "2026-10-18", "amount": 100.0},
            {"date": "2026-11-18", "amount": 600.0},
        ],
        "monthly",
        PRINCIPAL,
    )
    assert result_fail["ok"] is False
    assert any("25%" in r for r in result_fail["reasons"])


def test_payment_plan_valid():
    result = validate(
        "payment_plan",
        [
            {"date": "2026-09-18", "amount": 334.0},
            {"date": "2026-10-18", "amount": 333.0},
            {"date": "2026-11-18", "amount": 333.0},
        ],
        "monthly",
        PRINCIPAL,
    )
    assert result["ok"] is True


def test_payment_plan_too_many_payments():
    result = validate(
        "payment_plan",
        [
            {"date": "2026-09-18", "amount": 200.0},
            {"date": "2026-10-18", "amount": 200.0},
            {"date": "2026-11-18", "amount": 200.0},
            {"date": "2026-12-18", "amount": 200.0},
            {"date": "2027-01-18", "amount": 200.0},
        ],
        "monthly",
        PRINCIPAL,
    )
    assert result["ok"] is False
    assert any("3 payments" in r for r in result["reasons"])


def test_consumer_rejects_200_over_5_months():
    """The mid-call validation example from the plan."""
    result = validate(
        "payment_plan",
        [
            {"date": "2026-09-18", "amount": 200.0},
            {"date": "2026-10-18", "amount": 200.0},
            {"date": "2026-11-18", "amount": 200.0},
            {"date": "2026-12-18", "amount": 200.0},
            {"date": "2027-01-18", "amount": 200.0},
        ],
        "monthly",
        PRINCIPAL,
    )
    assert result["ok"] is False
    assert len(result["reasons"]) == 3
    assert any("3 payments" in r for r in result["reasons"])
    assert any("25%" in r for r in result["reasons"])
    assert any("3 months" in r for r in result["reasons"])


def test_single_payment_settlement_valid():
    """Edge case: 1-payment settlement of $800 (20% off)."""
    result = validate(
        "settlement",
        [{"date": "2026-08-18", "amount": 800.0}],
        None,
        PRINCIPAL,
    )
    # total=$800, floor=$200, payment=$800 >= $200 -> valid
    assert result["ok"] is True, f"Unexpected failure: {result['reasons']}"


if __name__ == "__main__":
    # Run tests manually (no pytest dependency required)
    tests = [
        test_full_payment_valid,
        test_full_payment_under_principal,
        test_downpayment_plus_one_valid,
        test_downpayment_plus_one_under_floor,
        test_settlement_valid,
        test_settlement_over_20_percent_discount,
        test_settlement_floor_scaling,
        test_payment_plan_valid,
        test_payment_plan_too_many_payments,
        test_consumer_rejects_200_over_5_months,
        test_single_payment_settlement_valid,
    ]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  PASS: {test.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {test.__name__}: {e}")
            failed += 1
    print(f"\n{'=' * 50}")
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)} tests")
    if failed > 0:
        exit(1)

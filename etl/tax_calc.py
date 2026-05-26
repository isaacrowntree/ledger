"""Australian tax calculations: PAYG income tax, Medicare levy, and budget allocation."""

# Australian individual tax brackets by financial year.
# FY key = ending year, e.g. 2025 means FY 2024-25 (1 Jul 2024 – 30 Jun 2025).
# Each bracket: (threshold, rate, base_tax)
# base_tax is the cumulative tax on all income below the threshold.
TAX_BRACKETS: dict[int, list[tuple[int, float, int]]] = {
    # FY 2020-21 and 2021-22 (LMITO era)
    2021: [
        (18_200, 0.0, 0),
        (45_000, 0.19, 0),
        (120_000, 0.325, 5_092),
        (180_000, 0.37, 29_467),
        (999_999_999, 0.45, 51_667),
    ],
    2022: [
        (18_200, 0.0, 0),
        (45_000, 0.19, 0),
        (120_000, 0.325, 5_092),
        (180_000, 0.37, 29_467),
        (999_999_999, 0.45, 51_667),
    ],
    # FY 2022-23
    2023: [
        (18_200, 0.0, 0),
        (45_000, 0.19, 0),
        (120_000, 0.325, 5_092),
        (180_000, 0.37, 29_467),
        (999_999_999, 0.45, 51_667),
    ],
    # FY 2023-24
    2024: [
        (18_200, 0.0, 0),
        (45_000, 0.19, 0),
        (120_000, 0.325, 5_092),
        (180_000, 0.37, 29_467),
        (999_999_999, 0.45, 51_667),
    ],
    # FY 2024-25 — Stage 3 tax cuts
    2025: [
        (18_200, 0.0, 0),
        (45_000, 0.16, 0),
        (135_000, 0.30, 4_288),
        (190_000, 0.37, 31_288),
        (999_999_999, 0.45, 51_638),
    ],
    # FY 2025-26 (same as 2024-25 unless legislated otherwise)
    2026: [
        (18_200, 0.0, 0),
        (45_000, 0.16, 0),
        (135_000, 0.30, 4_288),
        (190_000, 0.37, 31_288),
        (999_999_999, 0.45, 51_638),
    ],
}

MEDICARE_RATE = 0.02
MEDICARE_SURCHARGE_THRESHOLD = 93_000  # Single, no private health insurance
MEDICARE_SURCHARGE_RATE = 0.01  # Tier 1

# Federal budget expenditure allocation (2024-25 Budget, rounded).
# Source: Budget Paper No. 1, Statement 6 — Expenses
BUDGET_ALLOCATION = [
    ("Social Security & Welfare", 35.5),
    ("Health", 16.1),
    ("Education", 7.6),
    ("Defence", 6.3),
    ("General Government Services", 5.8),
    ("Other Economic Affairs", 5.2),
    ("Transport & Communication", 3.4),
    ("Housing & Community", 2.8),
    ("Public Order & Safety", 2.6),
    ("Recreation & Culture", 1.2),
    ("Interest on Debt", 8.5),
    ("Other", 5.0),
]


def _get_brackets(fy: int) -> list[tuple[int, float, int]]:
    """Get tax brackets for a financial year, falling back to nearest available."""
    if fy in TAX_BRACKETS:
        return TAX_BRACKETS[fy]
    available = sorted(TAX_BRACKETS.keys())
    if fy > available[-1]:
        return TAX_BRACKETS[available[-1]]
    if fy < available[0]:
        return TAX_BRACKETS[available[0]]
    # Find closest
    for i, yr in enumerate(available):
        if yr >= fy:
            return TAX_BRACKETS[yr]
    return TAX_BRACKETS[available[-1]]


def calculate_payg(taxable_income: float, fy: int) -> float:
    """Calculate PAYG income tax for a given taxable income and financial year."""
    if taxable_income <= 0:
        return 0.0

    brackets = _get_brackets(fy)

    for i, (threshold, rate, base_tax) in enumerate(brackets):
        if taxable_income <= threshold:
            if i == 0:
                return 0.0
            prev_threshold = brackets[i - 1][0]
            return base_tax + (taxable_income - prev_threshold) * rate

    # Above top bracket
    last = brackets[-1]
    prev_threshold = brackets[-2][0]
    return last[2] + (taxable_income - prev_threshold) * last[1]


def calculate_medicare(taxable_income: float, fy: int = 2025) -> float:
    """Calculate Medicare levy (2% of taxable income above threshold)."""
    if taxable_income <= 0:
        return 0.0
    # Simplified: full 2% above the low-income threshold (~$24,276 for 2024-25)
    # For most earners above ~$30k this is just 2% of taxable income
    return taxable_income * MEDICARE_RATE


def calculate_total_tax(taxable_income: float, fy: int) -> dict:
    """Calculate complete tax breakdown."""
    payg = calculate_payg(taxable_income, fy)
    medicare = calculate_medicare(taxable_income, fy)
    total = payg + medicare
    effective_rate = (total / taxable_income * 100) if taxable_income > 0 else 0

    return {
        "taxable_income": round(taxable_income, 2),
        "payg": round(payg, 2),
        "medicare": round(medicare, 2),
        "total_tax": round(total, 2),
        "effective_rate": round(effective_rate, 1),
        "after_tax": round(taxable_income - total, 2),
    }


def get_tax_dollar_breakdown(total_tax: float) -> list[dict]:
    """Map tax paid to government spending categories based on federal budget allocation."""
    result = []
    for category, pct in BUDGET_ALLOCATION:
        amount = total_tax * (pct / 100.0)
        result.append({
            "category": category,
            "amount": round(amount, 2),
            "pct": pct,
        })
    return result

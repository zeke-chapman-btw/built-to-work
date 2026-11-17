"""Position fees (Nov 2026): skilled = $5,000 for the first 3 candidates, $4,000 for the next 3,
then $3,000 for every further 3 (never lower). General labor = $1,000 per person needed."""
FIRST, SECOND, AFTER = 5000, 4000, 3000
GENERAL = 1000


def skilled_sets(candidates):
    return max(1, -(-int(candidates) // 3))


def position_fee(kind, quantity):
    """(dollars, plain-words breakdown)."""
    if kind == "none":
        return 0, "No charge"
    if kind == "general":
        n = max(1, int(quantity or 1))
        return GENERAL * n, f"General labor: {n} {'person' if n == 1 else 'people'} × ${GENERAL:,}"
    sets = skilled_sets(quantity or 3)
    parts = [FIRST] + ([SECOND] if sets >= 2 else []) + [AFTER] * max(0, sets - 2)
    words = " + ".join(f"${p:,}" for p in parts)
    return sum(parts), f"{sets * 3} candidates ({sets} set{'s' if sets > 1 else ''} of 3): {words}"

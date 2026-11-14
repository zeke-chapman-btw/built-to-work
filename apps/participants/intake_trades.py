"""Keep a sign-up form's trade choices in step with Settings › Recruiting › Trades.

Published form versions are locked snapshots, so the list flows into drafts: new forms,
the recommended draft, and an "Update trade choices" action when the list has changed.
"""
from copy import deepcopy


def trade_list():
    """Active trades from Settings, in their set order, as (value, label)."""
    from apps.recruiting.models import Classification
    return list(Classification.objects.filter(kind="trade", is_active=True).order_by("position", "label").values_list("key", "label"))


def _choices(pairs):
    return [{"value": value, "label": label} for value, label in pairs]


def with_trades(contract, trades=None):
    """A copy of the contract whose trade questions use the Settings list."""
    trades = trade_list() if trades is None else trades
    if not trades:
        return deepcopy(contract)
    contract = deepcopy(contract)
    keys = {value for value, _ in trades}
    for q in contract["questions"]:
        if q["key"] == "industry_interests":
            q["options"] = _choices(trades + [("other", "Other"), ("not_sure", "Not sure yet")])
        if q["key"] == "experiences":
            area = next(c for c in q["children"] if c["key"] == "area")
            # Areas with their own skill lists stay so those lists keep working.
            kept = [o for o in area["options"] if o["value"] in contract.get("area_skills", {}) and o["value"] not in keys]
            area["options"] = _choices(trades) + kept + [{"value": "other", "label": "Other"}]
    return contract


def trades_out_of_date(contract):
    """True when the form's trade choices differ from the Settings list."""
    trades = trade_list()
    if not trades:
        return False
    question = next((q for q in contract.get("questions", []) if q["key"] == "industry_interests"), None)
    if not question:
        return False
    current = [(o["value"], o["label"]) for o in question.get("options", []) if o["value"] not in ("other", "not_sure")]
    return current != trades

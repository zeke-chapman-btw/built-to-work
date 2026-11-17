from django import forms

from apps.customers.forms import ActionForm

from . import pricing


class ApproveForm(ActionForm):
    """Approving a position also sets its fee; the customer's bank is charged right after."""
    fee_kind = forms.ChoiceField(label="Position fee", widget=forms.RadioSelect, initial="skilled", choices=(
        ("skilled", f"Skilled position — ${pricing.FIRST:,} for 3 candidates, ${pricing.SECOND:,} for the next 3, "
                    f"then ${pricing.AFTER:,} for each 3 after that"),
        ("general", f"General labor — ${pricing.GENERAL:,} per person needed"),
        ("none", "No charge"),
    ))
    candidates = forms.IntegerField(label="Candidates wanted (skilled)", min_value=3, max_value=300, initial=3,
                                    help_text="In sets of 3. 9 candidates = $12,000.")
    people = forms.IntegerField(label="People needed (general labor)", min_value=1, max_value=500, initial=1)

    def clean(self):
        data = super().clean()
        kind = data.get("fee_kind")
        if kind == "skilled" and data.get("candidates") and data["candidates"] % 3:
            self.add_error("candidates", "Candidates come in sets of 3 (3, 6, 9…).")
        if kind in ("skilled", "general"):
            quantity = data.get("candidates") if kind == "skilled" else data.get("people")
            if quantity:
                data["fee_total"], data["fee_words"] = pricing.position_fee(kind, quantity)
        return data

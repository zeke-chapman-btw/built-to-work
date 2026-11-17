# Billing (Stripe, bank transfers only)

**Setup (administrator, once per mode):** Settings → Billing. Paste the Stripe secret key (sk_test_… first), press Test.
In Stripe → Developers → Webhooks add the endpoint URL shown on that page with the listed events, then paste its
signing secret (whsec_…). Repeat with live keys when ready. Keys are encrypted and never shown again.

**Per customer:** Customers → (customer) → Billing.
- *Send bank setup link* emails the billing email (or main contact) a link (good for 30 days) to a Stripe page where
  they connect their bank account (ACH). The page and its result are at /pay/bank/….
- *Edit details*: BTW account manager (gets billing reminders) and billing email.
- *Monthly agreements*: amount, start, end. Charged monthly until the end date, then stops. Reminders go to the
  customer contact and the account manager 30 and 7 days before (Settings → Reminders → Monthly agreement ending).
  *Renew* (Admin/Manager) creates the next agreement starting the day after the old one ends.
- *Position fees*: set on the Approve screen (Admin/Manager only). Skilled = $5,000 / $4,000 / then $3,000 per 3
  candidates; general labor = $1,000 per person; or no charge. Charged right after approval. If the bank isn't
  connected yet it waits and charges automatically once it is. Bank payments show Processing for ~4 business days,
  then Paid or Failed (failures trigger the "Payment not collected" reminder).

Code: `apps/billing` (services.py does all Stripe calls; webhook at /billing/stripe/webhook/).

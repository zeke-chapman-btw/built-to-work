from django.urls import path

from . import business, portal, views

_portal = portal.wrapped()

app_name = "billing"
urlpatterns = [
    path("customers/<uuid:org_id>/billing/bank-link/", views.send_bank_link, name="send-bank-link"),
    path("customers/<uuid:org_id>/billing/details/", views.edit_profile, name="profile"),
    path("customers/<uuid:org_id>/billing/agreements/new/", views.agreement_new, name="agreement-new"),
    path("customers/<uuid:org_id>/billing/agreements/<int:renew_id>/renew/", views.agreement_new, name="agreement-renew"),
    path("customers/<uuid:org_id>/billing/agreements/<int:pk>/cancel/", views.agreement_cancel, name="agreement-cancel"),
    path("customers/<uuid:org_id>/billing/charges/<int:pk>/retry/", views.charge_retry, name="charge-retry"),
    path("internal/reports/business/", business.overview, name="business"),  # served at /reports/business/
    path("settings/billing/", views.stripe_settings, name="settings"),
    path("billing/stripe/webhook/", views.webhook, name="webhook"),
    path("customer/billing/bank/", _portal["bank"], name="portal-bank"),
    path("customer/billing/documents/upload/", _portal["upload"], name="portal-upload"),
    path("customer/billing/documents/<int:pk>/", _portal["download"], name="portal-document"),
    path("pay/bank/<str:token>/", views.bank, name="bank"),
    path("pay/bank/<str:token>/done/", views.bank_done, name="bank-done"),
]

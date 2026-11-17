from django.urls import path

from . import connections, marketing_views, signature_views, templates_views, text_views, views

app_name = "messaging"
urlpatterns = [
    path("participants/<int:participant_id>/marketing/", marketing_views.edit_consent, name="marketing-edit"),
    path("participants/<int:participant_id>/bounce-clear/", marketing_views.bounce_clear, name="bounce-clear"),
    path("connections/mailchimp/", marketing_views.mailchimp_setup, name="mailchimp-setup"),
    path("connections/mailchimp/sync/", marketing_views.mailchimp_sync_now, name="mailchimp-sync"),
    path("connections/mailchimp/disconnect/", marketing_views.mailchimp_disconnect, name="mailchimp-disconnect"),
    path("mailchimp/webhook/<str:secret>/", marketing_views.mailchimp_webhook, name="mailchimp-webhook"),
    path("connections/", connections.connections, name="connections"),
    path("connections/texting/", connections.edit_text, name="connections-text"),
    path("connections/email/", connections.edit_email, name="connections-email"),
    path("connections/texting/test/", connections.test_text, name="connections-text-test"),
    path("connections/email/test/", connections.test_email, name="connections-email-test"),
    path("connections/sending/", connections.toggle_sending, name="connections-sending"),
    path("gmail/connect/", views.gmail_connect, name="gmail-connect"),
    path("gmail/callback/", views.gmail_callback, name="gmail-callback"),
    path("gmail/disconnect/", views.gmail_disconnect, name="gmail-disconnect"),
    path("participants/<int:participant_id>/email/", views.email_participant, name="email-participant"),
    path("participants/<int:participant_id>/text/", text_views.text_participant, name="text-participant"),
    path("contacts/<uuid:contact_id>/text/", text_views.text_contact, name="text-contact"),
    path("contacts/<uuid:contact_id>/email/", views.email_contact, name="email-contact"),
    path("texts/", text_views.inbox, name="texts"),
    path("texts/new/", text_views.new_text, name="new-text"),
    path("signature/", signature_views.my_signature, name="signature"),
    path("templates/", templates_views.template_list, name="templates"),
    path("templates/new/", templates_views.template_edit, name="template-new"),
    path("templates/<int:pk>/", templates_views.template_edit, name="template-edit"),
    path("connections/signature/", signature_views.company_signature, name="connections-signature"),
    path("gmail/replies/", views.gmail_replies_toggle, name="gmail-replies"),
    path("connections/email-replies/", connections.email_replies_toggle, name="connections-email-replies"),
    path("texts/<int:pk>/handled/", text_views.text_handled, name="text-handled"),
    path("texts/<int:pk>/reassign/", text_views.text_reassign, name="text-reassign"),
    path("eztexting/webhook/<str:token>/", text_views.eztexting_webhook, name="eztexting-webhook"),
    path("connections/replies/", connections.replies_setup, name="connections-replies"),
]

from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    def ready(self):
        # Plain wording for empty dropdown choices instead of Django's "---------".
        from django import forms
        from django.db.models import fields as model_fields
        model_fields.BLANK_CHOICE_DASH[:] = [("", "Choose…")]
        # No trailing colons on form labels.
        base_init = forms.BaseForm.__init__
        if not getattr(base_init, "_btw_plain", False):
            def form_init(self, *args, **kwargs):
                if len(args) >= 7:
                    if args[6] is None:
                        args = args[:6] + ("",) + args[7:]
                elif kwargs.get("label_suffix") is None:
                    kwargs["label_suffix"] = ""
                base_init(self, *args, **kwargs)
                for field in self.fields.values():
                    label = str(field.label or "")
                    if label.startswith("Is ") and len(label) > 3:
                        field.label = label[3:4].upper() + label[4:]
            form_init._btw_plain = True
            forms.BaseForm.__init__ = form_init
        original = forms.ModelChoiceField.__init__
        if getattr(original, "_btw_plain", False):
            return

        def __init__(self, queryset, *, empty_label="Choose…", **kwargs):
            original(self, queryset, empty_label=empty_label, **kwargs)
        __init__._btw_plain = True
        forms.ModelChoiceField.__init__ = __init__

from django.apps import AppConfig


class CustomersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.customers"
    verbose_name = "Customer management"

    def ready(self):
        from .token_logging import install_customer_link_filters
        install_customer_link_filters()

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    readonly_fields = ("id",)
    list_display = ("username", "email", "first_name", "last_name", "is_staff", "is_active")

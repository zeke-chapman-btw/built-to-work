import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # None uses defaults; an empty list deliberately hides all Quick Actions.
    quick_action_ids = models.JSONField(null=True, blank=True, default=None, editable=False)

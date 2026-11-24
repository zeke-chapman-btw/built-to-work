"""How-to guides for each kind of app user, with steps people can follow on screen."""
from django.conf import settings
from django.db import models

AUDIENCES = (
    ("admin", "Admins"),
    ("manager", "Managers"),
    ("recruiter", "Recruiters"),
    ("event_staff", "Event staff"),
    ("customer", "Customers"),
    ("participant", "Participants"),
)
STAFF_AUDIENCES = ("admin", "manager", "recruiter", "event_staff")


class Guide(models.Model):
    slug = models.SlugField(max_length=80, unique=True)
    title = models.CharField(max_length=160)
    summary = models.CharField(max_length=300, blank=True, help_text="One line: what this guide helps you do.")
    audiences = models.JSONField(default=list, blank=True, help_text="Who should see it.")
    position = models.PositiveIntegerField(default=100, help_text="Lower numbers show first.")
    required = models.BooleanField(default=True, help_text="Part of the person's training list.")
    video_url = models.URLField(blank=True, help_text="Optional video (YouTube, Vimeo, Loom or Guidde link).")
    minutes = models.PositiveSmallIntegerField(default=3)
    published = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("position", "title")
        default_permissions = ()

    def __str__(self):
        return self.title

    def audience_labels(self):
        names = dict(AUDIENCES)
        return [names[a] for a in self.audiences if a in names]

    def video_embed(self):
        url = self.video_url or ""
        if "youtube.com/watch?v=" in url:
            return "https://www.youtube.com/embed/" + url.split("v=", 1)[1].split("&", 1)[0]
        if "youtu.be/" in url:
            return "https://www.youtube.com/embed/" + url.split("youtu.be/", 1)[1].split("?", 1)[0]
        if "vimeo.com/" in url and "player." not in url:
            return "https://player.vimeo.com/video/" + url.rstrip("/").rsplit("/", 1)[1]
        if "loom.com/share/" in url:
            return url.replace("/share/", "/embed/")
        return ""


class GuideStep(models.Model):
    guide = models.ForeignKey(Guide, on_delete=models.CASCADE, related_name="steps")
    position = models.PositiveIntegerField(default=0)
    title = models.CharField(max_length=160)
    body = models.TextField(blank=True)
    image = models.CharField(max_length=200, blank=True, help_text="Screenshot file under static/training/.")
    page = models.CharField(max_length=200, blank=True, help_text="Page this step happens on, like /my/schedule/.")
    target = models.CharField(max_length=200, blank=True, help_text="What to highlight on that page (for the walk-through).")

    class Meta:
        ordering = ("position", "pk")
        default_permissions = ()


class GuideProgress(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="training_progress")
    guide = models.ForeignKey(Guide, on_delete=models.CASCADE, related_name="progress")
    completed_at = models.DateTimeField()

    class Meta:
        default_permissions = ()
        constraints = [models.UniqueConstraint(fields=("user", "guide"), name="training_one_progress")]

"""Wipe the PREVIEW database and start it fresh, optionally loading SAMPLE data.

Refuses anywhere except the password-gated preview site (see is_preview_site) and
requires the preview's own web address typed as confirmation. There is no undo.
"""
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from apps.core.sample_data import is_preview_site, load_samples


class Command(BaseCommand):
    help = "Wipe the preview database, rebuild it, and (with --load-samples) add SAMPLE data."

    def add_arguments(self, parser):
        parser.add_argument("--confirm", required=True, help="Type the preview web address, e.g. btw-preview.onrender.com")
        parser.add_argument("--load-samples", action="store_true", help="Load SAMPLE data after the wipe.")
        parser.add_argument("--keep-user", action="append", default=[], metavar="USERNAME_OR_EMAIL",
                            help="Bring this login back after the wipe with the same password and roles.")

    def handle(self, *args, **options):
        if not is_preview_site():
            raise CommandError("Refused: this only runs on the password-gated preview site.")
        hosts = [h.lower().lstrip(".") for h in settings.ALLOWED_HOSTS]
        if options["confirm"].strip().lower() not in hosts:
            raise CommandError("Refused: --confirm must be this site's web address (" + ", ".join(hosts) + ").")
        if connection.vendor != "postgresql":
            raise CommandError("Refused: the preview database is expected to be PostgreSQL.")
        kept = [self._snapshot(name) for name in options["keep_user"]]
        self.stdout.write("Wiping the preview database...")
        with connection.cursor() as cursor:
            cursor.execute("DROP SCHEMA public CASCADE")
            cursor.execute("CREATE SCHEMA public")
        connection.close()
        self.stdout.write("Rebuilding tables...")
        call_command("migrate", interactive=False, verbosity=0)
        self.stdout.write(self.style.SUCCESS("Preview database is empty and ready."))
        # Put kept logins back first, so a problem while loading samples never leaves the site without them.
        for snap in kept:
            self._restore(snap)
        if options["load_samples"]:
            self.stdout.write("Loading sample data (this can take a minute)...")
            result, credentials = load_samples(allow_preview=True)
            self.stdout.write(result)
            for username, password in credentials:
                self.stdout.write(f"{username}: {password}")
            if credentials:
                self.stdout.write("Passwords are shown once. Save them somewhere private.")
        for snap in kept:
            self._regroup(snap)
        self.stdout.write(self.style.SUCCESS("Done."))

    def _snapshot(self, name):
        from django.contrib.auth import get_user_model
        from django.db.models import Q
        user = get_user_model().objects.filter(Q(username__iexact=name) | Q(email__iexact=name)).first()
        if user is None:
            raise CommandError(f"Refused: no login matches '{name}', so nothing was wiped.")
        fields = ("username", "email", "first_name", "last_name", "password", "is_staff", "is_superuser", "is_active")
        return {"fields": {f: getattr(user, f) for f in fields},
                "groups": list(user.groups.values_list("name", flat=True)),
                "perms": list(user.user_permissions.values_list("content_type__app_label", "codename"))}

    def _restore(self, snap):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Group, Permission
        user = get_user_model()(**snap["fields"])
        user.save()
        user.groups.set(Group.objects.filter(name__in=snap["groups"]))
        missing = sorted(set(snap["groups"]) - set(user.groups.values_list("name", flat=True)))
        user.user_permissions.set([p for app, code in snap["perms"]
                                   for p in Permission.objects.filter(content_type__app_label=app, codename=code)])
        note = f" (roles not found: {', '.join(missing)})" if missing else ""
        self.stdout.write(self.style.SUCCESS(f"Kept login: {user.username} with the same password{note}."))

    def _regroup(self, snap):
        """Sample data may create role groups; attach any the kept login had."""
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Group
        user = get_user_model().objects.filter(username=snap["fields"]["username"]).first()
        if user:
            user.groups.add(*Group.objects.filter(name__in=snap["groups"]))

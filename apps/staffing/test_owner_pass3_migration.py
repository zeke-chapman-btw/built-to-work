"""Populated additive/reverse rehearsal; identity guards must remain installed."""
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class OwnPayAccessMigrationTests(TransactionTestCase):
    old = ('staffing', '0002_daily_role_coverage')
    new = ('staffing', '0003_staffprofile_my_pay_enabled')

    def migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate([target])
        return executor.loader.project_state([target]).apps

    def triggers(self):
        with connection.cursor() as cursor:
            if connection.vendor == 'sqlite':
                cursor.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger' ORDER BY name")
            else:
                cursor.execute('SELECT tgname, pg_get_triggerdef(oid) FROM pg_trigger WHERE NOT tgisinternal ORDER BY tgname')
            return sorted(cursor.fetchall())

    def test_populated_forward_reverse_preserves_legacy_records_and_identity_guards(self):
        try:
            apps = self.migrate(self.old)
            Profile = apps.get_model('staffing', 'StaffProfile')
            LegacyProfile = Profile
            person = Profile.objects.create(first_name='Migration', last_name='Fixture',
                email='migration@example.com', phone='404-555-0103', street_address='Non-deliverable fixture',
                city='Athens', state='GA', zip_code='00000', shirt_size='5XL', internal_notes='Retained source note')
            fields = [field.attname for field in Profile._meta.concrete_fields]
            original = Profile.objects.values(*fields).get(pk=person.pk)
            guards = self.triggers()
            self.assertTrue(guards)
            apps = self.migrate(self.new)
            Profile = apps.get_model('staffing', 'StaffProfile')
            self.assertEqual(Profile.objects.values(*fields).get(pk=person.pk), original)
            self.assertFalse(Profile.objects.get(pk=person.pk).my_pay_enabled)
            self.assertEqual(self.triggers(), guards)
            legacy_writer = LegacyProfile.objects.create(first_name='Older writer', last_name='Fixture')
            self.assertFalse(Profile.objects.get(pk=legacy_writer.pk).my_pay_enabled)
            # Reverse loses only the newly introduced configuration, never
            # legacy profile data, immutable earnings, or identity guards.
            Profile.objects.filter(pk=person.pk).update(my_pay_enabled=True)
            apps = self.migrate(self.old)
            Profile = apps.get_model('staffing', 'StaffProfile')
            self.assertEqual(Profile.objects.values(*fields).get(pk=person.pk), original)
            self.assertEqual(self.triggers(), guards)
            apps = self.migrate(self.new)
            Profile = apps.get_model('staffing', 'StaffProfile')
            self.assertFalse(Profile.objects.get(pk=person.pk).my_pay_enabled)
            self.assertEqual(Profile.objects.values(*fields).get(pk=person.pk), original)
            self.assertEqual(self.triggers(), guards)
        finally:
            self.migrate(self.new)

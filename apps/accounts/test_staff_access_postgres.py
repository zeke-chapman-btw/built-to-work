"""Isolated PostgreSQL row-lock and additive-migration checks."""
from concurrent.futures import ThreadPoolExecutor
from unittest import skipUnless
from uuid import uuid4
from django.test import TransactionTestCase,Client
from django.db import connection,close_old_connections
from django.contrib.auth import get_user_model,SESSION_KEY,BACKEND_SESSION_KEY,HASH_SESSION_KEY
from django.contrib.auth.models import Group
from django.contrib.auth.hashers import make_password
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.sessions.backends.db import SessionStore
from django.db.migrations.executor import MigrationExecutor
from django.core.exceptions import ValidationError
from django.urls import reverse
from .roles import role_configuration,ROLE_GROUPS
from .staff_services import save_account,revision,issue_invitation,invitation_token,accept_invitation
@skipUnless(connection.vendor=='postgresql','Requires an isolated PostgreSQL database')
class StaffPostgreSQLTests(TransactionTestCase):
    def setUp(self):
        role_configuration(apply=True);U=get_user_model()
        self.manager=U.objects.create_user('synthetic-pg-manager',is_staff=True,email='manager@example.test');self.manager.groups.add(Group.objects.get(name=ROLE_GROUPS['manager']))
        self.worker=U.objects.create_user('synthetic-pg-worker',is_staff=True,email='worker@example.test',first_name='SYNTHETIC',last_name='PG');self.worker.groups.add(Group.objects.get(name=ROLE_GROUPS['event_staff']))
    def run_threads(self,function):
        def run(i):
            close_old_connections()
            try:return function(i)
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:return list(pool.map(run,[0,1]))
    def test_concurrent_account_updates_have_one_winner_and_one_stale_revision(self):
        data=dict(username=self.worker.username,email=self.worker.email,first_name='SYNTHETIC',last_name='Updated',roles=['event_staff'],confirm_identity=True,reason='SYNTHETIC concurrent update',revision=revision(self.worker),is_active=True)
        def mutate(i):
            try:save_account(actor=self.manager,user_id=self.worker.pk,data=data);return 'saved'
            except ValidationError:return 'stale'
        self.assertCountEqual(self.run_threads(mutate),['saved','stale'])
    def test_concurrent_activation_consumes_invitation_once(self):
        self.worker.is_active=False;self.worker.save()
        item=issue_invitation(user=self.worker,actor=self.manager,expected_revision=revision(self.worker),reason='SYNTHETIC concurrent setup');token=invitation_token(item)
        def accept(i):
            try:accept_invitation(token=token,password='SYNTHETIC-pg-only-password-7319');return 'accepted'
            except ValidationError:return 'invalid'
        self.assertCountEqual(self.run_threads(accept),['accepted','invalid'])
    def test_additive_migration_preserves_uuid_password_and_legacy_session(self):
        executor=MigrationExecutor(connection)
        executor.migrate([('accounts','0002_user_quick_action_ids')])
        try:
            old=executor.loader.project_state([('accounts','0002_user_quick_action_ids')]).apps.get_model('accounts','User')
            ident=uuid4();password=make_password('SYNTHETIC-migration-password-7319')
            old.objects.create(id=ident,username='synthetic-pre-access',email='migration@example.test',password=password,is_staff=True,is_active=True)
            proxy=get_user_model()(id=ident,password=password,is_staff=True,is_active=True)
            session=SessionStore();session[SESSION_KEY]=str(ident);session[BACKEND_SESSION_KEY]='django.contrib.auth.backends.ModelBackend';session[HASH_SESSION_KEY]=AbstractBaseUser._get_session_auth_hash(proxy);session.save();cookie=session.session_key
        finally:
            MigrationExecutor(connection).migrate([('accounts','0004_security_revision_database_default')])
        user=get_user_model().objects.get(pk=ident)
        self.assertEqual(user.security_revision,0);self.assertEqual(user.password,password);self.assertEqual(user.groups.count(),0)
        client=Client();client.cookies['sessionid']=cookie;self.assertEqual(client.get(reverse('internal:home')).status_code,200)

    def test_unseeded_native_admin_cannot_concurrently_remove_last_administrator(self):
        from django.contrib import admin
        from django.test import RequestFactory
        from django.db import transaction
        from .admin import CustomUserAdmin
        U=get_user_model()
        admins=[U.objects.create_superuser('synthetic-bootstrap-'+str(i),None,None) for i in range(2)]
        Group.objects.filter(name__in=ROLE_GROUPS.values()).delete()
        def deactivate(i):
            actor=U.objects.get(pk=admins[i].pk)
            request=RequestFactory().post('/admin/');request.user=actor
            target=U.objects.get(pk=actor.pk);target.is_active=False
            try:
                with transaction.atomic():CustomUserAdmin(U,admin.site).save_model(request,target,None,True)
                return 'saved'
            except ValidationError:return 'retained'
        self.assertCountEqual(self.run_threads(deactivate),['saved','retained'])
        self.assertEqual(U.objects.filter(is_superuser=True,is_active=True,is_staff=True).count(),1)

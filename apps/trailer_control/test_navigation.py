"""Navigation must not widen restricted trailer administration or change hardware."""
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse
from apps.core.models import AuditLog
from apps.events.models import TrailerInstance
from apps.internal_ui.navigation import visible_workspace_links
from .models import Device, DisplayBinding, PublicationGrant, SyncRunRequest, ControlConfiguration

@override_settings(TRAILER_CONTROL_ENABLED=True, TRAILER_CONTROL_IDENTITY='SYNTHETIC-AIT-LOCAL')
class TrailerNavigationTests(TestCase):
    def setUp(self):
        self.admin=get_user_model().objects.create_superuser('synthetic-nav-admin')
        self.control=get_user_model().objects.create_user('synthetic-local-control',is_staff=True)
        self.worker=get_user_model().objects.create_user('synthetic-ordinary-worker',is_staff=True)
        self.control.user_permissions.add(Permission.objects.get(content_type__app_label='trailer_control',codename='view_control_center'))
        self.local=TrailerInstance.objects.create(identity='SYNTHETIC-AIT-LOCAL',display_name='SYNTHETIC local trailer')
        self.other=TrailerInstance.objects.create(identity='SYNTHETIC-RESTRICTED-OTHER',display_name='RESTRICTED OTHER TRAILER')

    def counts(self):
        return tuple(model.objects.count() for model in [AuditLog,TrailerInstance,Device,DisplayBinding,PublicationGrant,SyncRunRequest,ControlConfiguration])

    def test_control_only_sees_local_entry_not_directory_or_credentials(self):
        self.client.force_login(self.control,backend="django.contrib.auth.backends.ModelBackend")
        before=self.counts(); response=self.client.get(reverse('operations:trailers'))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'SYNTHETIC local trailer')
        self.assertContains(response,reverse('trailer_control:index'))
        self.assertNotContains(response,'RESTRICTED OTHER TRAILER')
        self.assertNotContains(response,'Provisioning and credentials')
        self.assertNotContains(response,'trailer-edit')
        self.assertIn('operations:trailers',[item['url_name'] for item in visible_workspace_links(self.control,'operations')])
        for trailer in [self.local,self.other]:
            self.assertEqual(self.client.get(reverse('operations:trailer-edit',args=[trailer.pk])).status_code,403)
        self.assertEqual(self.counts(),before)

    def test_admin_keeps_full_directory_and_control_is_single_local_entry(self):
        self.client.force_login(self.admin,backend="django.contrib.auth.backends.ModelBackend")
        response=self.client.get(reverse('operations:trailers'))
        self.assertContains(response,'RESTRICTED OTHER TRAILER')
        self.assertContains(response,'Provisioning and credentials')
        self.assertContains(response,reverse('trailer_control:index'),count=1)
        self.assertEqual(self.client.get(reverse('operations:trailer-edit',args=[self.other.pk])).status_code,200)

    def test_disabled_and_missing_identity_fail_closed_without_hiding_admin_directory(self):
        self.client.force_login(self.control,backend="django.contrib.auth.backends.ModelBackend")
        for options in [{'TRAILER_CONTROL_ENABLED':False},{'TRAILER_CONTROL_IDENTITY':'not-configured'}]:
            with override_settings(**options):
                self.assertEqual(self.client.get(reverse('operations:trailers')).status_code,403)
                self.assertNotIn('operations:trailers',[item['url_name'] for item in visible_workspace_links(self.control,'operations')])
                self.client.force_login(self.admin,backend="django.contrib.auth.backends.ModelBackend")
                self.assertEqual(self.client.get(reverse('operations:trailers')).status_code,200)
                self.client.force_login(self.control,backend="django.contrib.auth.backends.ModelBackend")

    def test_ordinary_and_inactive_staff_do_not_gain_entry(self):
        self.client.force_login(self.worker,backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.get(reverse('operations:trailers')).status_code,403)
        self.control.is_active=False;self.control.save(update_fields=['is_active'])
        self.client.force_login(self.control,backend="django.contrib.auth.backends.ModelBackend")
        self.assertIn(self.client.get(reverse('operations:trailers')).status_code,[302,403])

    def test_return_links_and_operations_direct_shortcut_remain(self):
        self.client.force_login(self.control,backend="django.contrib.auth.backends.ModelBackend")
        before=self.counts()
        response=self.client.get(reverse('trailer_control:index'))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,reverse('operations:trailers'))
        self.assertContains(response,reverse('internal:workspace',args=['operations']))
        self.assertContains(self.client.get(reverse('internal:workspace',args=['operations'])),reverse('trailer_control:index'))
        self.assertEqual(self.counts(),before)

"""Operations presentation round trips with the existing domain safeguards."""
from copy import deepcopy
from uuid import uuid4
from urllib.parse import urlencode, urlsplit, parse_qs
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import SimpleTestCase, TestCase, RequestFactory
from django.urls import reverse
from apps.internal_ui.section_editors import operations_return, has_edit_conflict
from apps.operations.equipment_views import revision
from apps.operations.template_forms import TemplateDraftForm
from apps.events.models import TrailerInstance
from apps.stations.models import Station
from apps.core.models import AuditLog
from apps.participants.models import ConsentDocumentVersion
from apps.participants.intake_lifecycle import create_form, save_draft, revision as intake_revision
from apps.participants.intake_config_forms import QuestionConfigForm
from apps.participants.ui_presentation import intake_editor_groups


class ReturnContextTests(SimpleTestCase):
    def test_only_known_local_destinations_and_filter_keys_survive(self):
        fallback=reverse('stations:list');factory=RequestFactory()
        good=fallback+'?'+urlencode(dict(q='North & West',status='active',sort='name',page=2))
        self.assertEqual(operations_return(factory.get('/',{'return_to':good}),fallback),good)
        for bad in ['https://outside.invalid'+fallback,'//outside.invalid'+fallback,'/\\outside.invalid',fallback+'?next=https://outside.invalid',fallback+'#elsewhere','/admin/','\x00'+fallback]:
            with self.subTest(bad=bad):self.assertEqual(operations_return(factory.post('/',{'return_to':bad}),fallback),fallback)

    def test_detail_context_validates_nested_origin(self):
        from types import SimpleNamespace
        station=SimpleNamespace(pk=uuid4());detail=reverse('stations:detail',args=[station.pk]);listing=reverse('stations:list')
        for source,expected in [(listing+'?q=practice',listing+'?q=practice'),('https://outside.invalid',None)]:
            value=detail+'?'+urlencode({'page':2,'from':source})
            result=operations_return(RequestFactory().get('/',{'return_to':value}),listing,station=station)
            query=parse_qs(urlsplit(result).query)
            self.assertEqual(query.get('from'),[expected] if expected else None)
            self.assertEqual(query['page'],['2'])


class OperationsRoundTripTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        U=get_user_model();cls.admin=U.objects.create_superuser('uxc-admin','admin@example.invalid','synthetic-only')
        cls.viewer=U.objects.create_user('uxc-viewer',is_staff=True)
        cls.station=Station.objects.create(code='uxc-test',name='Practice station',station_type='kiosk')
        cls.trailer=TrailerInstance.objects.create(identity='uxc-test',display_name='Practice trailer')

    def setUp(self):self.client.force_login(self.admin,backend='django.contrib.auth.backends.ModelBackend')

    def test_station_editor_retains_filtered_origin_and_protected_code(self):
        origin=reverse('stations:list')+'?q=Practice&status=active&page=2'
        url=reverse('stations:edit',args=[self.station.pk]);before=Station.objects.count()
        get=self.client.get(url,{'return_to':origin},HTTP_X_BTW_EDITOR='1')
        self.assertEqual(get.status_code,200);self.assertEqual(get['Cache-Control'],'no-store')
        self.assertEqual(Station.objects.count(),before)
        result=self.client.post(url,dict(code='forged',name='Practice renamed',station_type='kiosk',is_active='on',return_to=origin),HTTP_X_BTW_EDITOR='1')
        self.assertEqual(result.json()['url'],origin)
        self.station.refresh_from_db();self.assertEqual(self.station.code,'uxc-test');self.assertEqual(self.station.name,'Practice renamed')

    def test_invalid_station_keeps_entered_values_and_changes_nothing(self):
        response=self.client.post(reverse('stations:edit',args=[self.station.pk]),dict(name='',station_type='kiosk',description='Keep this draft',location='North side',is_active='on'),HTTP_X_BTW_EDITOR='1')
        self.assertEqual(response.status_code,400)
        self.assertContains(response,'Keep this draft',status_code=400)
        self.assertContains(response,'North side',status_code=400)
        self.station.refresh_from_db();self.assertEqual(self.station.name,'Practice station')

    def test_trailer_reason_identity_audit_and_return_context_remain_enforced(self):
        url=reverse('operations:trailer-edit',args=[self.trailer.pk]);origin=reverse('operations:trailers')+'?q=Practice&status=active'
        data=dict(display_name='Reviewed trailer',identity='forged',is_active='on',revision=revision(self.trailer),return_to=origin)
        rejected=self.client.post(url,data,HTTP_X_BTW_EDITOR='1');self.assertEqual(rejected.status_code,400)
        self.trailer.refresh_from_db();self.assertEqual(self.trailer.display_name,'Practice trailer')
        data['reason']='Synthetic review correction';saved=self.client.post(url,data,HTTP_X_BTW_EDITOR='1')
        self.assertEqual(saved.json()['url'],origin);self.trailer.refresh_from_db();self.assertEqual(self.trailer.identity,'uxc-test')
        audit=AuditLog.objects.get(action='operations.trailer.updated',object_id=str(self.trailer.pk))
        self.assertEqual(audit.reason,'Synthetic review correction')

    def test_stale_trailer_keeps_entries_and_refuses_overwrite(self):
        token=revision(self.trailer);TrailerInstance.objects.filter(pk=self.trailer.pk).update(display_name='Other reviewed change')
        response=self.client.post(reverse('operations:trailer-edit',args=[self.trailer.pk]),dict(display_name='My draft',is_active='on',revision=token,reason='Review'),HTTP_X_BTW_EDITOR='1')
        self.assertEqual(response.status_code,409);self.assertTrue(response.context['save_conflict'])
        self.assertContains(response,'My draft',status_code=409)
        self.trailer.refresh_from_db();self.assertEqual(self.trailer.display_name,'Other reviewed change')
        self.assertFalse(AuditLog.objects.filter(action='operations.trailer.updated',object_id=str(self.trailer.pk)).exists())

    def test_view_only_user_cannot_gain_authoring_or_private_consent_access(self):
        self.client.force_login(self.viewer,backend='django.contrib.auth.backends.ModelBackend')
        for url in [reverse('operations:trailers'),reverse('operations:trailer-edit',args=[self.trailer.pk]),reverse('assessments:category_new'),reverse('participants:intake-config-index'),reverse('participants:consent-index')]:
            with self.subTest(url=url):self.assertEqual(self.client.get(url).status_code,403)

    def test_template_resource_choices_and_validation_keep_selected_station(self):
        form=TemplateDraftForm(dict(name='Synthetic draft',stations=[str(self.station.pk)],not_applicable=['station']))
        self.assertFalse(form.is_valid());self.assertIn('not_applicable',form.errors)
        self.assertEqual(form['stations'].value(),[str(self.station.pk)])
        self.assertIn(str(self.station.pk),[str(pk) for pk,label in form.fields['stations'].widget.choices])

    def test_intake_conflict_retains_original_revision_and_contract(self):
        draft=create_form(actor=self.admin,name='Synthetic configuration')
        old=intake_revision(draft);contract=deepcopy(draft.snapshot)
        form=QuestionConfigForm(contract=contract,key='first_name')
        data={key:value for key,value in form.initial.items() if value is not None}
        data.update(label='My retained label',revision=old,required='on')
        # A real concurrent revision; the guarded service must reject the old token.
        draft.snapshot=deepcopy(contract);draft.snapshot['questions'][0]['help_text']='Other editor changed this'
        save_draft(actor=self.admin,form_id=draft.form_id,contract=draft.snapshot,expected_revision=old)
        response=self.client.post(reverse('participants:intake-config-question',args=[draft.form_id,'first_name']),data)
        self.assertEqual(response.status_code,200);self.assertTrue(response.context['save_conflict'])
        self.assertEqual(response.context['revision'],old)
        self.assertContains(response,'My retained label')
        draft.refresh_from_db();self.assertNotEqual(draft.snapshot['questions'][0]['label'],'My retained label')

    def test_consent_failure_has_recovery_without_publishing_or_replacing_text(self):
        doc=ConsentDocumentVersion.objects.create(key='uxc',version='1',title='Synthetic',body='Stored text')
        response=self.client.post(reverse('participants:consent-review',args=[doc.pk]),dict(action='save',body=''))
        self.assertEqual(response.status_code,400);self.assertContains(response,'Enter the complete document text.',status_code=400)
        blocked=self.client.post(reverse('participants:consent-publish',args=[doc.pk]),dict(confirm_publish='on'))
        self.assertEqual(blocked.status_code,400);doc.refresh_from_db();self.assertEqual(doc.body,'Stored text');self.assertFalse(doc.is_approved)

    def test_editor_grouping_keeps_every_question_control(self):
        draft=create_form(actor=self.admin,name='Synthetic field coverage')
        form=QuestionConfigForm(contract=draft.snapshot,key='first_name')
        names=[field.name for group in intake_editor_groups(form,'question') for field in group['fields']]
        self.assertCountEqual(names,list(form.fields))

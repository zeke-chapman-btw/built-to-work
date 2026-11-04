"""Populated baseline-to-Build15 migration preservation in a dedicated test DB."""
from decimal import Decimal
from unittest import skipUnless
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


@skipUnless(connection.vendor == 'postgresql', 'Populated immutable hiring-history migration uses PostgreSQL TRUNCATE cleanup; verified in its own PostgreSQL test database')
class PopulatedMigrationPreservationTests(TransactionTestCase):
    available_apps = None

    def test_original_people_consent_identity_reviews_and_approved_terms_survive(self):
        executor=MigrationExecutor(connection)
        latest=executor.loader.graph.leaf_nodes()
        before=[('recruiting',None),('participants','0020_participant_profile_entries'),('events','0014_participantmatchreview_candidates_and_more')]
        try:
            executor.migrate(before)
            executor=MigrationExecutor(connection)
            targets=[node for node in latest if node[0] not in {'recruiting','participants','events'}]
            targets += [('participants','0020_participant_profile_entries'),('events','0014_participantmatchreview_candidates_and_more')]
            old=executor.loader.project_state(targets).apps
            User=old.get_model('accounts','User')
            actor=User.objects.create(username='synthetic15-migration',is_active=True,is_staff=True,is_superuser=True,password='!')
            Person=old.get_model('participants','Participant')
            person=Person.objects.create(first_name='SYNTHETIC',last_name='Migration',city='Original city')
            uuid=person.identity_uuid
            Career=old.get_model('participants','ParticipantCareerProfile')
            career=Career.objects.create(participant=person,employment_status='Original historical text',years_of_experience=Decimal('9.5'))
            Saved=old.get_model('participants','SavedParticipantView')
            saved=Saved.objects.create(owner=actor,name='SYNTHETIC retained search',criteria={'city':'Original city'})
            Form=old.get_model('participants','RegistrationForm')
            Version=old.get_model('participants','RegistrationFormVersion')
            form=Form.objects.create(slug='synthetic15-migration-form',name='SYNTHETIC retained intake')
            version=Version.objects.create(form=form,version=1,status='published',snapshot={'original':'unchanged'})
            Submission=old.get_model('participants','RegistrationSubmission')
            submission=Submission.objects.create(form_version=version,participant=person,status='submitted',answers={'retained':'original'},answer_snapshot={'original':'retained'})
            Document=old.get_model('participants','ConsentDocumentVersion')
            doc=Document.objects.create(key='synthetic15-original',version='1',title='SYNTHETIC consent',body='Original recorded terms',content_hash='a'*64,is_approved=True)
            Acceptance=old.get_model('participants','ConsentAcceptance')
            acceptance=Acceptance.objects.create(participant=person,submission=submission,document=doc,metadata={'content_hash':'a'*64})
            Review=old.get_model('events','ParticipantMatchReview')
            review=Review.objects.create(source_submission=submission,source_participant=person,status='open',evidence={'original':True})
            Organization=old.get_model('customers','CustomerOrganization')
            org=Organization.objects.create(name='SYNTHETIC migration company')
            Need=old.get_model('customers','WorkforceNeed')
            Revision=old.get_model('customers','WorkforceNeedRevision')
            need=Need.objects.create(organization=org)
            hiring=Revision.objects.create(organization=org,need=need,number=1,title='SYNTHETIC approved position',
                state='approved',worker_quantity=2,pay_minimum=Decimal('27.25'),pay_maximum=Decimal('32.75'),
                submitted_by=actor,submitted_at=timezone.now(),approved_by=actor,approved_at=timezone.now())
            need.effective_revision=hiring;need.status='active';need.save()
            ids={'person':person.pk,'career':career.pk,'saved':saved.pk,'submission':submission.pk,'consent':acceptance.pk,
                 'doc':doc.pk,'review':review.pk,'need':need.pk,'hiring':hiring.pk}
            executor=MigrationExecutor(connection);executor.migrate(latest)
            new=executor.loader.project_state(latest).apps
            person=new.get_model('participants','Participant').objects.get(pk=ids['person'])
            self.assertEqual(person.identity_uuid,uuid)
            self.assertEqual(person.city,'Original city')
            self.assertEqual(new.get_model('participants','ParticipantCareerProfile').objects.get(pk=ids['career']).employment_status,'Original historical text')
            saved=new.get_model('participants','SavedParticipantView').objects.get(pk=ids['saved'])
            self.assertFalse(saved.shared);self.assertEqual(saved.criteria,{'city':'Original city'})
            source=new.get_model('participants','RegistrationSubmission').objects.get(pk=ids['submission'])
            self.assertEqual(source.answers,{'retained':'original'})
            acceptance=new.get_model('participants','ConsentAcceptance').objects.get(pk=ids['consent'])
            self.assertEqual(acceptance.document_id,ids['doc']);self.assertEqual(acceptance.participant_id,ids['person'])
            review=new.get_model('events','ParticipantMatchReview').objects.get(pk=ids['review'])
            self.assertFalse(review.established_person_review);self.assertEqual(review.evidence,{'original':True})
            hiring=new.get_model('customers','WorkforceNeedRevision').objects.get(pk=ids['hiring'])
            self.assertEqual(hiring.state,'approved');self.assertEqual(hiring.pay_minimum,Decimal('27.25'))
            self.assertEqual(hiring.pay_maximum,Decimal('32.75'));self.assertEqual(hiring.worker_quantity,2)
            self.assertEqual(new.get_model('customers','WorkforceNeed').objects.get(pk=ids['need']).effective_revision_id,ids['hiring'])
            # The previous deployed code omits both new columns during rolling deployment
            # and code rollback. Exercise its historical ORM against the upgraded schema.
            legacy_search=Saved.objects.create(owner_id=actor.pk,name='SYNTHETIC old-code search after upgrade',criteria={'legacy':True})
            self.assertFalse(new.get_model('participants','SavedParticipantView').objects.get(pk=legacy_search.pk).shared)
            legacy_submission=Submission.objects.create(form_version_id=version.pk,participant_id=ids['person'],status='submitted',answers={'legacy':True})
            legacy_review=Review.objects.create(source_submission=legacy_submission,source_participant_id=ids['person'],status='open',evidence={'legacy_insert':True})
            self.assertFalse(new.get_model('events','ParticipantMatchReview').objects.get(pk=legacy_review.pk).established_person_review)
        finally:
            MigrationExecutor(connection).migrate(latest)

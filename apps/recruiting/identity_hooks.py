"""Keep reviewed legacy identity links and recruiting uniqueness in one transaction."""
import hashlib,json
from django.core.exceptions import ValidationError
from apps.events.models import ParticipantIdentityLink
from apps.participants.identity import canonical_id,fresh_identity_context,identity_members
from apps.participants.models import Participant
from .models import CandidateOpportunity,CandidateInterest,CandidateListEntry,IdentityConsolidation


def before_reviewed_write(name,kwargs):
    if name=='reverse_match':
        link=ParticipantIdentityLink.objects.filter(review_id=kwargs.get('review_id'),is_active=True).first()
        if link:
            ids=identity_members(link.candidate_participant_id)|identity_members(link.source_participant_id)
            from apps.participants.identifier_services import protect_identifier_reversal
            protect_identifier_reversal(ids)
            if (CandidateOpportunity.objects.filter(participant_id__in=ids).exists() or CandidateInterest.objects.filter(participant_id__in=ids).exists()
                or CandidateListEntry.objects.filter(participant_id__in=ids).exists()):
                raise ValidationError('Recruiting activity depends on this identity link. Use Recruiting → Identity review history to request a safe reversal or an attributed follow-up review; no recruiting records were discarded.')
    return set(ParticipantIdentityLink.objects.filter(is_active=True,review__status='resolved').values_list('pk',flat=True))


def after_reviewed_write(before,actor):
    from .foundation import audit
    from .selection import reconcile_consolidation
    links=list(ParticipantIdentityLink.objects.filter(is_active=True,review__status='resolved').exclude(pk__in=before).select_related('review','source_participant','candidate_participant'))
    if not links:return
    from apps.participants.identifier_services import preserve_reviewed_links
    preserve_reviewed_links(links)
    with fresh_identity_context():
        references=set(CandidateOpportunity.objects.values_list('participant_id',flat=True))|set(CandidateInterest.objects.values_list('participant_id',flat=True))|set(CandidateListEntry.objects.filter(removed_at__isnull=True).values_list('participant_id',flat=True))
        history=[]
        for source_id in sorted(references):
            target_id=canonical_id(source_id)
            if source_id==target_id:continue
            source=Participant.objects.get(pk=source_id);target=Participant.objects.get(pk=target_id)
            for entry in CandidateListEntry.objects.select_for_update().filter(participant=source,removed_at__isnull=True).order_by('pk'):
                other=CandidateListEntry.objects.filter(candidate_list=entry.candidate_list,participant=target,removed_at__isnull=True).first()
                if other:
                    from django.utils import timezone
                    entry.removed_at=timezone.now();entry.removed_by=actor;entry.removal_reason='Retained historical membership after reviewed identity consolidation.'
                else:entry.participant=target
                entry.save();history.append({'list_entry':str(entry.pk),'action':'legacy_identity_reconciled'})
            history+=reconcile_consolidation(source,target,actor)
        for link in links:
            fingerprint=hashlib.sha256(json.dumps({'source':link.source_participant_id,'target':link.candidate_participant_id,'review':str(link.review_id)},sort_keys=True).encode()).hexdigest()
            row,created=IdentityConsolidation.objects.get_or_create(review=link.review,defaults={'source':link.source_participant,'target_id':canonical_id(link.candidate_participant_id),
                'actor':actor,'reason':'Existing reviewed identity decision; recruiting references reconciled without discarding source history.','fingerprint':fingerprint,'collision_history':history})
            if created:audit(actor,'identity.legacy_reconciled',row,data={'review':str(link.review_id),'retained_history':len(history)})

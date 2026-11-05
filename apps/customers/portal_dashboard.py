"""Customer-owned overview assembled from existing guarded projections.

No internal stage labels, match scores, private interactions or unreleased people
are projected. A response remains the employer's report until BTW confirms it.
"""
from django.urls import reverse
from apps.recruiting.access import policy_transaction
from apps.recruiting.employer import list_candidates
from apps.recruiting.models import WorkforceRequest
from apps.crm.services import employer_history
from .portal_policy import require_membership
from . import projections


def recruiting_context(actor, organization_id, *, can_needs, can_campaigns):
    with policy_transaction():
        require_membership(actor, organization_id, lock=True)
        positions = projections.needs(actor, organization_id)
        campaigns = projections.campaigns(actor, organization_id)
        candidates = list_candidates(actor, organization_id)
        positions_by_id = {str(row['id']): row for row in positions}
        requests = []
        grouped_ids = set()
        for request in WorkforceRequest.objects.filter(organization_id=organization_id,
                archived_at__isnull=True).prefetch_related('positions').order_by('name', 'pk'):
            rows = [positions_by_id[str(link.need_id)] for link in request.positions.all()
                if str(link.need_id) in positions_by_id]
            if rows:
                requests.append({'id': str(request.pk), 'name': request.name, 'positions': rows})
                grouped_ids.update(str(row['id']) for row in rows)
        actions = []
        for kind, rows, allowed in [('need', positions, can_needs), ('campaign', campaigns, can_campaigns)]:
            if not allowed:
                continue
            for row in rows:
                if row['review_state'] not in {'draft', 'returned'} or row['record_status'] in {'closed', 'cancelled', 'completed'}:
                    continue
                feedback = projections.feedback(actor, organization_id, **{kind + '_id': row['id']}) if row['review_state'] == 'returned' else []
                actions.append({'title': row.get('title') or row.get('name'),
                    'label': 'Review requested changes' if row['review_state'] == 'returned' else 'Continue draft',
                    'url': reverse('customer_portal:' + kind, args=[row['id']]),
                    'feedback': feedback[0] if feedback else None})
        recent = projections.history(actor, organization_id)
        for candidate in candidates:
            url = reverse('customer_portal:recruiting-candidate', args=[candidate['id']])
            if candidate['can_respond'] and not candidate['latest_response']:
                actions.append({'title': candidate['candidate'].get('name') or candidate['position'],
                    'label': 'Review released candidate', 'url': url})
            for item in employer_history(actor, organization_id, candidate['id'], limit=8):
                recent.append({'label': item['type'] + ' · ' + item['summary'],
                    'subject': candidate['position'], 'at': item['occurred_at'],
                    'url': reverse('crm:employer-detail', args=[candidate['id'], item['id']])})
        recent.sort(key=lambda item: item['at'], reverse=True)
        return {'positions': positions, 'requests': requests,
            'standalone_positions': [row for row in positions if str(row['id']) not in grouped_ids],
            'recruiting_campaigns': campaigns,
            'released_candidates': candidates, 'action_required': actions[:8],
            'action_required_total': len(actions), 'customer_recent': recent[:8],
            'active_positions': [row for row in positions if row['record_status'] == 'active'],
            'active_campaigns': [row for row in campaigns if row['record_status'] == 'active']}

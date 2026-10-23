"""Read-only participant projection over accepted, authoritative activity records.

Quiz output follows the existing kiosk result labels. Simulator display is an
explicit owner-approved policy (2026-10-23); acceptance is not a legacy release flag.
"""
from django.core.paginator import Paginator
from apps.event_reports.activity import activity_sources
from .identity import identity_members
from .portal_services import own_account


def profile_assessment_results(user, page=1):
    account = own_account(user)
    people = identity_members(account.participant_id)
    sources = activity_sources()
    quizzes = sources['finished_quizzes'].filter(participant_id__in=people).select_related(
        'experience_session__event').prefetch_related('sections__category', 'sections__responses')
    captures = sources['captures'].filter(participant_id__in=people,
        total_score__isnull=False, total_score__gte=0, total_score__lte=100).select_related('event', 'profile')
    # Preview stays inside its one private event, even if future identity code changes.
    from .portal_preview import current_fixture
    fixture = current_fixture(user)
    if fixture:
        quizzes = quizzes.filter(experience_session__event=fixture.event,
                                 experience_session__participant_id=fixture.participant_id)
        captures = captures.filter(event=fixture.event, participant_id=fixture.participant_id)
    results = []
    for attempt in quizzes:
        sections = list(attempt.sections.all())
        # A final attempt must contain both completed category sections. Do not
        # turn malformed or unfinished stored records into published results.
        if len(sections) != 2 or any(s.status != 'complete' or not s.completed_at or not s.questions_snapshot for s in sections):
            continue
        detail = [{'name': s.category_name_snapshot or s.category.name,
                   'correct': s.correct_count} for s in sections]
        results.append({'key': 'quiz-' + str(attempt.pk), 'type': 'Quiz',
            'date': attempt.completed_at, 'event': attempt.experience_session.event.name,
            'timezone': attempt.experience_session.event.timezone_name,
            'score': str(attempt.total_correct) + ' correct', 'categories': detail})
    for capture in captures:
        # Raw identifiers, diagnostic metadata, reconciliation reasons and
        # evaluator notes are deliberately absent from this allowlisted payload.
        results.append({'key': 'simulator-' + str(capture.pk), 'type': 'Simulator',
            'date': capture.captured_at, 'event': capture.event.name, 'timezone': capture.event.timezone_name,
            'score': format(capture.total_score.normalize(), 'f') + '%', 'categories': []})
    results.sort(key=lambda row: (row['date'], row['key']), reverse=True)
    paginator = Paginator(results, 10)
    return {'assessment_page': paginator.get_page(page), 'assessment_count': paginator.count}


def profile_identity(person, work_entries, current_interests):
    given = person.preferred_name or person.first_name
    current = next((entry for entry in work_entries if entry.is_current and entry.role), None)
    return {'profile_name': ' '.join(part for part in (given, person.last_name) if part),
            'profile_initials': ((given[:1] if given else '') + person.last_name[:1]).upper(),
            'profile_location': ', '.join(part for part in (person.city, person.state) if part),
            'profile_current_role': current.role if current else '',
            'profile_interests': current_interests}

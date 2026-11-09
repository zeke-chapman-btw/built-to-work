"""Presentation names only; actor references are unchanged."""
def compact_actor(actor):
    if actor is None:
        return 'Unavailable account'
    first = (getattr(actor, 'first_name', '') or '').strip()
    last = (getattr(actor, 'last_name', '') or '').strip()
    if not (first and last):
        profile = getattr(actor, 'staff_profile', None)
        profile_first = (getattr(profile, 'first_name', '') or '').strip()
        profile_last = (getattr(profile, 'last_name', '') or '').strip()
        if profile_first and profile_last:
            first, last = profile_first, profile_last
        elif not (first or last):
            first, last = profile_first, profile_last
    if first and last:
        return first + ' ' + last[0] + '.'
    if first or last:
        return first or last
    return (getattr(actor, 'username', '') or '').strip() or 'Unavailable account'

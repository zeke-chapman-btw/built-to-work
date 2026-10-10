from django.db import transaction
from django.core.exceptions import ValidationError
from .models import TicketAllocationBlock

def allocate_ticket_number(*, block_name="cloud"):
    with transaction.atomic():
        defaults={"test": (0, 0), "cloud": (1, 3999999999), "trailer1": (4000000000, 4999999999), "trailer2": (5000000000, 5999999999)}
        if block_name not in defaults:
            raise ValidationError("Unknown ticket allocation block.")
        start,end=defaults[block_name]
        TicketAllocationBlock.objects.get_or_create(name=block_name, defaults={"start_number": start, "end_number": end, "next_number": start})
        block=TicketAllocationBlock.objects.select_for_update().get(name=block_name, is_active=True)
        if block.next_number > block.end_number:
            raise ValidationError("No ticket numbers remain in this allocation block.")
        value=block.next_number
        block.next_number += 1
        block.save(update_fields=("next_number",))
        return f"{value:010d}"

def qr_payload(ticket_number):
    return f"tel:{ticket_number}"

def resolve_ticket_number(ticket_number, *, expected_event=None):
    from .models import QrTicket
    qs=QrTicket.objects.select_related("registration__event","registration__participant").filter(ticket_number=ticket_number, is_current=True)
    ticket=qs.first()
    if not ticket: return None
    if expected_event is not None and ticket.registration.event_id != expected_event.id: return None
    return ticket
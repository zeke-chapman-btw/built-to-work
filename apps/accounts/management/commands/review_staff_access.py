"""Read-only private rollout inventory. Never assigns roles."""
import json,os
from pathlib import Path
from django.core.management.base import BaseCommand,CommandError
from django.contrib.auth import get_user_model
from apps.accounts.roles import roles_for
class Command(BaseCommand):
    help="Read-only staff mapping inventory; private details require --private-output."
    def add_arguments(self,parser):
        parser.add_argument('--private-output')
    def handle(self,*args,**options):
        rows=[]
        for user in get_user_model().objects.filter(is_staff=True).order_by('pk'):
            rows.append(dict(id=str(user.pk),username=user.username,active=user.is_active,roles=sorted(roles_for(user)),groups=list(user.groups.values_list('name',flat=True)),direct_permissions=sorted(user.get_user_permissions()),owner_decision_required=not user.is_superuser))
        self.stdout.write(json.dumps(dict(staff_accounts=len(rows),unmapped=sum(not r['roles'] for r in rows),with_direct_grants=sum(bool(r['direct_permissions']) for r in rows),automatic_changes=0)))
        if options['private_output']:
            path=Path(options['private_output']).expanduser()
            try:
                fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
                with os.fdopen(fd,'w') as output:json.dump(rows,output,indent=2)
            except OSError as exc:raise CommandError('Private inventory must be a new file in a protected location.') from exc
            self.stdout.write('Private inventory created with owner-only permissions. Do not commit or publish it.')

import json
from django.core.management.base import BaseCommand
from apps.accounts.roles import role_configuration
class Command(BaseCommand):
    help='Show versioned staff-role permission diff. --apply changes definitions only; never enrolls accounts.'
    def add_arguments(self,parser):parser.add_argument('--apply',action='store_true')
    def handle(self,*args,**options):self.stdout.write(json.dumps({'applied':options['apply'],'roles':role_configuration(apply=options['apply'])},indent=2))

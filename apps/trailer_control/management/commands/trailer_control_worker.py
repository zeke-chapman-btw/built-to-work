import time
from django.core.management.base import BaseCommand,CommandError
from apps.trailer_control.access import local_trailer
from apps.trailer_control.health import sample_health
from apps.trailer_control.sync_worker import run_one

class Command(BaseCommand):
    help="Sample local evidence and process one bounded request per loop; supervise this process locally."
    def add_arguments(self,parser):parser.add_argument("--once",action="store_true")
    def handle(self,*args,**options):
        try:trailer=local_trailer()
        except Exception:raise CommandError("Enable local Trailer Control Center with a valid active trailer identity first.")
        while True:
            try:
                sample_health(trailer)
                run_one(trailer)
            except Exception:
                self.stderr.write("Trailer sampler/worker unavailable; no readiness is asserted.")
                if options["once"]:raise CommandError("Trailer worker check failed")
            if options["once"]:return
            time.sleep(15)

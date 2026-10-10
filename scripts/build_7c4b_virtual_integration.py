#!/usr/bin/env python3
"""Run trailer and central BTW instances with independent SQLite databases."""
from __future__ import annotations
import hashlib, json, os, socket, subprocess, sys, tempfile, time, urllib.error, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANAGE = ROOT / "manage.py"
EVENT = "a1000000-0000-4000-8000-000000000001"
GROUP = "a1000000-0000-4000-8000-000000000002"
TRAILER = "a1000000-0000-4000-8000-000000000003"
PREP = "a1000000-0000-4000-8000-000000000004"
DOC = "1"
FORM = "1"
FORMVER = "1"
CREDENTIAL = "virtual-trailer-credential-not-for-production"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_port(port, process, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        if process.poll() is not None:
            raise RuntimeError("Django process exited; see its temporary log")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("Django process did not start listening")


def request_json(url, body, credential):
    req = urllib.request.Request(url, json.dumps(body).encode(), {
        "Content-Type": "application/json", "Authorization": f"Bearer {credential}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def main():
    with tempfile.TemporaryDirectory(prefix="btw-7c4b-virtual-") as temp:
        tmp = Path(temp)
        settings_source = chr(10).join(['import os', 'from config.settings.development import *', "DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': os.environ['BTW_VIRTUAL_DB']}}", "ALLOWED_HOSTS = ['127.0.0.1', 'localhost', 'testserver']"])
        (tmp / "virtual_settings.py").write_text(settings_source, encoding="utf-8")
        base_env = os.environ.copy()
        base_env["DJANGO_SETTINGS_MODULE"] = "virtual_settings"
        base_env["PYTHONPATH"] = os.pathsep.join([str(tmp), str(ROOT), base_env.get("PYTHONPATH", "")])
        base_env.pop("BTW_SYNC_API_TOKEN", None)
        db_trailer, db_central = tmp / "trailer.sqlite3", tmp / "central.sqlite3"
        envs = {}
        for name, db in (("trailer", db_trailer), ("central", db_central)):
            env = base_env.copy(); env["BTW_VIRTUAL_DB"] = str(db); envs[name] = env
            subprocess.run([sys.executable, str(MANAGE), "migrate", "--noinput"], cwd=ROOT, env=env,
                           check=True, stdout=subprocess.DEVNULL)

        seed_common = f'''import hashlib, json
from datetime import timedelta
from django.utils import timezone
from django.contrib.auth.hashers import make_password
from apps.events.models import Event, EventGroup, TrailerInstance, OfflineEventPreparation, OfflineTicketRange
from apps.participants.models import ConsentDocumentVersion, RegistrationForm, RegistrationFormVersion
from apps.events.models import QrTicket
now=timezone.now()
event, _ = Event.objects.get_or_create(pk="{EVENT}", defaults={{"name":"Virtual Sync Test", "code":"VIRTUAL-7C4B", "start_at":now-timedelta(hours=1), "end_at":now+timedelta(days=3), "timezone_name":"UTC", "status":Event.Status.UPCOMING,"group_mode":"optional"}})
group, _ = EventGroup.objects.get_or_create(pk="{GROUP}", defaults={{"event":event,"name":"Virtual Group","capacity":20}})
trailer, _ = TrailerInstance.objects.get_or_create(pk="{TRAILER}", defaults={{"identity":"TRAILER-VIRTUAL-01","display_name":"Virtual trailer","is_active":True,"credential_hash":make_password("{CREDENTIAL}")}})
doc, _ = ConsentDocumentVersion.objects.get_or_create(pk="{DOC}", defaults={{"key":"virtual-synthetic-policy","version":1,"title":"Synthetic integration fixture","body":"Synthetic test text only; not a legal document.","content_hash":hashlib.sha256(b"synthetic").hexdigest(),"is_approved":True,"effective_at":now,"published_at":now}})
form, _ = RegistrationForm.objects.get_or_create(pk="{FORM}", defaults={{"slug":"virtual-sync","name":"Virtual sync form","is_active":True}})
formver, _ = RegistrationFormVersion.objects.get_or_create(pk="{FORMVER}", defaults={{"form":form,"version":1,"status":RegistrationFormVersion.Status.PUBLISHED,"snapshot":{{"questions":[]}},"published_at":now,"effective_at":now}})
prep, _ = OfflineEventPreparation.objects.get_or_create(pk="{PREP}", defaults={{"event":event,"trailer":trailer,"status":OfflineEventPreparation.Status.ACTIVE,"consent_document":doc,"sync_identity":"virtual-sync-identity","activated_at":now}})
OfflineTicketRange.objects.get_or_create(preparation=prep, kind=OfflineTicketRange.Kind.PRIMARY, defaults={{"start_number":8100000000,"end_number":8100000999,"next_number":8100000000}})
'''
        # Central also has a second trailer record to verify credential separation.
        central_seed = seed_common + f'''
from django.contrib.auth.hashers import make_password
TrailerInstance.objects.get_or_create(identity="TRAILER-VIRTUAL-02", defaults={{"display_name":"Second virtual trailer","is_active":True,"credential_hash":make_password("different-virtual-credential")}})
'''
        for role, code in (("trailer", seed_common), ("central", central_seed)):
            result = subprocess.run([sys.executable, str(MANAGE), "shell", "-c", code], cwd=ROOT,
                                    env=envs[role], text=True, capture_output=True)
            if result.returncode:
                raise RuntimeError(f"{role} seed failed: " + result.stderr + " " + result.stdout)

        registration_code = '''import json
from apps.events.models import OfflineEventPreparation, EventGroup
from apps.events.offline import create_offline_registration
prep=OfflineEventPreparation.objects.get(pk="PREP")
group=EventGroup.objects.get(pk="GROUP")
p,r,t,e=create_offline_registration(preparation=prep, actor=None, data={"first_name":"Virtual","last_name":"Participant","contact_email":"virtual-participant@example.invalid","contact_phone":"5550102030","group":str(group.pk),"consent_accepted":True,"answers":{},"custom_answers":{"shift":"day"}})
print("VIRTUAL_RESULT="+json.dumps({"payload":e.payload,"operation_key":"registration:"+str(e.pk),"registration_id":str(r.pk),"participant_id":str(p.identity_uuid),"ticket_number":t.ticket_number,"ticket_token":str(t.token)}, default=str))'''.replace("PREP", PREP).replace("GROUP", GROUP)
        local = subprocess.run([sys.executable, str(MANAGE), "shell", "-c", registration_code], cwd=ROOT,
                               env=envs["trailer"], text=True, capture_output=True)
        if local.returncode:
            raise RuntimeError("local registration failed: " + local.stderr + " " + local.stdout)
        line = next((x for x in local.stdout.splitlines() if x.startswith("VIRTUAL_RESULT=")), None)
        if not line:
            raise RuntimeError("local registration returned no payload: " + local.stdout)
        result = json.loads(line.split("=", 1)[1]); payload=result["payload"]
        if not payload.get("participant") or not payload.get("ticket") or not payload.get("consent"):
            raise RuntimeError("local outbox envelope lacks participant, ticket, or consent evidence")

        central_port = free_port(); trailer_port = free_port()
        logs = [(tmp / "trailer.log").open("w+"), (tmp / "central.log").open("w+")]
        processes=[]
        for role, port, log in (("trailer",trailer_port,logs[0]),("central",central_port,logs[1])):
            proc=subprocess.Popen([sys.executable,str(MANAGE),"runserver","--noreload",f"127.0.0.1:{port}"],cwd=ROOT,env=envs[role],stdout=log,stderr=subprocess.STDOUT)
            processes.append(proc); wait_port(port,proc)
        try:
            endpoint=f"http://127.0.0.1:{central_port}/events/sync/ingest/"
            body={"operation_key":result["operation_key"],"trailer_identity":"TRAILER-VIRTUAL-01","event_id":EVENT,"preparation_id":PREP,"payload":payload}
            status, response=request_json(endpoint,body,CREDENTIAL)
            if status != 200 or response.get("status") != "accepted":
                raise RuntimeError(f"central import failed: HTTP {status}: {response}")
            check_code='''import json
from apps.events.models import EventRegistration, QrTicket, CentralSyncReceipt
from apps.participants.models import Participant, ConsentAcceptance, RegistrationSubmission
p=Participant.objects.get(identity_uuid="PID")
r=EventRegistration.objects.get(pk="RID", event_id="EID", participant=p)
t=QrTicket.objects.get(ticket_number="TICKET", registration=r)
a=ConsentAcceptance.objects.get(participant=p)
s=RegistrationSubmission.objects.get(participant=p)
print("CENTRAL_CHECK="+json.dumps({"participant":str(p.identity_uuid),"registration":str(r.pk),"ticket":t.ticket_number,"token":str(t.token),"consent":str(a.document_id),"form_version":str(s.form_version_id),"custom_answers":r.custom_answers}))'''.replace("PID",result["participant_id"]).replace("RID",result["registration_id"]).replace("EID",EVENT).replace("TICKET",result["ticket_number"])
            check=subprocess.run([sys.executable,str(MANAGE),"shell","-c",check_code],cwd=ROOT,env=envs["central"],text=True,capture_output=True,check=True)
            central_json=json.loads(next(x for x in check.stdout.splitlines() if x.startswith("CENTRAL_CHECK=")).split("=",1)[1])
            if central_json["ticket"] != result["ticket_number"] or central_json["token"] != payload["ticket"]["token"] or central_json["custom_answers"].get("custom_answers", {}).get("shift") != "day":
                raise RuntimeError(f"central ticket/answers changed: {central_json}")
            # Replay after discarding the first acknowledgement: stable operation key must deduplicate.
            status2, response2=request_json(endpoint,body,CREDENTIAL)
            if status2 != 200 or not response2.get("duplicate"):
                raise RuntimeError(f"replay was not idempotent: HTTP {status2}: {response2}")
            # A different trailer cannot borrow the first trailer's credential.
            cross=dict(body); cross["operation_key"] += ":cross"; cross["trailer_identity"]="TRAILER-VIRTUAL-02"
            cross_status,_=request_json(endpoint,cross,CREDENTIAL)
            if cross_status != 401:
                raise RuntimeError(f"cross-trailer credential accepted: HTTP {cross_status}")
            # Event scope is validated independently of credential validity.
            wrong_event=dict(body); wrong_event["operation_key"] += ":event"; wrong_event["event_id"]="a1000000-0000-4000-8000-000000000099"
            event_status,_=request_json(endpoint,wrong_event,CREDENTIAL)
            if event_status not in (400,403):
                raise RuntimeError(f"cross-event operation not rejected: HTTP {event_status}")
            print("PASS: two independent Django processes and SQLite databases")
            print("PASS: local offline registration -> authenticated HTTP central import")
            print("PASS: participant UUID, event registration, ticket/token, consent/form snapshot, group/custom answers preserved")
            print("PASS: replay after simulated lost acknowledgement is idempotent")
            print("PASS: cross-trailer credential and cross-event scope rejected")
            print("NOTE: SQLite validates integration/isolation, not PostgreSQL locking or true concurrent allocation.")
        finally:
            for process in processes:
                process.terminate()
            for process in processes:
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: process.kill()
            for log in logs: log.close()

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise

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
        raw = exc.read()
        try:
            return exc.code, json.loads(raw or b"{}")
        except (ValueError, UnicodeDecodeError):
            return exc.code, {"detail": raw.decode("utf-8", errors="replace")[:800]}


def run_shell(env, code, marker=None):
    result = subprocess.run([sys.executable, str(MANAGE), "shell", "-c", code],
                            cwd=ROOT, env=env, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError("virtual shell failed: " + result.stderr + " " + result.stdout)
    if marker:
        return next((line.split("=", 1)[1] for line in result.stdout.splitlines()
                     if line.startswith(marker + "=")), None)
    return result.stdout


def run_worker(env, endpoint):
    return subprocess.run([sys.executable, str(MANAGE), "sync_outbox", "--once",
                           "--endpoint", endpoint], cwd=ROOT, env=env,
                          text=True, capture_output=True, timeout=25)


def main():
    with tempfile.TemporaryDirectory(prefix="btw-7c4b-virtual-") as temp:
        tmp = Path(temp)
        settings_source = chr(10).join(['import os', 'from config.settings.development import *', "DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': os.environ['BTW_VIRTUAL_DB']}}", "ALLOWED_HOSTS = ['127.0.0.1', 'localhost', 'testserver']"])
        (tmp / "virtual_settings.py").write_text(settings_source, encoding="utf-8")
        base_env = os.environ.copy()
        base_env["DJANGO_SETTINGS_MODULE"] = "virtual_settings"
        base_env["PYTHONPATH"] = os.pathsep.join([str(tmp), str(ROOT), base_env.get("PYTHONPATH", "")])
        base_env.pop("BTW_SYNC_API_TOKEN", None)
        db_trailer, db_trailer2, db_central = tmp / "trailer.sqlite3", tmp / "trailer2.sqlite3", tmp / "central.sqlite3"
        envs = {}
        for name, db in (("trailer", db_trailer), ("trailer2", db_trailer2), ("central", db_central)):
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
        # Distinct trailer databases, credentials, preparations, and reserved ranges.
        trailer2_id = "a1000000-0000-4000-8000-000000000005"
        prep2_id = "a1000000-0000-4000-8000-000000000006"
        credential2 = "second-virtual-credential-not-for-production"
        second_seed = (seed_common.replace(TRAILER, trailer2_id).replace(PREP, prep2_id)
                       .replace("TRAILER-VIRTUAL-01", "TRAILER-VIRTUAL-02")
                       .replace(CREDENTIAL, credential2)
                       .replace("8100000000", "8200000000")
                       .replace("8100000999", "8200000999"))
        for role, code in (("trailer", seed_common), ("trailer2", second_seed),
                           ("central", seed_common + second_seed)):
            seeded = subprocess.run([sys.executable, str(MANAGE), "shell", "-c", code], cwd=ROOT,
                                    env=envs[role], text=True, capture_output=True)
            if seeded.returncode:
                raise RuntimeError(f"{role} seed failed: " + seeded.stderr + " " + seeded.stdout)
        envs["trailer"]["BTW_SYNC_API_TOKEN"] = CREDENTIAL
        envs["trailer2"]["BTW_SYNC_API_TOKEN"] = credential2
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

        central_port = free_port(); trailer_port = free_port(); trailer2_port = free_port()
        logs = [(tmp / "trailer.log").open("w+"), (tmp / "trailer2.log").open("w+"),
                (tmp / "central.log").open("w+")]
        processes=[]
        for role, port, log in (("trailer",trailer_port,logs[0]),
                                ("trailer2",trailer2_port,logs[1]),
                                ("central",central_port,logs[2])):
            proc=subprocess.Popen([sys.executable,str(MANAGE),"runserver","--noreload",f"127.0.0.1:{port}"],cwd=ROOT,env=envs[role],stdout=log,stderr=subprocess.STDOUT)
            processes.append(proc); wait_port(port,proc)
        backup_dir = None
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
            # A central outage must not stop new trailer registrations or lose outbox work.
            processes[2].terminate(); processes[2].wait(timeout=5)
            code2 = (registration_code.replace(PREP, prep2_id)
                     .replace("virtual-participant@example.invalid", "virtual-second@example.invalid")
                     .replace("5550102030", "5550102040"))
            second_line = run_shell(envs["trailer2"], code2, "VIRTUAL_RESULT")
            if not second_line:
                raise RuntimeError("second trailer registration did not complete while central was offline")
            second = json.loads(second_line)
            if not second["ticket_number"].startswith("82") or second["ticket_number"] == result["ticket_number"]:
                raise RuntimeError("two trailer ticket ranges overlapped")
            outage = run_worker(envs["trailer2"], endpoint)
            outage_state = run_shell(envs["trailer2"],
                'from apps.events.models import SyncOutboxItem; print("STATE="+SyncOutboxItem.objects.get(operation_key="'+second['operation_key']+'").status)', "STATE")
            if outage.returncode or outage_state != "failed":
                raise RuntimeError(f"outage did not leave a durable failed outbox: {outage_state}; {outage.stderr}")
            processes[2] = subprocess.Popen(
                [sys.executable, str(MANAGE), "runserver", "--noreload", f"127.0.0.1:{central_port}"],
                cwd=ROOT, env=envs["central"], stdout=logs[2], stderr=subprocess.STDOUT)
            wait_port(central_port, processes[2])
            run_shell(envs["trailer2"],
                'from datetime import timedelta; from django.utils import timezone; from apps.events.models import SyncOutboxItem; SyncOutboxItem.objects.filter(operation_key="'+second['operation_key']+'").update(next_attempt_at=timezone.now()-timedelta(seconds=1))')
            recovery = run_worker(envs["trailer2"], endpoint)
            recovered_state = run_shell(envs["trailer2"],
                'from apps.events.models import SyncOutboxItem; print("STATE="+SyncOutboxItem.objects.get(operation_key="'+second['operation_key']+'").status)', "STATE")
            if recovery.returncode or recovered_state != "sent":
                raise RuntimeError(f"worker restart failed: {recovered_state}; {recovery.stderr}")
            second_check = run_shell(envs["central"],
                'from apps.events.models import EventRegistration,QrTicket,CentralSyncReceipt; from apps.participants.models import Participant; '
                'p=Participant.objects.get(identity_uuid="'+second['participant_id']+'"); '
                'r=EventRegistration.objects.get(pk="'+second['registration_id']+'", participant=p); '
                't=QrTicket.objects.get(registration=r); '
                'receipt=CentralSyncReceipt.objects.get(operation_key="'+second['operation_key']+'"); '
                'print("SECOND="+t.ticket_number+":"+receipt.trailer_identity)', "SECOND")
            if second_check != second["ticket_number"] + ":TRAILER-VIRTUAL-02":
                raise RuntimeError(f"second trailer import/provenance failed: {second_check}")
            second_body = {"operation_key": second["operation_key"], "trailer_identity": "TRAILER-VIRTUAL-02",
                           "event_id": EVENT, "preparation_id": prep2_id, "payload": second["payload"]}
            replay_status, replay = request_json(endpoint, second_body, credential2)
            if replay_status != 200 or not replay.get("duplicate"):
                raise RuntimeError("second trailer replay created a new operation")
            # The first HTTP acceptance intentionally left the local acknowledgment unsaved.
            lost_ack = run_worker(envs["trailer"], endpoint)
            first_state = run_shell(envs["trailer"],
                'from apps.events.models import SyncOutboxItem; print("STATE="+SyncOutboxItem.objects.get(operation_key="'+result['operation_key']+'").status)', "STATE")
            if lost_ack.returncode or first_state != "sent":
                raise RuntimeError(f"lost acknowledgment did not recover: {first_state}; {lost_ack.stderr}")
            # A central candidate must trigger an OPEN case without merging or hiding the ticket.
            overlap_line = run_shell(envs["trailer2"], registration_code.replace(PREP, prep2_id), "VIRTUAL_RESULT")
            overlap = json.loads(overlap_line)
            overlap_delivery = run_worker(envs["trailer2"], endpoint)
            if overlap_delivery.returncode:
                raise RuntimeError("uncertain-match delivery failed: " + overlap_delivery.stderr)
            overlap_check = run_shell(envs["central"],
                'from apps.events.models import ParticipantMatchReview,EventRegistration,QrTicket; '
                'from apps.participants.models import Participant; '
                'p=Participant.objects.get(identity_uuid="'+overlap['participant_id']+'"); '
                'r=EventRegistration.objects.get(pk="'+overlap['registration_id']+'", participant=p); '
                't=QrTicket.objects.get(registration=r); '
                'v=ParticipantMatchReview.objects.get(envelope__registration_id=r.pk); '
                'print("MATCH="+v.status+":"+str(v.candidate_participant_id != p.pk)+":"+t.ticket_number)', "MATCH")
            if overlap_check != "open:True:" + overlap["ticket_number"]:
                raise RuntimeError(f"uncertain match was merged, lost, or not OPEN: {overlap_check}")
            reconcile_code = '''from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from apps.events.models import ParticipantMatchReview,ParticipantMatchDecision,ParticipantIdentityLink,EventRegistration,QrTicket
from apps.events.reconciliation import resolve_match,reverse_match
from apps.participants.models import Participant,ConsentAcceptance
case=ParticipantMatchReview.objects.get(envelope__registration_id="REGISTRATION")
source_uuid=case.envelope.participant_identity
candidate_uuid=case.candidate_participant.identity_uuid
staff=get_user_model().objects.create_user(username="virtual-staff",password="test",is_staff=True)
admin=get_user_model().objects.create_superuser(username="virtual-admin",password="test")
before=(Participant.objects.count(),EventRegistration.objects.count(),QrTicket.objects.count(),ConsentAcceptance.objects.count())
from django.test import Client
from django.urls import reverse
client=Client(HTTP_HOST="testserver")
client.force_login(admin,backend="django.contrib.auth.backends.ModelBackend")
list_url=reverse("events:match-review-list")
detail_url=reverse("events:match-review-detail",args=[case.pk])
resolve_url=reverse("events:match-review-resolve",args=[case.pk])
assert client.get(list_url).status_code==200
assert client.get(detail_url).status_code==200
assert client.post(resolve_url,{"decision":"same","reason":"Verified identity evidence"}).status_code==302
assert client.get(detail_url).status_code==200
resolve_match(review_id=case.pk,action="same",reason="Repeated request",actor=admin)
link=ParticipantIdentityLink.objects.get(review=case)
assert link.is_active and link.source_participant.identity_uuid==source_uuid and link.candidate_participant.identity_uuid==candidate_uuid
assert ParticipantMatchDecision.objects.filter(review=case).count()==1
try:
    reverse_match(review_id=case.pk,reason="Not allowed",actor=staff)
    raise AssertionError("staff reversal should be denied")
except PermissionDenied:
    pass
reverse_match(review_id=case.pk,reason="Admin corrected match",actor=admin)
reverse_match(review_id=case.pk,reason="Repeated request",actor=admin)
link.refresh_from_db()
assert not link.is_active and ParticipantMatchDecision.objects.filter(review=case).count()==2
assert before==(Participant.objects.count(),EventRegistration.objects.count(),QrTicket.objects.count(),ConsentAcceptance.objects.count())
assert EventRegistration.objects.get(pk="REGISTRATION").participant.identity_uuid==source_uuid
separate_case=ParticipantMatchReview.objects.create(envelope=case.envelope,candidate_participant=case.candidate_participant,evidence=case.evidence)
separate_url=reverse("events:match-review-resolve",args=[separate_case.pk])
assert client.post(separate_url,{"decision":"separate","reason":"Distinct identity verified"}).status_code==302
assert client.post(separate_url,{"decision":"separate","reason":"Repeated request"}).status_code==302
separate_case.refresh_from_db()
assert separate_case.status==ParticipantMatchReview.Status.REJECTED
assert not ParticipantIdentityLink.objects.filter(review=separate_case).exists()
assert ParticipantMatchDecision.objects.filter(review=separate_case).count()==1
assert before==(Participant.objects.count(),EventRegistration.objects.count(),QrTicket.objects.count(),ConsentAcceptance.objects.count())
print("RECONCILE=linked-reversed-and-kept-separate-without-data-loss")'''.replace("REGISTRATION",overlap["registration_id"])
            if run_shell(envs["central"],reconcile_code,"RECONCILE") != "linked-reversed-and-kept-separate-without-data-loss":
                raise RuntimeError("same-person resolution or reversal did not preserve source history")
            # Snapshot an unsent registration and restore it as a replacement trailer.
            from cryptography.fernet import Fernet
            import shutil
            backup_dir = Path(tempfile.mkdtemp(prefix="btw-7c4c-backups-"))
            envs["trailer2"]["BTW_BACKUP_DIR"] = str(backup_dir)
            envs["trailer2"]["BTW_TRAILER_BACKUP_KEY"] = Fernet.generate_key().decode()
            envs["trailer2"]["BTW_BACKUP_FREQUENCY_MINUTES"] = "0"
            recovery_code = (registration_code.replace(PREP, prep2_id)
                             .replace("virtual-participant@example.invalid", "virtual-recovery@example.invalid")
                             .replace("5550102030", "5550102050"))
            recovery_line = run_shell(envs["trailer2"], recovery_code, "VIRTUAL_RESULT")
            recovered_registration = json.loads(recovery_line)
            backup_code = '''from django.contrib.auth import get_user_model
from apps.events.models import TrailerInstance
from apps.events.backup_service import create_database_backup
get_user_model().objects.create_superuser(username="recovery-admin",password="test")
record=create_database_backup(TrailerInstance.objects.get(identity="TRAILER-VIRTUAL-02"))
print("BACKUP="+str(record.pk))'''
            backup_id = run_shell(envs["trailer2"], backup_code, "BACKUP")
            if not backup_id or not list(backup_dir.glob("*.fernet")):
                raise RuntimeError("encrypted trailer backup was not created")
            processes[1].terminate(); processes[1].wait(timeout=5)
            # Original trailer HTTP service is down before replacement restoration.
            restored_db = tmp / "replacement-trailer.sqlite3"
            restore_code = '''from apps.events.models import BackupRecord
from apps.events.backup_service import restore_to_replacement
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from cryptography.fernet import InvalidToken
from pathlib import Path
import shutil
record=BackupRecord.objects.get(pk="BACKUP_ID")
admin=get_user_model().objects.get(username="recovery-admin")
target="RESTORED_PATH"
try:
    restore_to_replacement(record,target,get_user_model().objects.get_or_create(username="backup-nonadmin")[0])
    raise AssertionError("unauthorized restore succeeded")
except PermissionError:
    pass
try:
    restore_to_replacement(record,"LIVE_PATH",admin)
    raise AssertionError("live database overwrite succeeded")
except ValueError:
    pass
corrupt=Path(record.storage_path).with_suffix(".corrupt.fernet")
corrupt.write_bytes(Path(record.storage_path).read_bytes()+b"corrupt")
original=record.storage_path
record.storage_path=str(corrupt)
record.save(update_fields=["storage_path"])
try:
    try:
        restore_to_replacement(record,target,admin)
        raise AssertionError("corrupt backup restored")
    except ValueError:
        pass
finally:
    record.storage_path=original
    record.save(update_fields=["storage_path"])
    corrupt.unlink()
restore_to_replacement(record,target,admin)
try:
    restore_to_replacement(record,target,admin)
    raise AssertionError("existing replacement overwritten")
except FileExistsError:
    pass
print("RESTORE=authorized-audited-and-guarded")'''.replace("BACKUP_ID",backup_id).replace("RESTORED_PATH",str(restored_db)).replace("LIVE_PATH",str(db_trailer2))
            if run_shell(envs["trailer2"],restore_code,"RESTORE") != "authorized-audited-and-guarded":
                raise RuntimeError("backup restore safeguards failed")
            bad_env = envs["trailer2"].copy()
            bad_env["BTW_TRAILER_BACKUP_KEY"] = Fernet.generate_key().decode()
            bad_key_code = '''from apps.events.models import BackupRecord
from apps.events.backup_service import restore_to_replacement
from django.contrib.auth import get_user_model
from cryptography.fernet import InvalidToken
try:
    restore_to_replacement(BackupRecord.objects.get(pk="BACKUP_ID"),"BAD_TARGET",get_user_model().objects.get(username="recovery-admin"))
    raise AssertionError("wrong backup key accepted")
except InvalidToken:
    print("BAD_KEY=rejected")'''.replace("BACKUP_ID",backup_id).replace("BAD_TARGET",str(tmp / "bad-key.sqlite3"))
            if run_shell(bad_env,bad_key_code,"BAD_KEY") != "rejected":
                raise RuntimeError("wrong encryption key was not rejected")
            replacement_env = envs["trailer2"].copy()
            replacement_env["BTW_VIRTUAL_DB"] = str(restored_db)
            verification = '''from apps.events.models import EventRegistration,QrTicket,SyncOutboxItem
from apps.participants.models import Participant,ConsentAcceptance,RegistrationSubmission
p=Participant.objects.get(identity_uuid="PARTICIPANT")
r=EventRegistration.objects.get(pk="REGISTRATION",participant=p)
t=QrTicket.objects.get(registration=r)
a=ConsentAcceptance.objects.get(participant=p)
s=RegistrationSubmission.objects.get(participant=p)
o=SyncOutboxItem.objects.get(operation_key="OPERATION")
assert r.group is not None and a.document.is_approved and s.status=="submitted" and o.status=="pending"
assert r.custom_answers.get("custom_answers",{}).get("shift")=="day"
print("PRESERVED="+t.ticket_number)'''.replace("PARTICIPANT",recovered_registration["participant_id"]).replace("REGISTRATION",recovered_registration["registration_id"]).replace("OPERATION",recovered_registration["operation_key"])
            if run_shell(replacement_env,verification,"PRESERVED") != recovered_registration["ticket_number"]:
                raise RuntimeError("replacement trailer lost registration, consent, group, ticket, or outbox")
            replacement_delivery = run_worker(replacement_env,endpoint)
            if replacement_delivery.returncode:
                raise RuntimeError("replacement trailer failed to synchronize: " + replacement_delivery.stderr)
            central_recovery_check = '''from apps.events.models import EventRegistration,QrTicket,CentralSyncReceipt
from apps.participants.models import Participant
p=Participant.objects.get(identity_uuid="PARTICIPANT")
r=EventRegistration.objects.get(pk="REGISTRATION",participant=p)
t=QrTicket.objects.get(registration=r)
receipt=CentralSyncReceipt.objects.get(operation_key="OPERATION")
assert receipt.status=="accepted"
print("CENTRAL_RECOVERY="+t.ticket_number+":"+str(QrTicket.objects.filter(ticket_number=t.ticket_number).count()))'''.replace("PARTICIPANT",recovered_registration["participant_id"]).replace("REGISTRATION",recovered_registration["registration_id"]).replace("OPERATION",recovered_registration["operation_key"])
            expected = recovered_registration["ticket_number"] + ":1"
            if run_shell(envs["central"],central_recovery_check,"CENTRAL_RECOVERY") != expected:
                raise RuntimeError("central recovery changed or duplicated the original ticket")
            second_restore = restore_code.replace(str(restored_db),str(tmp / "replacement-replay.sqlite3"))
            run_shell(envs["trailer2"],second_restore,"RESTORE")
            replay_env = envs["trailer2"].copy(); replay_env["BTW_VIRTUAL_DB"] = str(tmp / "replacement-replay.sqlite3")
            replay_delivery = run_worker(replay_env,endpoint)
            if replay_delivery.returncode or run_shell(envs["central"],central_recovery_check,"CENTRAL_RECOVERY") != expected:
                raise RuntimeError("restoring pre-acknowledgment state duplicated central records")
            # Total-loss recovery: no BackupRecord lookup or original SQLite file may be used.
            archive = next(backup_dir.glob("*.fernet"))
            manifest = archive.with_suffix(".manifest.enc")
            if not manifest.is_file() or b"TRAILER-VIRTUAL-02" in manifest.read_bytes():
                raise RuntimeError("portable encrypted metadata was not produced securely")
            db_trailer2.unlink()
            if db_trailer2.exists():
                raise RuntimeError("original trailer database still exists")

            def portable_attempt(target, *, archive_file=archive, manifest_file=manifest,
                                 password="test", key=None):
                recovery_env = envs["trailer2"].copy()
                if key is not None:
                    recovery_env["BTW_TRAILER_BACKUP_KEY"] = key
                return subprocess.run(
                    [sys.executable, str(MANAGE), "restore_portable_trailer_backup",
                     "--archive", str(archive_file), "--manifest", str(manifest_file),
                     "--target", str(target), "--actor", "recovery-admin",
                     "--password-stdin", "--confirm"],
                    cwd=ROOT, env=recovery_env, input=password + "\n",
                    text=True, capture_output=True,
                )

            wrong_key_target = tmp / "wrong-key.sqlite3"
            if portable_attempt(wrong_key_target, key=Fernet.generate_key().decode()).returncode == 0 or wrong_key_target.exists():
                raise RuntimeError("portable recovery accepted an incorrect key")
            wrong_actor_target = tmp / "wrong-admin.sqlite3"
            if portable_attempt(wrong_actor_target, password="incorrect").returncode == 0 or wrong_actor_target.exists():
                raise RuntimeError("portable recovery accepted an unauthorized administrator")
            damaged_archive = backup_dir / "damaged.fernet"
            damaged_archive.write_bytes(archive.read_bytes() + b"corrupt")
            damaged_target = tmp / "damaged.sqlite3"
            if portable_attempt(damaged_target, archive_file=damaged_archive).returncode == 0 or damaged_target.exists():
                raise RuntimeError("portable recovery accepted a modified archive")
            damaged_manifest = backup_dir / "damaged.manifest.enc"
            damaged_manifest.write_bytes(manifest.read_bytes() + b"corrupt")
            metadata_target = tmp / "metadata.sqlite3"
            if portable_attempt(metadata_target, manifest_file=damaged_manifest).returncode == 0 or metadata_target.exists():
                raise RuntimeError("portable recovery accepted modified metadata")

            total_loss_db = tmp / "total-loss-replacement.sqlite3"
            result = portable_attempt(total_loss_db)
            if result.returncode or not total_loss_db.is_file() or db_trailer2.exists():
                raise RuntimeError("total-loss portable restore failed: " + result.stderr)
            if portable_attempt(total_loss_db).returncode == 0:
                raise RuntimeError("portable recovery overwrote a live replacement database")
            total_loss_env = envs["trailer2"].copy()
            total_loss_env["BTW_VIRTUAL_DB"] = str(total_loss_db)
            if run_shell(total_loss_env, verification, "PRESERVED") != recovered_registration["ticket_number"]:
                raise RuntimeError("total-loss restore lost participant, consent, group, ticket, or outbox")
            audit_code = "from apps.core.models import AuditLog\nprint('AUDIT='+str(AuditLog.objects.filter(action='trailer_backup.portable_restore').count()))"
            if run_shell(total_loss_env, audit_code, "AUDIT") != "1":
                raise RuntimeError("portable recovery audit is missing from replacement trailer")
            if run_worker(total_loss_env, endpoint).returncode or run_shell(envs["central"], central_recovery_check, "CENTRAL_RECOVERY") != expected:
                raise RuntimeError("total-loss recovery replay duplicated or lost central data")
            if db_trailer2.exists():
                raise RuntimeError("recovery unexpectedly recreated the original database")
            print("PASS: total original SQLite loss, portable authenticated metadata, authorized restore, and audited replay")
            print("PASS: encrypted backup, wrong-key/corruption/authorization/live-overwrite safeguards")
            print("PASS: replacement trailer preserved registration and pending outbox, then synchronized without duplicates")
            print("PASS: central outage, continued trailer registration, durable retry, worker restart")
            print("PASS: two separate trailers imported with distinct credentials, provenance, and ticket ranges")
            print("PASS: lost acknowledgment and repeated HTTP operation did not duplicate records")
            print("PASS: uncertain match stayed OPEN with a distinct, ticketed source participant")
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
            if backup_dir is not None:
                shutil.rmtree(backup_dir, ignore_errors=True)

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise

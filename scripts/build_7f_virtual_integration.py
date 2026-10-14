#!/usr/bin/env python3
"""Two independent SQLite BTW instances for central-to-trailer Build 7F testing.

Run: python scripts/build_7f_virtual_integration.py
No production credentials or legal documents are used.
"""
from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANAGE = ROOT / "manage.py"
EVENT = "b7000000-0000-4000-8000-000000000001"
TRAILERS = (("TRAILER-7F-01", "7f-credential-one", "7000000000", "7000000099"),
            ("TRAILER-7F-02", "7f-credential-two", "7100000000", "7100000099"))


def shell(env, code):
    result = subprocess.run([sys.executable, str(MANAGE), "shell", "-c", code], cwd=ROOT,
                            env=env, text=True, capture_output=True, timeout=35)
    if result.returncode:
        raise RuntimeError(result.stdout + "\n" + result.stderr)
    return result.stdout.strip()


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait(port_number, process):
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError("Django server exited")
        try:
            with socket.create_connection(("127.0.0.1", port_number), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("Django server did not start")


def get(url, identity, credential, **params):
    target = url + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(target, headers={"Authorization": "Bearer " + credential,
                                                      "X-Trailer-Identity": identity})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def worker(env, endpoint):
    result = subprocess.run([sys.executable, str(MANAGE), "sync_central_events", "--once", "--force",
                             "--endpoint", endpoint], cwd=ROOT, env=env, text=True,
                            capture_output=True, timeout=50)
    if result.returncode:
        raise RuntimeError(result.stdout + "\n" + result.stderr)
    return result.stdout + result.stderr


def main():
    with tempfile.TemporaryDirectory(prefix="btw-7f-virtual-") as location:
        tmp = Path(location)
        (tmp / "virtual_settings.py").write_text("\n".join((
            "import os", "from config.settings.development import *",
            "DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': os.environ['BTW_VIRTUAL_DB']}}",
            "PRIVATE_CONSENT_ROOT = os.environ['BTW_VIRTUAL_PRIVATE_CONSENT']",
            "ALLOWED_HOSTS = ['127.0.0.1', 'localhost', 'testserver']")), encoding="utf-8")
        base = os.environ.copy()
        base["DJANGO_SETTINGS_MODULE"] = "virtual_settings"
        base["PYTHONPATH"] = os.pathsep.join((str(tmp), str(ROOT), base.get("PYTHONPATH", "")))
        envs = {}
        for name in ("central", "trailer1", "trailer2"):
            env = base.copy()
            env["BTW_VIRTUAL_DB"] = str(tmp / f"{name}.sqlite3")
            env["BTW_VIRTUAL_PRIVATE_CONSENT"] = str(tmp / name / "private")
            envs[name] = env
            subprocess.run([sys.executable, str(MANAGE), "migrate", "--noinput"], cwd=ROOT,
                           env=env, check=True, stdout=subprocess.DEVNULL)
        seed = f'''import hashlib
from datetime import timedelta
from django.utils import timezone
from django.core.files.base import ContentFile
from io import BytesIO
from pypdf import PdfWriter
from django.contrib.auth.hashers import make_password
from apps.events.models import Event, EventGroup, EventRegistrationQuestion, TrailerInstance, OfflineEventPreparation, OfflineTicketRange
from apps.participants.models import ConsentDocumentVersion, RegistrationForm, RegistrationFormVersion
now=timezone.now()
pdf_buffer=BytesIO(); writer=PdfWriter(); writer.add_blank_page(width=612,height=792); writer.write(pdf_buffer); pdf=pdf_buffer.getvalue()
doc=ConsentDocumentVersion.objects.create(key="virtual-7f",version="1",title="Synthetic 7F",body="Synthetic text only",content_hash=hashlib.sha256(pdf).hexdigest(),is_approved=True,effective_at=now-timedelta(days=1),published_at=now-timedelta(days=1))
doc.original_pdf.save("virtual.pdf",ContentFile(pdf),save=True)
form=RegistrationForm.objects.create(slug="virtual-7f",name="Virtual form")
RegistrationFormVersion.objects.create(form=form,version=1,status="published",snapshot={{"questions":[]}},published_at=now)
event=Event.objects.create(id="{EVENT}",name="Virtual Prepared Event",code="VIRTUAL-7F",start_at=now+timedelta(days=1),end_at=now+timedelta(days=2),status="upcoming",group_mode="required",public_registration_status="open",public_consent_document=doc)
EventGroup.objects.create(event=event,name="Morning",capacity=100)
EventRegistrationQuestion.objects.create(event=event,key="trade",label="Trade",is_required=True)
'''
        for identity, credential, start, end in TRAILERS:
            seed += f'''trailer=TrailerInstance.objects.create(identity="{identity}",display_name="{identity}",credential_hash=make_password("{credential}"))
prep=OfflineEventPreparation.objects.create(event=event,trailer=trailer,status="prepared",consent_document=doc,consent_hash=doc.content_hash,sync_identity=trailer.identity)
OfflineTicketRange.objects.create(preparation=prep,kind="primary",start_number={start},end_number={end},next_number={start})
OfflineTicketRange.objects.create(preparation=prep,kind="backup",start_number={int(end)+1},end_number={int(end)+100},next_number={int(end)+1})
'''
        shell(envs["central"], seed)
        envs["central"]["BTW_7F_TEST_PAGE_SIZE"] = "1"
        registration = f'''from datetime import timedelta
from django.utils import timezone
from apps.events.models import Event, EventRegistration, QrTicket
from apps.participants.models import Participant, RegistrationFormVersion, RegistrationSubmission, ConsentAcceptance, ConsentDocumentVersion
now=timezone.now(); event=Event.objects.get(pk="{EVENT}"); group=event.groups.get(name="Morning")
for i in range(2):
    participant=Participant.objects.create(first_name="Taylor",last_name=f"Example{{i}}",contact_email=f"t{{i}}@example.test")
    submission=RegistrationSubmission.objects.create(form_version=RegistrationFormVersion.objects.get(form__slug="virtual-7f",version=1),participant=participant,event=event,status="submitted",answers={{"first_name":"Taylor"}},answer_snapshot={{"event_questions":[{{"key":"trade"}}]}},submitted_at=now,source="public_online",idempotency_key=f"virtual-7f-{{i}}")
    ConsentAcceptance.objects.create(participant=participant,submission=submission,document=ConsentDocumentVersion.objects.get(key="virtual-7f"),metadata={{"pdf_sha256":event.public_consent_document.content_hash}})
    reg=EventRegistration.objects.create(event=event,participant=participant,group=group,source="online",custom_answers={{"trade":{{"answer":"Welding"}}}},registered_at=now)
    QrTicket.objects.create(registration=reg,ticket_number=f"{{1200000000+i:010d}}",issued_at=now,expires_at=now+timedelta(days=2))
'''
        shell(envs["central"], registration)
        central_port, trailer_port = port(), port()
        logs = [open(tmp / "central.log", "w"), open(tmp / "trailer.log", "w")]
        processes = []
        try:
            for name, value, logfile in (("central", central_port, logs[0]), ("trailer1", trailer_port, logs[1])):
                process = subprocess.Popen([sys.executable, str(MANAGE), "runserver", "--noreload", f"127.0.0.1:{value}"],
                                           cwd=ROOT, env=envs[name], stdout=logfile, stderr=subprocess.STDOUT)
                processes.append(process)
                wait(value, process)
            url = f"http://127.0.0.1:{central_port}/events/sync/download/"
            self_url = f"http://127.0.0.1:{trailer_port}/events/sync/download/"
            status, catalog = get(url, *TRAILERS[0][:2])
            assert status == 200 and len(catalog["events"]) == 1
            assert get(url, TRAILERS[0][0], "wrong", event_id=EVENT)[0] == 401
            assert get(url, "TRAILER-7F-02", TRAILERS[0][1], event_id=EVENT)[0] == 401
            assert get(url, TRAILERS[0][0], TRAILERS[0][1], event_id="00000000-0000-4000-8000-000000000001")[0] == 403
            print("PASS: per-trailer credentials and event scope")
            envs["trailer1"]["BTW_TRAILER_IDENTITY"] = TRAILERS[0][0]
            envs["trailer1"]["BTW_SYNC_API_TOKEN"] = TRAILERS[0][1]
            _, first = get(url, TRAILERS[0][0], TRAILERS[0][1], event_id=EVENT)
            assert first["next_cursor"] and len(first["records"]) == 1
            staged = base64.b64encode(json.dumps(first).encode()).decode()
            shell(envs["trailer1"], f'''import base64,json
from apps.events.models import CentralEventDownload,CentralEventDownloadPage
from django.utils.dateparse import parse_datetime
p=json.loads(base64.b64decode("{staged}"))
d=CentralEventDownload.objects.create(trailer_identity="TRAILER-7F-01",event_id=p["event_id"],preparation_id=p["preparation_id"],status="downloading",config=p["config"],snapshot_at=parse_datetime(p["snapshot_at"]),next_cursor=p["next_cursor"])
CentralEventDownloadPage.objects.create(download=d,page_number=0,records=p["records"])
''')
            processes[0].terminate(); processes[0].wait(timeout=5)
            worker(envs["trailer1"], url)
            shell(envs["trailer1"], '''from apps.events.models import CentralEventDownload,EventRegistration
assert CentralEventDownload.objects.get().next_cursor and EventRegistration.objects.count()==0''')
            central_process = subprocess.Popen([sys.executable, str(MANAGE), "runserver", "--noreload", f"127.0.0.1:{central_port}"],
                                               cwd=ROOT, env=envs["central"], stdout=logs[0], stderr=subprocess.STDOUT)
            processes.append(central_process); wait(central_port, central_process)
            print("PASS: interrupted page and offline catalog survive restart, then connectivity returns")
            for index, (identity, credential, _, _) in enumerate(TRAILERS, start=1):
                env = envs[f"trailer{index}"]
                env["BTW_TRAILER_IDENTITY"] = identity
                env["BTW_SYNC_API_TOKEN"] = credential
                result = worker(env, url)
                assert "Prepared event" in result, result
                check = f'''from apps.events.models import Event, EventRegistration, QrTicket, OfflineEventPreparation, CentralEventDownload
from apps.participants.models import Participant, ConsentAcceptance, RegistrationSubmission
from apps.events.services import resolve_ticket
event=Event.objects.get(pk="{EVENT}")
assert EventRegistration.objects.filter(event=event,source="online").count()==2
assert Participant.objects.count()==2 and QrTicket.objects.count()==2
assert ConsentAcceptance.objects.count()==2 and RegistrationSubmission.objects.count()==2
assert all(r.custom_answers["trade"]["answer"]=="Welding" for r in event.registrations.all())
assert event.groups.get(name="Morning").registrations.count()==2
assert resolve_ticket("tel:1200000000",expected_event=event).ticket.ticket_number=="1200000000"
assert CentralEventDownload.objects.get(event_id=event.pk).status=="ready"
assert OfflineEventPreparation.objects.get(event=event).consent_document.original_pdf
'''
                shell(env, check)
                worker(env, url)
                shell(env, check)
            print("PASS: two independent trailers preserve UUIDs, tickets, groups, answers, consent; replay is idempotent")
            assert shell(envs["central"], 'from apps.events.models import EventRegistration; print(EventRegistration.objects.count())').splitlines()[-1] == "2"
            print("PASS: central and trailer databases remain independent")
            shell(envs["central"], registration.replace("range(2)", "range(2,3)"))
            worker(envs["trailer1"], url)
            shell(envs["trailer1"], '''from apps.events.models import EventRegistration,QrTicket
assert EventRegistration.objects.count()==3 and QrTicket.objects.count()==3''')
            shell(envs["trailer2"], '''from apps.events.models import EventRegistration
assert EventRegistration.objects.count()==2''')
            print("PASS: incremental online registration download")
            shell(envs["central"], '''from django.contrib.auth import get_user_model
from apps.events.models import EventRegistration
from apps.events.services import void_registration
reg=EventRegistration.objects.get(tickets__ticket_number="1200000000")
actor=get_user_model().objects.create_user(username="virtual-void",password="synthetic",is_staff=True)
assert void_registration(registration=reg,actor=actor,reason="Virtual cancellation")''')
            worker(envs["trailer1"], url)
            shell(envs["trailer1"], '''from apps.events.models import EventRegistration,QrTicket
from apps.events.services import resolve_ticket
reg=EventRegistration.objects.get(tickets__ticket_number="1200000000")
ticket=QrTicket.objects.get(ticket_number="1200000000")
assert reg.status==EventRegistration.Status.VOID
assert not ticket.is_current and ticket.revoked_at is not None
assert resolve_ticket("tel:1200000000",expected_event=reg.event).status=="not_found"''')
            worker(envs["trailer1"], url)
            shell(envs["trailer1"], '''from apps.events.models import EventRegistration,QrTicket
assert EventRegistration.objects.count()==3 and QrTicket.objects.count()==3''')
            print("PASS: central cancellation invalidates trailer ticket; replay is idempotent")
            shell(envs["trailer1"], '''from apps.events.models import CentralEventDownload
CentralEventDownload.objects.update(checkpoint_at=None)''')
            worker(envs["trailer1"], url)
            shell(envs["trailer1"], '''from apps.events.models import EventRegistration,QrTicket
from apps.participants.models import Participant,ConsentAcceptance
assert EventRegistration.objects.count()==3 and QrTicket.objects.count()==3
assert Participant.objects.count()==3 and ConsentAcceptance.objects.count()==3''')
            print("PASS: lost local acknowledgment replay does not duplicate records")
        finally:
            for process in processes:
                process.terminate()
            for process in processes:
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: process.kill()
            for logfile in logs: logfile.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("FAIL:", exc, file=sys.stderr)
        raise

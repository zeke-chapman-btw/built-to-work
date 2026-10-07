from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.assessments.models import QuizAttempt
from apps.events.models import Attendance, Event, EventRegistration, QrTicket
from apps.participants.models import Participant
from apps.stations.models import EventStation, ExperienceActivity, ExperienceSession, Station
from apps.stations.services import _ensure_activity_rows, activity_order_for_session
from apps.games import duck_hunt as duck
from apps.games.models import EventGameConfiguration, GameDefinition, GameSession
from apps.games.services import resolve_and_start, reset_nonofficial_session


class DuckHuntTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.event = Event.objects.create(name="Duck test", code="DUCK-TEST", start_at=now-timedelta(days=1), end_at=now+timedelta(days=1))
        self.station = Station.objects.create(code="KIOSK-TEST", name="Test", station_type=Station.Type.KIOSK)
        EventStation.objects.create(event=self.event, station=self.station, enabled=True)
        self.game = GameDefinition.objects.get(key="duck_hunt")
        EventGameConfiguration.objects.create(event=self.event, game=self.game)
        self.experience = self.experience_for()
        browser = self.client.session
        browser["test_kiosk_session"] = str(self.experience.pk)
        browser.save()
        self.run = duck.prepare(resolve_and_start(experience_session=self.experience))

    def experience_for(self, mode=ExperienceSession.Mode.STAFF_TEST):
        identity = {}
        if mode == ExperienceSession.Mode.OFFICIAL:
            p = Participant.objects.create(first_name="Synthetic", last_name="Duck")
            reg = EventRegistration.objects.create(event=self.event, participant=p)
            identity = {"participant": p, "registration": reg}
        exp = ExperienceSession.objects.create(event=self.event, mode=mode, **identity)
        _ensure_activity_rows(exp)
        exp.activities.filter(activity=ExperienceActivity.Activity.KIOSK).update(status=ExperienceActivity.Status.COMPLETED)
        QuizAttempt.objects.create(experience_session=exp, participant=identity.get("participant"), is_official=mode == "official", status=QuizAttempt.Status.COMPLETE)
        return exp

    def url(self, action, run=None, experience=None):
        kwargs = {"station_code": self.station.code, "session_id": (experience or self.experience).pk}
        if action.startswith("duck_"):
            kwargs["game_session_id"] = (run or self.run).pk
        return reverse("games:"+action, kwargs=kwargs)

    def elapsed(self, run=None):
        run = duck.activate(run or self.run)
        run.started_at = timezone.now()-timedelta(seconds=46)
        run.save(update_fields=("started_at",))
        return run

    def shot(self, run=None):
        run = run or self.run
        snapshot = run.configuration_snapshot["duck_hunt"]
        target = snapshot["targets"][0]
        at = target["start"]+target["flight"]*.5
        x, y = duck.target_position(target, at, snapshot["rules"])
        return {"t": at, "x": x, "y": y}

    def test_generic_launch_renders_duck_and_reuses_session(self):
        response = self.client.get(self.url("launch"))
        self.assertContains(response, "SHOOT THE TARGET TO START")
        self.assertContains(response, "TEST MODE")
        self.assertContains(response, "games/duck_hunt/game.js")
        self.assertNotContains(response, "<span>KILLS</span>")
        self.assertEqual(GameSession.objects.count(), 1)
        self.client.get(self.url("launch"))
        self.assertEqual(GameSession.objects.count(), 1)

    def test_launch_does_not_start_round(self):
        self.client.get(self.url("launch"))
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, GameSession.Status.NOT_STARTED)
        self.assertIsNone(self.run.started_at)

    def test_activation_starts_after_countdown_and_cannot_repeat(self):
        now = timezone.now()
        with patch("apps.games.duck_hunt.timezone.now", return_value=now):
            response = self.client.post(self.url("duck_start"))
        self.assertEqual(response.status_code, 200)
        self.run.refresh_from_db()
        self.assertEqual(self.run.started_at, now+timedelta(milliseconds=3600))
        self.assertEqual(self.client.post(self.url("duck_start")).status_code, 409)

    def test_wrong_browser_cannot_access_or_start_official_round(self):
        exp = self.experience_for("official")
        official = duck.prepare(resolve_and_start(experience_session=exp))
        self.assertEqual(self.client.get(self.url("launch", experience=exp)).status_code, 404)
        self.assertEqual(self.client.post(self.url("duck_start", official, exp)).status_code, 404)
        self.assertEqual(self.client.post(self.url("duck_finish", official, exp), {"shots": []}, content_type="application/json").status_code, 404)

    def test_foreign_run_under_authorized_experience_is_rejected(self):
        other = self.experience_for()
        run = duck.prepare(resolve_and_start(experience_session=other))
        self.assertEqual(self.client.post(self.url("duck_start", run)).status_code, 404)

    def test_event_and_mode_mismatch_rejected(self):
        self.run.mode = "demo"
        self.run.save(update_fields=("mode",))
        with self.assertRaises(ValidationError):
            duck.activate(self.run)
        self.run.mode = "staff_test"
        now = timezone.now()
        self.run.event = Event.objects.create(name="Other", code="OTHER", start_at=now, end_at=now+timedelta(days=1))
        self.run.save(update_fields=("mode", "event"))
        with self.assertRaises(ValidationError):
            duck.activate(self.run)

    def test_assessment_required_before_launch_and_start(self):
        QuizAttempt.objects.filter(experience_session=self.experience).update(status=QuizAttempt.Status.ACTIVE)
        self.assertEqual(self.client.get(self.url("launch")).status_code, 302)
        self.assertEqual(self.client.post(self.url("duck_start")).status_code, 409)

    def test_valid_completion_stores_server_calculated_metrics(self):
        run = self.elapsed()
        result = duck.accept_result(run, [self.shot(), {"t": 44000, "x": 10, "y": 1050}])
        data = result.result_data["duck_hunt"]
        self.assertEqual(data["kills"], 1)
        self.assertEqual(data["shots"], 2)
        self.assertEqual(data["accuracy"], 50)
        self.assertIn(result.raw_score, (10, 15, 20, 25, 30, 35, 40))
        self.assertEqual(result.raw_score, data["score"])
        self.assertEqual(data["mode"], "staff_test")
        self.assertEqual(data["opportunities"], len(run.configuration_snapshot["duck_hunt"]["targets"]))
        self.assertNotIn("shot_ledger", data)

    def test_official_completion_finishes_game_and_unlocks_simulator(self):
        exp = self.experience_for("official")
        run = duck.prepare(resolve_and_start(experience_session=exp))
        result = duck.accept_result(self.elapsed(run), [])
        self.assertEqual(result.status, GameSession.Status.COMPLETED)
        self.assertEqual(exp.activities.get(activity="game").status, ExperienceActivity.Status.COMPLETED)
        rows = {a.activity: a for a in exp.activities.all()}
        self.assertEqual(next(a for a in activity_order_for_session(exp) if rows[a].status == "pending"), "simulator")

    def test_duplicate_submission_retains_first_result(self):
        run = self.elapsed()
        first = duck.accept_result(run, [])
        second = duck.accept_result(run, [self.shot()])
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(second.raw_score, 0)
        self.assertEqual(GameSession.objects.count(), 1)

    def test_voided_and_replaced_rounds_reject_results(self):
        run = self.elapsed()
        reset_nonofficial_session(game_session=run)
        replacement = resolve_and_start(experience_session=self.experience)
        run.refresh_from_db()
        self.assertEqual(run.replaced_by_id, replacement.pk)
        with self.assertRaises(ValidationError):
            duck.accept_result(run, [])

    def test_test_and_demo_results_never_complete_official_activity(self):
        official = self.experience_for("official")
        for mode in ("staff_test", "demo"):
            exp = self.experience_for(mode)
            run = duck.prepare(resolve_and_start(experience_session=exp))
            completed = duck.accept_result(self.elapsed(run), [])
            self.assertFalse(completed.is_official)
        self.assertEqual(official.activities.get(activity="game").status, "pending")
        self.assertFalse(official.game_sessions.exists())

    def test_no_game_path_remains_unchanged(self):
        self.event.game_configuration.delete()
        self.assertIsNone(resolve_and_start(experience_session=self.experience))
        response = self.client.get(self.url("launch"))
        self.assertRedirects(response, self.url("simulator_next"))

    def test_premature_completion_rejected(self):
        with self.assertRaises(ValidationError):
            duck.accept_result(self.run, [])
        run = duck.activate(self.run)
        with self.assertRaises(ValidationError):
            duck.accept_result(run, [])

    def test_malformed_impossible_and_nonfinite_shots_rejected(self):
        snapshot = self.run.configuration_snapshot["duck_hunt"]
        for shots in (None, {}, [{"score": 999}], [{"t": -1,"x": 2,"y": 2}], [{"t": 45000,"x": 1,"y": 1}], [{"t": 1,"x": float("nan"),"y": 1}], [{"t": 1,"x": True,"y": 1}], [{"t": 1,"x": 9999,"y": 1}], [{"t": 2,"x": 1,"y": 1},{"t": 1,"x": 1,"y": 1}]):
            with self.subTest(shots=shots), self.assertRaises(ValidationError):
                duck.evaluate_shots(shots, snapshot)

    def test_forged_score_field_rejected_by_http(self):
        self.elapsed()
        response = self.client.post(self.url("duck_finish"), {"shots": [], "score": 999999}, content_type="application/json")
        self.assertEqual(response.status_code, 409)
        self.run.refresh_from_db()
        self.assertIsNone(self.run.raw_score)

    def test_zero_shots_accuracy_is_zero(self):
        data = duck.evaluate_shots([], self.run.configuration_snapshot["duck_hunt"])
        self.assertEqual((data["score"], data["kills"], data["accuracy"]), (0,0,0))

    def test_matrix_and_overlap_one_target_per_shot(self):
        snapshot = deepcopy(self.run.configuration_snapshot["duck_hunt"])
        t = snapshot["targets"][0]
        t.update(start=0, end=7000, flight=7000, size=160)
        t2 = dict(t, id=999)
        snapshot["targets"] = [t, t2]
        x,y = duck.target_position(t,3500)
        result = duck.evaluate_shots([{"t":3500,"x":x,"y":y}],snapshot)
        self.assertEqual(result["kills"],1)
        self.assertEqual(result["score"],t["points"])
        for d,row in duck.RULES["scores"].items():
            for s,points in row.items():
                t.update(distance=d,speed=s,points=points)
                snapshot["targets"]=[t]
                self.assertEqual(duck.evaluate_shots([{"t":3500,"x":x,"y":y}],snapshot)["score"],points)

    def test_same_target_cannot_score_twice(self):
        snapshot = deepcopy(self.run.configuration_snapshot["duck_hunt"])
        t = snapshot["targets"][0]
        snapshot["targets"] = [t]
        at=t["start"]+t["flight"]/2
        x,y=duck.target_position(t,at)
        result=duck.evaluate_shots([{"t":at,"x":x,"y":y},{"t":at+20,"x":x,"y":y}],snapshot)
        self.assertEqual((result["kills"],result["shots"]),(1,2))

    def test_flight_durations_are_about_twenty_percent_faster(self):
        original_flight_ms = {"slow": 7000, "medium": 5700, "fast": 4500}
        for speed, original_duration in original_flight_ms.items():
            with self.subTest(speed=speed):
                self.assertAlmostEqual(original_duration / duck.RULES["flight_ms"][speed], 1.2, places=3)

    def test_schedule_is_reproducible_with_comparable_mix_and_escalation(self):
        from collections import Counter
        a,b=duck.opportunity_schedule(1),duck.opportunity_schedule(2)
        self.assertEqual(a,duck.opportunity_schedule(1))
        self.assertNotEqual(a,b)
        self.assertEqual(Counter((t["phase"],t["distance"],t["speed"]) for t in a),Counter((t["phase"],t["distance"],t["speed"]) for t in b))
        for seed in range(20):
            targets=duck.opportunity_schedule(seed)
            peaks=[max(sum(t["start"]<=ms<t["end"] for t in targets) for ms in range(p["start"],p["end"],50)) for p in duck.RULES["phases"]]
            self.assertTrue(1<=peaks[0]<=3,peaks)
            self.assertTrue(3<=peaks[1]<=6,peaks)
            self.assertTrue(6<=peaks[2]<=10,peaks)

    def test_snapshot_retained_when_event_settings_change(self):
        snapshot=deepcopy(self.run.configuration_snapshot)
        EventGameConfiguration.objects.filter(event=self.event).update(settings={"future":1},configuration_version="2")
        current=duck.prepare(self.run)
        self.assertEqual(current.configuration_snapshot,snapshot)
        self.assertEqual(current.configuration_version,"1")

    def test_refresh_in_progress_shows_safe_interruption(self):
        duck.activate(self.run)
        response=self.client.get(self.url("launch"))
        self.assertContains(response,"ROUND INTERRUPTED")
        self.run.refresh_from_db()
        self.assertEqual(self.run.status,GameSession.Status.IN_PROGRESS)
        self.assertIsNone(self.run.raw_score)

    def test_replay_is_nonofficial_and_creates_no_identity_or_attendance(self):
        before=[m.objects.count() for m in (Participant,EventRegistration,QrTicket,Attendance)]
        duck.accept_result(self.elapsed(),[])
        response=self.client.post(self.url("duck_replay"))
        self.assertRedirects(response,self.url("launch"))
        self.run.refresh_from_db()
        self.assertEqual(self.run.status,GameSession.Status.VOIDED)
        self.assertIsNotNone(self.run.replaced_by_id)
        self.assertEqual([m.objects.count() for m in (Participant,EventRegistration,QrTicket,Attendance)],before)
        self.assertEqual(self.run.replaced_by.mode,"staff_test")

    def test_official_replay_forbidden_and_controls_hidden(self):
        exp=self.experience_for("official")
        run=duck.prepare(resolve_and_start(experience_session=exp))
        browser=self.client.session
        browser.pop("test_kiosk_session",None)
        browser["participant_kiosk_session"]=str(exp.pk)
        browser.save()
        response=self.client.get(self.url("launch",experience=exp))
        self.assertNotContains(response,"PLAY AGAIN")
        self.assertNotContains(response,"TEST MODE")
        self.assertNotContains(response,"Synthetic")
        self.assertEqual(self.client.post(self.url("duck_replay",run,exp)).status_code,403)
        run.refresh_from_db()
        self.assertEqual(run.status,GameSession.Status.NOT_STARTED)

    def test_http_results_are_minimal_and_simulator_handoff_unlocked(self):
        self.elapsed()
        response=self.client.post(self.url("duck_finish"),{"shots":[]},content_type="application/json")
        self.assertEqual(response.status_code,200)
        self.assertEqual(set(response.json()["result"]),{"score","kills","accuracy"})
        self.assertContains(self.client.get(self.url("launch")),"HUNT COMPLETE")
        self.assertContains(self.client.get(self.url("launch")), "HEAVY EQUIPMENT SIMULATOR")
        self.assertContains(self.client.get(self.url("simulator_next")),"SIMULATOR")

    def test_placeholder_completion_cannot_bypass_playable_game(self):
        response = self.client.post(reverse("games:test_complete", kwargs={"game_session_id": self.run.pk}), {"station_code": self.station.code})
        self.assertEqual(response.status_code, 403)
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, GameSession.Status.NOT_STARTED)

    def test_local_background_is_referenced_and_rapid_triggers_are_counted(self):
        self.assertContains(self.client.get(self.url("launch")), "games/duck_hunt/marsh.png")
        result = duck.evaluate_shots([{"t": 1, "x": 0, "y": 0}, {"t": 2, "x": 0, "y": 0}], self.run.configuration_snapshot["duck_hunt"])
        self.assertEqual(result["shots"], 2)

    def test_quiz_results_show_configured_game_and_no_game_handoff(self):
        url = reverse("assessments:results", kwargs={"station_code": self.station.code, "session_id": self.experience.pk})
        response = self.client.get(url)
        self.assertContains(response, "Duck Hunt")
        self.assertContains(response, ">Duck Hunt</a>")
        self.assertContains(response, reverse("games:launch", kwargs={"station_code": self.station.code, "session_id": self.experience.pk}))
        self.assertNotContains(response, reverse("assessments:duck_handoff", kwargs={"station_code": self.station.code, "session_id": self.experience.pk}))
        self.event.game_configuration.delete()
        response = self.client.get(url)
        self.assertContains(response, "SIMULATOR")
        self.assertContains(response, "Please head to the Simulator station to continue.")
        self.assertContains(response, "RETURN TO KIOSK")
        self.assertNotContains(response, "data-auto-go")


    def test_completed_results_return_home_without_changing_game_result(self):
        self.run.status = GameSession.Status.COMPLETED
        self.run.result_data = {"duck_hunt": {"score": 770, "kills": 35, "accuracy": 95}}
        self.run.save(update_fields=["status", "result_data"])
        browser = self.client.session
        browser["test_kiosk_session"] = str(self.experience.pk)
        browser.save()

        launch_url = reverse("games:launch", kwargs={"station_code": self.station.code, "session_id": self.experience.pk})
        response = self.client.get(launch_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-auto-return="10"')
        self.assertContains(response, "Please head to the Heavy Equipment Simulator station to continue.")
        self.assertNotContains(response, "CONTINUE")
        simulator_url = reverse("games:simulator_next", kwargs={"station_code": self.station.code, "session_id": self.experience.pk})
        self.assertNotContains(response, simulator_url)

        exit_url = reverse("stations:service_exit_test", kwargs={"station_code": self.station.code, "session_id": self.experience.pk})
        response = self.client.post(exit_url)
        self.assertRedirects(response, reverse("stations:kiosk", kwargs={"station_code": self.station.code}), fetch_redirect_response=False)
        self.assertNotIn("test_kiosk_session", self.client.session)
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, GameSession.Status.COMPLETED)
        self.assertEqual(self.run.result_data["duck_hunt"]["score"], 770)

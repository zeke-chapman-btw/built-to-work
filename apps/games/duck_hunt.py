"""Duck Hunt V1 rules and trusted result acceptance. No browser-supplied scores."""
from copy import deepcopy
from datetime import timedelta
import math
import random
import secrets

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.assessments.models import QuizAttempt
from .models import GameSession
from .services import complete_game_session, configured_game

IMPLEMENTATION_VERSION = "1"
RULES = {
    "version": "duck-hunt-v2", "duration_ms": 45000, "countdown_ms": 3600,
    "feedback_ms": 700, "width": 1920, "height": 1080,
    "sizes": {"near": 160, "mid": 126, "far": 98},
    "flight_ms": {"slow": 5833, "medium": 4750, "fast": 3750},
    "scores": {"near": {"slow": 10, "medium": 15, "fast": 20},
               "mid": {"slow": 20, "medium": 25, "fast": 30},
               "far": {"slow": 30, "medium": 35, "fast": 40}},
    "phases": [{"start": 0, "end": 14000, "interval": 2500},
               {"start": 14000, "end": 30000, "interval": 1250},
               {"start": 30000, "end": 45000, "interval": 750}],
    "max_concurrency": 10,
}


def opportunity_schedule(seed, rules=RULES):
    rng = random.Random(seed)
    mix = [(d, s) for d in ("near", "mid", "far") for s in ("slow", "medium", "fast")]
    targets = []
    for phase_index, phase in enumerate(rules["phases"]):
        slots = list(range(phase["start"], phase["end"], phase["interval"]))
        difficulties = [mix[i % len(mix)] for i in range(len(slots))]
        rng.shuffle(difficulties)
        for slot, (distance, speed) in zip(slots, difficulties):
            start = max(phase["start"], min(phase["end"] - 1, slot + rng.randint(0, 80)))
            duration = rules["flight_ms"][speed]
            while sum(t["start"] <= start < t["end"] for t in targets) >= rules["max_concurrency"]:
                start += 50
            # Fixed distance/speed counts, independent of participant performance.
            path = ("level", "rising", "descending")[len(targets) % 3]
            y0 = rng.uniform(0.25, 0.56)
            y1 = y0 + ({"level": 0, "rising": -0.12, "descending": 0.12}[path])
            targets.append({"id": len(targets), "start": start,
                "end": min(start + duration, rules["duration_ms"]), "flight": duration,
                "distance": distance, "speed": speed, "path": path, "phase": phase_index,
                "direction": rng.choice((-1, 1)), "y0": y0, "y1": y1,
                "wave": rng.uniform(0, math.tau), "size": rules["sizes"][distance],
                "points": rules["scores"][distance][speed]})
    return targets


def target_position(target, at_ms, rules=RULES):
    progress = (at_ms - target["start"]) / target["flight"]
    x = -0.09 + 1.18 * progress
    if target["direction"] < 0:
        x = 1 - x
    y = target["y0"] + (target["y1"] - target["y0"]) * progress
    y += 0.018 * math.sin(progress * math.tau + target["wave"])
    return x * rules["width"], y * rules["height"]


def pick_target(targets, at_ms, x, y, hit_ids, rules=RULES):
    # Paint far targets first; the visually frontmost/nearest target wins overlap.
    for target in sorted(targets, key=lambda t: (t["size"], t["id"]), reverse=True):
        if target["id"] in hit_ids or not target["start"] <= at_ms < target["end"]:
            continue
        tx, ty = target_position(target, at_ms, rules)
        if ((x - tx) / (target["size"] * 0.53)) ** 2 + ((y - ty) / (target["size"] * 0.32)) ** 2 <= 1:
            return target
    return None


def evaluate_shots(shots, snapshot):
    rules, targets = snapshot["rules"], snapshot["targets"]
    if not isinstance(shots, list) or len(shots) > 4500:
        raise ValidationError("Invalid shot record.")
    hit_ids, score, previous = set(), 0, -1
    breakdown = {d: {s: {"opportunities": 0, "hits": 0, "score": 0} for s in rules["flight_ms"]} for d in rules["sizes"]}
    for t in targets:
        breakdown[t["distance"]][t["speed"]]["opportunities"] += 1
    for shot in shots:
        if not isinstance(shot, dict) or set(shot) != {"t", "x", "y"}:
            raise ValidationError("Invalid shot record.")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in shot.values()):
            raise ValidationError("Invalid shot coordinates.")
        at, x, y = shot["t"], shot["x"], shot["y"]
        if not 0 <= at < rules["duration_ms"] or at < previous or not 0 <= x <= rules["width"] or not 0 <= y <= rules["height"]:
            raise ValidationError("Shot is outside the round.")
        previous = at
        target = pick_target(targets, at, x, y, hit_ids, rules)
        if target:
            hit_ids.add(target["id"])
            score += target["points"]
            item = breakdown[target["distance"]][target["speed"]]
            item["hits"] += 1
            item["score"] += target["points"]
    return {"score": score, "kills": len(hit_ids), "shots": len(shots),
        "accuracy": round(100 * len(hit_ids) / len(shots), 2) if shots else 0,
        "opportunities": len(targets), "breakdown": breakdown, "rules_version": rules["version"]}


def validate_context(run):
    run.clean()
    run.experience_session.clean()
    config = configured_game(run.event)
    if run.game.implementation_key != "duck_hunt" or not config or config.game_id != run.game_id:
        raise ValidationError("This game is unavailable.")
    if run.status == GameSession.Status.VOIDED or run.replaced_by_id:
        raise ValidationError("This round is no longer available.")
    if not QuizAttempt.objects.filter(experience_session=run.experience_session,
            is_official=run.is_official, status=QuizAttempt.Status.COMPLETE, voided_at__isnull=True).exists():
        raise ValidationError("Complete the assessment first.")


def prepare(game_session):
    with transaction.atomic():
        run = GameSession.objects.select_for_update().select_related("game", "event", "experience_session").get(pk=game_session.pk)
        validate_context(run)
        if "duck_hunt" not in run.configuration_snapshot:
            if run.status == GameSession.Status.COMPLETED:
                raise ValidationError("This earlier game result cannot be replayed.")
            seed = secrets.randbits(32)
            snapshot = deepcopy(run.configuration_snapshot)
            snapshot["duck_hunt"] = {"seed": seed, "rules": deepcopy(RULES), "targets": opportunity_schedule(seed)}
            run.configuration_snapshot = snapshot
            run.game_version = IMPLEMENTATION_VERSION
            run.status = GameSession.Status.NOT_STARTED
            run.started_at = None
            run.save(update_fields=("configuration_snapshot", "game_version", "status", "started_at"))
        return run


def activate(game_session):
    with transaction.atomic():
        run = GameSession.objects.select_for_update().select_related("game", "event", "experience_session").get(pk=game_session.pk)
        validate_context(run)
        if run.status != GameSession.Status.NOT_STARTED or "duck_hunt" not in run.configuration_snapshot:
            raise ValidationError("This round has already started. Please ask a team member for help.")
        run.started_at = timezone.now() + timedelta(milliseconds=run.configuration_snapshot["duck_hunt"]["rules"]["countdown_ms"])
        run.status = GameSession.Status.IN_PROGRESS
        run.save(update_fields=("started_at", "status"))
        return run


def accept_result(game_session, shots):
    with transaction.atomic():
        run = GameSession.objects.select_for_update().select_related("game", "event", "experience_session").get(pk=game_session.pk)
        validate_context(run)
        if run.status == GameSession.Status.COMPLETED:
            return run  # First accepted result wins, including network retries.
        snapshot = run.configuration_snapshot.get("duck_hunt")
        if not snapshot or run.status != GameSession.Status.IN_PROGRESS or not run.started_at:
            raise ValidationError("The round has not started.")
        duration = snapshot["rules"]["duration_ms"]
        if timezone.now() < run.started_at + timedelta(milliseconds=duration):
            raise ValidationError("The round is still running.")
        result = evaluate_shots(shots, snapshot)
        result["started_at"] = run.started_at.isoformat()
        result["round_ended_at"] = (run.started_at + timedelta(milliseconds=duration)).isoformat()
        result["implementation_version"] = run.game_version
        result["mode"] = run.mode
        # Keep aggregates, not the potentially large shot ledger.
        return complete_game_session(game_session=run, raw_score=result["score"], result_data={"duck_hunt": result})

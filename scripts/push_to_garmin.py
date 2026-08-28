#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Загрузка тренировок из garmin-workouts.json в Garmin Connect.

Запуск на машине с доступом к аккаунту Garmin:

    pip install garth
    export GARMIN_EMAIL=...            # или введёт интерактивно
    export GARMIN_PASSWORD=...
    python3 scripts/push_to_garmin.py            # создать + поставить в календарь
    python3 scripts/push_to_garmin.py --dry-run  # только показать, что будет создано

Токен сессии сохраняется в ~/.garth, повторный логин не нужен.
"""
import argparse, getpass, json, os, sys

API = "https://connectapi.garmin.com"

SPORT = {"running": (1, "running"), "cycling": (2, "cycling"), "swimming": (4, "lap_swimming")}
STEP = {"warmup": (1, "warmup"), "cooldown": (2, "cooldown"), "interval": (3, "interval"),
        "recovery": (4, "recovery"), "rest": (5, "rest"), "repeat": (6, "repeat")}
TARGET = {"hr": (4, "heart.rate.zone"), "power": (2, "power.zone"), "pace": (6, "pace.zone")}


def pace_to_mps(p):
    """'5:30' (мин:сек на км) -> м/с"""
    m, s = p.split(":")
    return 1000.0 / (int(m) * 60 + int(s))


def build_target(t):
    if not t:
        return {"targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}}
    tid, tkey = TARGET[t["type"]]
    if t["type"] == "pace":
        one, two = pace_to_mps(t["low"]), pace_to_mps(t["high"])
    else:
        one, two = float(t["low"]), float(t["high"])
    return {"targetType": {"workoutTargetTypeId": tid, "workoutTargetTypeKey": tkey},
            "targetValueOne": min(one, two), "targetValueTwo": max(one, two)}


def build_step(st, order):
    if st.get("type") == "repeat":
        children = []
        for i, child in enumerate(st["steps"], start=1):
            children.append(build_step(child, order + i))
        return {"type": "RepeatGroupDTO", "stepOrder": order,
                "stepType": {"stepTypeId": 6, "stepTypeKey": "repeat"},
                "numberOfIterations": st["repeats"], "smartRepeat": False,
                "endCondition": {"conditionTypeId": 7, "conditionTypeKey": "iterations"},
                "endConditionValue": st["repeats"], "workoutSteps": children}

    sid, skey = STEP.get(st.get("type", "interval"), STEP["interval"])
    step = {"type": "ExecutableStepDTO", "stepOrder": order,
            "stepType": {"stepTypeId": sid, "stepTypeKey": skey},
            "description": st.get("note")}
    if st.get("distanceKm"):
        step["endCondition"] = {"conditionTypeId": 3, "conditionTypeKey": "distance"}
        step["endConditionValue"] = float(st["distanceKm"]) * 1000
    else:
        secs = int(st.get("durationSec") or float(st.get("durationMin", 0)) * 60)
        step["endCondition"] = {"conditionTypeId": 2, "conditionTypeKey": "time"}
        step["endConditionValue"] = secs
    step.update(build_target(st.get("target")))
    return step


def flatten_order(steps):
    """Пересчитать stepOrder сквозным номером, как ожидает Garmin."""
    n = [0]

    def walk(items):
        out = []
        for it in items:
            n[0] += 1
            it["stepOrder"] = n[0]
            if it["type"] == "RepeatGroupDTO":
                it["workoutSteps"] = walk(it["workoutSteps"])
            out.append(it)
        return out

    return walk(steps)


def build_workout(w):
    sid, skey = SPORT[w["sport"]]
    sport = {"sportTypeId": sid, "sportTypeKey": skey}
    steps = flatten_order([build_step(s, i) for i, s in enumerate(w["steps"], start=1)])
    return {"sportType": sport, "workoutName": w["name"][:80],
            "description": w.get("note", ""),
            "workoutSegments": [{"segmentOrder": 1, "sportType": sport, "workoutSteps": steps}]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=os.path.join(os.path.dirname(__file__), "..", "garmin-workouts.json"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    data = json.load(open(os.path.abspath(args.file), encoding="utf-8"))
    workouts = data["workouts"]

    if args.dry_run:
        for w in workouts:
            print(f"{w['date']}  {w['sport']:8}  {w['name']}  ({w['estimatedDurationMin']} мин)")
            print(json.dumps(build_workout(w), ensure_ascii=False)[:200] + " ...")
        print(f"\nВсего: {len(workouts)} тренировок (--dry-run, ничего не отправлено)")
        return

    import garth

    token_dir = os.path.expanduser("~/.garth")
    try:
        garth.resume(token_dir)
        garth.client.username
    except Exception:
        email = os.getenv("GARMIN_EMAIL") or input("Garmin email: ")
        password = os.getenv("GARMIN_PASSWORD") or getpass.getpass("Garmin password: ")
        garth.login(email, password)
        garth.save(token_dir)

    ok, failed = 0, []
    for w in workouts:
        try:
            created = garth.connectapi("/workout-service/workout", method="POST", json=build_workout(w))
            wid = created["workoutId"]
            garth.connectapi(f"/workout-service/schedule/{wid}", method="POST", json={"date": w["date"]})
            print(f"✓ {w['date']}  {w['name']}  (id {wid})")
            ok += 1
        except Exception as e:
            print(f"✗ {w['date']}  {w['name']}: {e}", file=sys.stderr)
            failed.append(w["name"])

    print(f"\nЗагружено {ok} из {len(workouts)}")
    if failed:
        print("Не прошли: " + ", ".join(failed))
    if data.get("dryLandSwim"):
        print("\nСухое плавание (в Garmin не загружается, сделай вручную или пропусти):")
        for d in data["dryLandSwim"]:
            print(f"  {d['date']}  {d['durationMin']} мин — {d['content']}")


if __name__ == "__main__":
    main()

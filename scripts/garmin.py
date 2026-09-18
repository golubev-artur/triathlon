#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Всё общение с Garmin Connect одним скриптом.

    python3 scripts/garmin.py --pull              # забрать тренировки (по умолчанию 90 дней)
    python3 scripts/garmin.py --pull --days 365
    python3 scripts/garmin.py --push              # положить план из garmin-workouts.json в часы
    python3 scripts/garmin.py                     # --pull + --push разом

Логин спрашивается один раз, дальше работает по токену из ~/.garth.
Выгрузка пишется в garmin-activities.json — из него автопланировщик берёт факт.
"""
import argparse, datetime as dt, getpass, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "garmin-activities.json")

KIND = {"running": "run", "trail_running": "run", "treadmill_running": "run", "indoor_running": "run",
        "cycling": "bike", "road_biking": "bike", "indoor_cycling": "bike", "virtual_ride": "bike",
        "gravel_cycling": "bike", "mountain_biking": "bike",
        "lap_swimming": "swim", "open_water_swimming": "swim",
        "strength_training": "gym", "walking": "walk", "hiking": "walk"}


def connect():
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
        print("Вход выполнен, токен сохранён — пароль больше не спросит.")
    return garth


def pull(garth, days):
    got, start, limit = [], 0, 100
    since = dt.date.today() - dt.timedelta(days=days)
    while True:
        batch = garth.connectapi(
            f"/activitylist-service/activities/search/activities?start={start}&limit={limit}")
        if not batch:
            break
        stop = False
        for a in batch:
            d = (a.get("startTimeLocal") or "")[:10]
            if not d:
                continue
            if dt.date.fromisoformat(d) < since:
                stop = True
                break
            t = ((a.get("activityType") or {}).get("typeKey") or "").lower()
            got.append({
                "date": d,
                "time": (a.get("startTimeLocal") or "")[11:16],
                "kind": KIND.get(t, "other"),
                "garminType": t,
                "name": a.get("activityName"),
                "minutes": round((a.get("duration") or 0) / 60),
                "km": round((a.get("distance") or 0) / 1000, 2),
                "avgHR": a.get("averageHR"),
                "maxHR": a.get("maxHR"),
                "elevation": round(a.get("elevationGain") or 0),
                "trainingLoad": a.get("activityTrainingLoad"),
            })
        if stop or len(batch) < limit:
            break
        start += limit

    json.dump({"pulled": dt.datetime.now().isoformat(timespec="seconds"), "days": days,
               "activities": got}, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"Получено {len(got)} тренировок за {days} дн. → garmin-activities.json")
    for a in got[:15]:
        print(f"  {a['date']} {a['time']}  {a['kind']:5} {a['minutes']:4} мин  {a['km']:6} км  "
              f"♥{a['avgHR'] or '-'}  {a['name'] or ''}")
    if len(got) > 15:
        print(f"  … ещё {len(got) - 15}")
    return got


def push(garth):
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    from push_to_garmin import build_workout
    plan = json.load(open(os.path.join(ROOT, "garmin-workouts.json"), encoding="utf-8"))
    today = dt.date.today().isoformat()
    upcoming = [w for w in plan["workouts"] if w["date"] >= today]
    if not upcoming:
        print("В garmin-workouts.json нет тренировок на будущее — сначала построй план (autoplan.py)")
        return
    for w in upcoming:
        created = garth.connectapi("/workout-service/workout", method="POST", json=build_workout(w))
        wid = created["workoutId"]
        garth.connectapi(f"/workout-service/schedule/{wid}", method="POST", json={"date": w["date"]})
        print(f"✓ {w['date']}  {w['name']}")
    print(f"Загружено {len(upcoming)} тренировок в календарь Garmin")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pull", action="store_true")
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--days", type=int, default=90)
    args = ap.parse_args()
    both = not (args.pull or args.push)
    g = connect()
    if args.pull or both:
        pull(g, args.days)
    if args.push or both:
        push(g)


if __name__ == "__main__":
    main()

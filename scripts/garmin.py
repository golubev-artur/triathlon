#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Всё общение с Garmin Connect одним скриптом (библиотека garminconnect).

    python3 scripts/garmin.py --pull              # забрать тренировки (по умолчанию 90 дней)
    python3 scripts/garmin.py --pull --days 3650  # вся история, вместе с гонками
    python3 scripts/garmin.py --push              # положить план из garmin-workouts.json в часы
    python3 scripts/garmin.py                     # --pull + --push разом

Нужен Python 3.12+ (на маке: uv run --python 3.12 --with garminconnect scripts/garmin.py ...).
Логин спрашивается один раз, дальше работает по токену из ~/.garminconnect —
он обновляется сам. В CI логин берётся из GARMIN_EMAIL / GARMIN_PASSWORD,
а токен переживает запуски через кэш.
Выгрузка пишется в garmin-activities.json — из него автопланировщик берёт факт.
"""
import argparse, datetime as dt, getpass, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "garmin-activities.json")
TOKENS = os.path.expanduser(os.getenv("GARMINTOKENS") or "~/.garminconnect")

KIND = {"running": "run", "trail_running": "run", "treadmill_running": "run", "indoor_running": "run",
        "track_running": "run", "street_running": "run",
        "cycling": "bike", "road_biking": "bike", "indoor_cycling": "bike", "virtual_ride": "bike",
        "gravel_cycling": "bike", "mountain_biking": "bike",
        "lap_swimming": "swim", "open_water_swimming": "swim",
        "multi_sport": "tri", "triathlon": "tri", "duathlon": "tri",
        "strength_training": "gym", "walking": "walk", "hiking": "walk"}


def connect():
    from garminconnect import Garmin

    interactive = sys.stdin.isatty()
    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")
    has_tokens = os.path.exists(os.path.join(TOKENS, "garmin_tokens.json"))

    if not has_tokens and not email and not interactive:
        raise SystemExit("Нет входа в Garmin: задай GARMIN_EMAIL и GARMIN_PASSWORD")
    if not has_tokens and interactive:
        email = email or input("Garmin email: ")
        password = password or getpass.getpass("Garmin password: ")

    os.makedirs(TOKENS, mode=0o700, exist_ok=True)
    api = Garmin(email, password,
                 prompt_mfa=(lambda: input("Код из письма/приложения Garmin: ")) if interactive else None)
    try:
        api.login(TOKENS)
    except Exception as e:
        text = str(e)
        if "429" in text:
            raise SystemExit("Garmin временно ограничил вход (429). Повтори через пару часов — "
                             "после первого удачного входа токен сохранится и вход больше не понадобится.")
        if "MFA" in text.upper() and not interactive:
            raise SystemExit("На аккаунте включён код подтверждения (MFA) — войди один раз на ноутбуке, "
                             "токен из ~/.garminconnect можно перенести в CI.")
        raise
    if not has_tokens:
        print("Вход выполнен, токен сохранён — пароль больше не понадобится.")
    return api


def pull(api, days):
    today = dt.date.today()
    since = today - dt.timedelta(days=days)
    raw = api.get_activities_by_date(since.isoformat(), today.isoformat()) or []

    got = []
    for a in raw:
        start = a.get("startTimeLocal") or ""
        t = ((a.get("activityType") or {}).get("typeKey") or "").lower()
        event = ((a.get("eventType") or {}).get("typeKey") or "").lower()
        got.append({
            "id": a.get("activityId"),
            "date": start[:10],
            "time": start[11:16],
            "kind": KIND.get(t, "other"),
            "garminType": t,
            "race": event == "race",
            "name": a.get("activityName"),
            "minutes": round((a.get("duration") or 0) / 60),
            "km": round((a.get("distance") or 0) / 1000, 2),
            "avgHR": a.get("averageHR"),
            "maxHR": a.get("maxHR"),
            "avgPower": a.get("avgPower"),
            "elevation": round(a.get("elevationGain") or 0),
            "trainingLoad": a.get("activityTrainingLoad"),
            "aerobicTE": a.get("aerobicTrainingEffect"),
        })
    got.sort(key=lambda x: (x["date"], x["time"]), reverse=True)

    json.dump({"pulled": dt.datetime.now().isoformat(timespec="seconds"), "days": days,
               "activities": got}, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    races = [a for a in got if a["race"] or a["kind"] == "tri"]
    print(f"Получено {len(got)} тренировок за {days} дн., из них гонок: {len(races)} → garmin-activities.json")
    for a in got[:15]:
        mark = "🏁" if a in races else "  "
        print(f"{mark}{a['date']} {a['time']}  {a['kind']:5} {a['minutes']:4} мин  {a['km']:6} км  "
              f"♥{a['avgHR'] or '-'}  {a['name'] or ''}")
    if len(got) > 15:
        print(f"  … ещё {len(got) - 15}")
    return got


def push(api):
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    from push_to_garmin import build_workout
    plan = json.load(open(os.path.join(ROOT, "garmin-workouts.json"), encoding="utf-8"))
    today = dt.date.today().isoformat()
    upcoming = [w for w in plan["workouts"] if w["date"] >= today]
    if not upcoming:
        print("В garmin-workouts.json нет тренировок на будущее — сначала построй план (autoplan.py)")
        return
    for w in upcoming:
        created = api.upload_workout(build_workout(w))
        api.schedule_workout(created["workoutId"], w["date"])
        print(f"✓ {w['date']}  {w['name']}")
    print(f"Загружено {len(upcoming)} тренировок в календарь Garmin")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pull", action="store_true")
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--days", type=int, default=90)
    args = ap.parse_args()
    both = not (args.pull or args.push)
    api = connect()
    if args.pull or both:
        pull(api, args.days)
    if args.push or both:
        push(api)


if __name__ == "__main__":
    main()

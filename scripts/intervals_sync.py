#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Обмен с intervals.icu: забрать тренировки, отправить план.

Ключ и athlete id берутся из .env (см. .env.example) или из переменных окружения.

    pip install requests python-dotenv
    python3 scripts/intervals_sync.py --pull            # последние 30 дней активностей
    python3 scripts/intervals_sync.py --pull --days 90
    python3 scripts/intervals_sync.py --push            # план из garmin-workouts.json в календарь
    python3 scripts/intervals_sync.py --whoami          # проверка ключа

Календарь intervals.icu синхронизируется с Garmin Connect, если в
https://intervals.icu/settings/connections включена запись тренировок в Garmin.
"""
import argparse, base64, datetime as dt, json, os, sys, urllib.request, urllib.error

BASE = "https://intervals.icu/api/v1"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_env():
    path = os.path.join(ROOT, ".env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    key, athlete = os.getenv("INTERVALS_API_KEY"), os.getenv("INTERVALS_ATHLETE_ID")
    if not key or not athlete:
        sys.exit("Нет INTERVALS_API_KEY / INTERVALS_ATHLETE_ID — скопируй .env.example в .env и заполни.")
    return key, athlete


def call(key, path, method="GET", payload=None):
    url = BASE + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    token = base64.b64encode(f"API_KEY:{key}".encode()).decode()
    req.add_header("Authorization", "Basic " + token)
    req.add_header("Content-Type", "application/json")
    # без внятного User-Agent Cloudflare отбивает запрос (403, error code 1010)
    req.add_header("User-Agent", "triathlon-dashboard/1.0 (+https://github.com/golubev-artur/triathlon)")
    req.add_header("Accept", "*/*")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read().decode()
            return json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {path} → {e.code}: {e.read().decode()[:300]}")


SPORT = {"running": "Run", "cycling": "Ride", "swimming": "Swim"}


def steps_text(steps, out=None, indent=""):
    """План в текстовый формат workout_doc intervals.icu (одна строка — один шаг)."""
    out = out if out is not None else []
    for st in steps:
        if st.get("type") == "repeat":
            out.append(f"{indent}{st['repeats']}x")
            steps_text(st["steps"], out, indent + "- ")
            continue
        if st.get("distanceKm"):
            dur = f"{st['distanceKm']}km"
        else:
            secs = int(st.get("durationSec") or float(st.get("durationMin", 0)) * 60)
            dur = f"{secs // 60}m" + (f"{secs % 60}s" if secs % 60 else "")
        t = st.get("target") or {}
        if t.get("type") == "hr":
            tgt = f" {t['low']}-{t['high']} bpm"
        elif t.get("type") == "power":
            tgt = f" {t['low']}-{t['high']}W"
        elif t.get("type") == "pace":
            tgt = f" {t['high']}-{t['low']}/km"
        else:
            tgt = ""
        note = f"  # {st['note']}" if st.get("note") else ""
        out.append(f"{indent}- {dur}{tgt}{note}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pull", action="store_true")
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--whoami", action="store_true")
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()
    key, athlete = load_env()

    if args.whoami or not (args.pull or args.push):
        me = call(key, f"/athlete/{athlete}/profile")
        ath = (me or {}).get("athlete", me) or {}
        print(f"Ключ рабочий: {ath.get('name')} (id {ath.get('id', athlete)}), часовой пояс {ath.get('timezone')}")
        if not (args.pull or args.push):
            return

    if args.pull:
        newest = dt.date.today()
        oldest = newest - dt.timedelta(days=args.days)
        acts = call(key, f"/athlete/{athlete}/activities?oldest={oldest}&newest={newest}")
        out = os.path.join(ROOT, "intervals-activities.json")
        json.dump(acts, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"Получено {len(acts)} активностей за {args.days} дн. → {out}")
        for a in acts[:15]:
            mins = round((a.get("moving_time") or 0) / 60)
            km = round((a.get("distance") or 0) / 1000, 1)
            print(f"  {a.get('start_date_local','')[:10]}  {a.get('type','?'):6} {mins:4} мин  {km:5} км  ♥{a.get('average_heartrate') or '-'}")

    if args.push:
        plan = json.load(open(os.path.join(ROOT, "garmin-workouts.json"), encoding="utf-8"))
        for w in plan["workouts"]:
            body = {
                "start_date_local": w["date"] + "T00:00:00",
                "category": "WORKOUT",
                "type": SPORT.get(w["sport"], "Other"),
                "name": w["name"],
                "description": w.get("note", ""),
                "moving_time": int(w.get("estimatedDurationMin", 0)) * 60,
                "workout_doc": {"description": "\n".join(steps_text(w["steps"]))},
            }
            call(key, f"/athlete/{athlete}/events", method="POST", payload=body)
            print(f"✓ {w['date']}  {w['name']}")
        print(f"\nОтправлено {len(plan['workouts'])} тренировок в календарь intervals.icu")


if __name__ == "__main__":
    main()

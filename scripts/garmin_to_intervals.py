#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Перенос истории тренировок из Garmin Connect в intervals.icu.

Качает оригинальные .fit из Garmin и загружает их в intervals.icu.
Уже загруженные пропускает (сверяет по дате и длительности).

    python3 scripts/garmin_to_intervals.py --days 365 --dry-run
    python3 scripts/garmin_to_intervals.py --days 365
"""
import argparse, base64, datetime as dt, io, json, os, sys, urllib.request, uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from intervals_sync import load_env, call, BASE  # noqa: E402

UA = "triathlon-dashboard/1.0 (+https://github.com/golubev-artur/triathlon)"


def upload(key, athlete, name, blob):
    """multipart/form-data загрузка файла в intervals.icu"""
    boundary = uuid.uuid4().hex
    body = io.BytesIO()
    body.write(f"--{boundary}\r\n".encode())
    body.write(f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'.encode())
    body.write(b"Content-Type: application/octet-stream\r\n\r\n")
    body.write(blob)
    body.write(f"\r\n--{boundary}--\r\n".encode())
    data = body.getvalue()

    req = urllib.request.Request(f"{BASE}/athlete/{athlete}/activities", data=data, method="POST")
    req.add_header("Authorization", "Basic " + base64.b64encode(f"API_KEY:{key}".encode()).decode())
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("User-Agent", UA)
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    key, athlete = load_env()

    from garmin import connect
    garth = connect()   # спросит логин один раз, дальше по токену

    # что уже есть в intervals.icu — чтобы не заливать дважды
    newest = dt.date.today()
    oldest = newest - dt.timedelta(days=args.days)
    have = {(a.get("start_date_local", "")[:10], round((a.get("moving_time") or 0) / 60))
            for a in (call(key, f"/athlete/{athlete}/activities?oldest={oldest}&newest={newest}") or [])}
    print(f"В intervals.icu уже есть {len(have)} тренировок за период")

    start, sent, skipped = 0, 0, 0
    while True:
        batch = garth.connectapi(
            f"/activitylist-service/activities/search/activities?start={start}&limit=100")
        if not batch:
            break
        done = False
        for a in batch:
            d = (a.get("startTimeLocal") or "")[:10]
            if not d:
                continue
            if dt.date.fromisoformat(d) < oldest:
                done = True
                break
            mins = round((a.get("duration") or 0) / 60)
            if (d, mins) in have:
                skipped += 1
                continue
            aid = a["activityId"]
            label = f"{d}  {a.get('activityName') or ''} ({mins} мин)"
            if args.dry_run:
                print(f"  → {label}")
                sent += 1
                continue
            try:
                blob = garth.download(f"/download-service/files/activity/{aid}")
                code = upload(key, athlete, f"{aid}.zip", blob)
                print(f"✓ {label}  [{code}]")
                sent += 1
            except Exception as e:
                print(f"✗ {label}: {e}", file=sys.stderr)
        if done or len(batch) < 100:
            break
        start += 100

    word = "будет загружено" if args.dry_run else "загружено"
    print(f"\nИтого: {word} {sent}, пропущено (уже есть) {skipped}")


if __name__ == "__main__":
    main()

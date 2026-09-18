#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Автопланировщик недели: смотрит факт за 14 дней и строит следующую неделю.

    python3 scripts/autoplan.py                  # план на следующую неделю
    python3 scripts/autoplan.py --week current   # переписать текущую неделю
    python3 scripts/autoplan.py --dry-run        # показать, ничего не записывая

Читает:  plan-config.json (цели, зоны, ограничения), data.json и/или
         intervals-activities.json (что реально сделано).
Пишет:   plan-weeks.json     — неделя для дашборда,
         garmin-workouts.json — та же неделя для загрузки в часы.
"""
import argparse, datetime as dt, json, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def load(name, default=None):
    path = os.path.join(ROOT, name)
    if not os.path.exists(path):
        return default
    return json.load(open(path, encoding="utf-8"))


def recent_actuals(days=14):
    """Список (дата, тип, минуты) за последние N дней из доступных источников."""
    out, today = [], dt.date.today()
    # основной источник — прямая выгрузка из Garmin
    for a in (load("garmin-activities.json", {}) or {}).get("activities", []):
        date = dt.date.fromisoformat(a["date"])
        if (today - date).days <= days and a.get("kind") in ("run", "bike", "swim"):
            out.append((date, a["kind"], a.get("minutes", 0)))
    if out:
        return out
    acts = load("intervals-activities.json", []) or []
    for a in acts:
        d = (a.get("start_date_local") or "")[:10]
        if not d:
            continue
        date = dt.date.fromisoformat(d)
        if (today - date).days <= days:
            t = (a.get("type") or "").lower()
            kind = "run" if "run" in t else "bike" if "ride" in t or "bike" in t else "swim" if "swim" in t else "other"
            out.append((date, kind, round((a.get("moving_time") or 0) / 60)))
    if not out:  # запасной источник — отметки на дашборде
        for w in load("data.json", {}).get("weekStatus", []) or []:
            for a in w.get("actual", []) + w.get("extra", []):
                out.append((dt.date.fromisoformat(w["date"]), a.get("type", "other"), a.get("duration", 0)))
    return out


def load_factor(cfg, actuals):
    """Во сколько раз урезать недельный объём относительно целевого."""
    done_min = sum(m for _, _, m in actuals)
    target_min = cfg["weeklyHours"] * 60 * 2  # цель за 2 недели
    if target_min <= 0:
        return 1.0
    ratio = done_min / target_min
    if ratio < 0.25:                     # длинная пауза — возвращаемся аккуратно
        return cfg.get("returnFactor", 0.65)
    if ratio < 0.7:                      # недобор — прибавляем ограниченно
        return min(1.0, ratio + cfg.get("rampPercent", 10) / 100)
    return 1.0


def taper_factor(cfg, monday):
    """Снижение объёма перед стартом."""
    if not cfg.get("raceDate"):
        return 1.0, False
    race = dt.date.fromisoformat(cfg["raceDate"])
    left = (race - monday).days
    if left < 0:
        return 1.0, False
    if left <= 7:
        return 0.45, True
    if left <= 14:
        return 0.7, True
    return 1.0, False


def build_week(cfg, monday, actuals):
    z2 = cfg["zones"]["hrZ2"]
    tempo_pace = cfg["zones"]["tempoPace"]
    ftp = cfg["zones"]["ftp"]
    factor = load_factor(cfg, actuals) * taper_factor(cfg, monday)[0]
    is_taper = taper_factor(cfg, monday)[1]
    base = cfg["weeklyHours"] * 60 * factor

    # распределение недельных минут по слотам
    share = {"quality_run": .13, "bike_int": .15, "easy_run": .12, "long_bike": .32, "long_run": .28}
    mins = {k: max(20, round(base * v / 5) * 5) for k, v in share.items()}
    morning, evening = cfg["morningTime"], cfg["eveningTime"]

    def run_quality():
        if is_taper:
            return dict(type="run", name="Бег Z2 + гоночные отрезки", time=morning,
                        details=f"{mins['quality_run']} мин, ЧСС {z2[0]}-{z2[1]}, в середине 4×1' в темпе гонки {tempo_pace[0]}/км")
        reps = max(3, min(5, mins["quality_run"] // 12))
        return dict(type="run", name="Бег темповый Z3", time=morning,
                    details=f"{mins['quality_run']} мин — разминка 15 мин, {reps}×6' в темпе {tempo_pace[0]}-{tempo_pace[1]}/км через 2 мин трусцы, заминка")

    def bike_interval():
        if is_taper:
            return dict(type="bike", name="Вело Z2 + гоночная мощность", time=evening,
                        details=f"{mins['bike_int']} мин, в середине 3×3' {int(ftp*.82)}-{int(ftp*.88)}W, каденс 85-90")
        return dict(type="bike", name="Вело Sweet Spot", time=evening,
                    details=f"{mins['bike_int']} мин — разминка 10 мин, 3×10' {int(ftp*.80)}-{int(ftp*.85)}W, каденс 85-90, заминка")

    slots = {
        "tue": [run_quality()],
        "wed": [bike_interval()],
        "thu": [dict(type="run", name="Бег Z2 + страйды", time=morning,
                     details=f"{mins['easy_run']} мин, ЧСС {z2[0]}-{z2[1]}, в конце 6×20 сек ускорения через 60 сек")],
        "sat": [dict(type="bike", name="Длинное вело + брик", time="8:00",
                     details=f"{mins['long_bike']} мин (вело {mins['long_bike']-15} + брик-бег 15), ЧСС до {z2[1]+5}, питание каждые 20 мин")],
        "sun": [dict(type="run", name="Длинный бег Z2", time="8:00",
                     details=f"{mins['long_run']} мин, ЧСС {z2[0]}-{z2[1]}, питьё каждые 20 мин")],
    }
    if cfg.get("swimAvailable"):
        slots["mon"] = [dict(type="swim", name="Плавание", time=evening,
                             details="40 мин — техника + непрерывный отрезок, sighting")]
    else:
        slots["mon"] = [dict(type="gym", name="Сухое плавание (резина)", time=evening,
                             details="20 мин — эспандер на гребок, плечи и ротаторы; бассейн недоступен")]
    for d in cfg.get("restDays", []):
        slots.pop(d, None)

    days, workouts = [], []
    for i, key in enumerate(DAY_KEYS):
        date = monday + dt.timedelta(days=i)
        trainings = []
        for t in slots.get(key, []):
            trainings.append({"time": t["time"], "type": t["type"], "name": t["name"],
                              "details": t["details"], "done": False})
            if t["type"] in ("run", "bike"):
                workouts.append(to_workout(date, t, z2, ftp, tempo_pace))
        days.append({"day": DAYS_RU[i], "date": date.strftime("%d.%m"),
                     "garminDate": date.isoformat(), "trainings": trainings})

    total = sum(int(t["details"].split()[0]) for d in days for t in d["trainings"] if t["details"].split()[0].isdigit())
    label = "Тейпер · автоплан" if is_taper else ("Возвращение · автоплан" if factor < 0.8 else "Автоплан недели")
    rng = f"{monday.strftime('%d.%m')} – {(monday+dt.timedelta(days=6)).strftime('%d.%m')}"
    week = {"label": label, "range": rng,
            "stats": {"plan": f"{total//60}ч {total%60}мин", "runKm": 0, "bikeKm": 0, "swimM": 0},
            "days": days}
    return week, workouts


def to_workout(date, t, z2, ftp, tempo_pace):
    """Тренировка в формате garmin-workouts.json (шаги с таргетами)."""
    mins = int(t["details"].split()[0])
    sport = "running" if t["type"] == "run" else "cycling"
    if "темповый" in t["name"]:
        reps = max(3, min(5, mins // 12))
        steps = [{"type": "warmup", "durationMin": 15, "target": {"type": "hr", "low": z2[0] - 15, "high": z2[1]}},
                 {"type": "repeat", "repeats": reps, "steps": [
                     {"type": "interval", "durationMin": 6, "target": {"type": "pace", "low": tempo_pace[0], "high": tempo_pace[1]}},
                     {"type": "recovery", "durationMin": 2, "target": {"type": "hr", "low": z2[0] - 15, "high": z2[1]}}]},
                 {"type": "cooldown", "durationMin": max(5, mins - 15 - reps * 8), "target": {"type": "hr", "low": z2[0] - 25, "high": z2[1] - 15}}]
    elif "Sweet Spot" in t["name"] or "мощность" in t["name"]:
        steps = [{"type": "warmup", "durationMin": 10, "target": {"type": "power", "low": int(ftp * .58), "high": int(ftp * .68)}},
                 {"type": "repeat", "repeats": 3, "steps": [
                     {"type": "interval", "durationMin": 10, "target": {"type": "power", "low": int(ftp * .80), "high": int(ftp * .85)}},
                     {"type": "recovery", "durationMin": 5, "target": {"type": "power", "low": int(ftp * .55), "high": int(ftp * .62)}}]},
                 {"type": "cooldown", "durationMin": max(5, mins - 55), "target": {"type": "power", "low": int(ftp * .52), "high": int(ftp * .62)}}]
    else:
        steps = [{"type": "warmup", "durationMin": 10, "target": {"type": "hr", "low": z2[0] - 15, "high": z2[0]}},
                 {"type": "interval", "durationMin": max(10, mins - 15), "target": {"type": "hr", "low": z2[0], "high": z2[1]}, "note": t["details"]},
                 {"type": "cooldown", "durationMin": 5, "target": {"type": "hr", "low": z2[0] - 25, "high": z2[0]}}]
    return {"date": date.isoformat(), "sport": sport, "name": t["name"],
            "estimatedDurationMin": mins, "steps": steps}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", choices=["next", "current"], default="next")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = load("plan-config.json")
    if not cfg:
        raise SystemExit("Нет plan-config.json")
    today = dt.date.today()
    monday = today - dt.timedelta(days=today.weekday())
    if args.week == "next":
        monday += dt.timedelta(days=7)

    actuals = recent_actuals()
    week, workouts = build_week(cfg, monday, actuals)

    done = sum(m for _, _, m in actuals)
    print(f"Факт за 14 дней: {len(actuals)} тренировок, {done} мин")
    print(f"Неделя {week['range']} — {week['label']}, план {week['stats']['plan']}")
    for d in week["days"]:
        for t in d["trainings"]:
            print(f"  {d['day']} {d['date']}  {t['time']:>5}  {t['name']} — {t['details'][:70]}")
    if args.dry_run:
        print("\n--dry-run: ничего не записано")
        return

    pw = load("plan-weeks.json", {"weeks": []})
    pw["weeks"] = [w for w in pw.get("weeks", []) if w["range"] != week["range"]] + [week]
    pw["generated"] = dt.datetime.now().isoformat(timespec="seconds")
    json.dump(pw, open(os.path.join(ROOT, "plan-weeks.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    gw = load("garmin-workouts.json", {})
    gw["generated"] = today.isoformat()
    gw["workouts"] = [w for w in gw.get("workouts", []) if w["date"] < monday.isoformat()] + workouts
    json.dump(gw, open(os.path.join(ROOT, "garmin-workouts.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\nЗаписано: plan-weeks.json (дашборд) и garmin-workouts.json ({len(workouts)} тренировок для часов)")


if __name__ == "__main__":
    main()

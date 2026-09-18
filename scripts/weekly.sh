#!/usr/bin/env bash
# Еженедельный прогон: факт из Garmin → план на неделю → тренировки в часы → дашборд.
set -u
cd "$(dirname "$0")/.." || exit 1
exec >>"$PWD/autoplan.log" 2>&1
echo "=== $(date '+%Y-%m-%d %H:%M') ==="

PY=$(command -v python3 || echo python)

# 1. что реально сделано за 90 дней
"$PY" scripts/garmin.py --pull --days 90 || echo "! выгрузка из Garmin не прошла"

# 2. план на следующую неделю
"$PY" scripts/autoplan.py --week next || { echo "! автоплан упал"; exit 1; }

# 3. тренировки в часы
"$PY" scripts/garmin.py --push || echo "! загрузка в часы не прошла"

# 4. обновить дашборд
if [ -n "$(git status --porcelain plan-weeks.json garmin-workouts.json)" ]; then
  git add plan-weeks.json garmin-workouts.json
  git commit -q -m "Автоплан: неделя от $(date '+%d.%m.%Y')" && git push -q origin main && echo "дашборд обновлён"
fi
echo "готово"

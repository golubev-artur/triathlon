#!/usr/bin/env bash
# Еженедельный прогон: забрать факт → построить неделю → положить в часы → обновить дашборд.
set -u
cd "$(dirname "$0")/.." || exit 1
exec >>"$PWD/autoplan.log" 2>&1
echo "=== $(date '+%Y-%m-%d %H:%M') ==="

PY=$(command -v python3 || echo python)

# 1. свежие данные о выполненном (не критично, если ключа нет)
"$PY" scripts/intervals_sync.py --pull --days 21 || echo "! выгрузка intervals пропущена"

# 2. план на следующую неделю
"$PY" scripts/autoplan.py --week next || { echo "! автоплан упал"; exit 1; }

# 3. тренировки в часы: сначала напрямую в Garmin, иначе через intervals.icu
"$PY" scripts/push_to_garmin.py || "$PY" scripts/intervals_sync.py --push || echo "! загрузка в часы не прошла"

# 4. обновить дашборд
if [ -n "$(git status --porcelain plan-weeks.json garmin-workouts.json)" ]; then
  git add plan-weeks.json garmin-workouts.json
  git commit -q -m "Автоплан: неделя от $(date '+%d.%m.%Y')" && git push -q origin main && echo "дашборд обновлён"
fi
echo "готово"

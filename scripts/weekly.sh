#!/usr/bin/env bash
# Еженедельный прогон: факт из Garmin → план на неделю → тренировки в часы → дашборд.
set -u
cd "$(dirname "$0")/.." || exit 1
exec >>"$PWD/autoplan.log" 2>&1
echo "=== $(date '+%Y-%m-%d %H:%M') ==="

PY=$(command -v python3 || echo python)
# garminconnect требует Python 3.12+; на маке без прав админа он ставится через uv
if command -v uv >/dev/null 2>&1; then
  GARMIN="uv run --python 3.12 --with garminconnect scripts/garmin.py"
else
  GARMIN="$PY scripts/garmin.py"
fi

# 1. что реально сделано за 90 дней
$GARMIN --pull --days 90 || echo "! выгрузка из Garmin не прошла"

# 2. план на следующую неделю
"$PY" scripts/autoplan.py --week next || { echo "! автоплан упал"; exit 1; }

# 3. тренировки в часы
$GARMIN --push || echo "! загрузка в часы не прошла"

# 4. обновить дашборд
if [ -n "$(git status --porcelain plan-weeks.json garmin-workouts.json)" ]; then
  git add plan-weeks.json garmin-workouts.json
  git commit -q -m "Автоплан: неделя от $(date '+%d.%m.%Y')" && git push -q origin main && echo "дашборд обновлён"
fi
echo "готово"

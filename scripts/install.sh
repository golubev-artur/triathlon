#!/usr/bin/env bash
# Подготовка автопланировщика на ноутбуке: зависимости, .env, права.
# Расписание НЕ ставится автоматически — команда печатается в конце, добавишь сам.
set -e
cd "$(dirname "$0")/.."
ROOT="$PWD"
echo "Папка проекта: $ROOT"

PY=$(command -v python3 || command -v python)
"$PY" -m pip install --quiet --user garth requests || echo "! поставь зависимости вручную: pip install garth requests"

[ -f .env ] || { cp .env.example .env; echo "Создан .env — впиши ключ intervals.icu и athlete id"; }
chmod +x scripts/*.sh scripts/*.py

echo
echo "1) Проверь один прогон руками:"
echo "     ./scripts/weekly.sh && tail -20 autoplan.log"
echo
echo "2) Чтобы запускалось само по воскресеньям в 20:00 — добавь строку в своё расписание"
echo "   (crontab -e) и сохрани:"
echo "     0 20 * * 0 cd $ROOT && ./scripts/weekly.sh"

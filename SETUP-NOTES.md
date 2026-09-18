# Где мы остановились

Заметка для продолжения работы. Обновлять по ходу.

## Что уже работает

- **Дашборд** — https://golubev-artur.github.io/triathlon/, деплой автоматом при push в `main`.
- **Автопланировщик** — `scripts/autoplan.py`: строит неделю из `plan-config.json` и факта
  за 14 дней. Пауза → мягкое возвращение, старт близко → тейпер. Пишет `plan-weeks.json`
  (его подхватывает страница) и `garmin-workouts.json` (для загрузки в часы).
  Проверка без записи: `python3 scripts/autoplan.py --dry-run`.
- **intervals.icu** — ключ рабочий, `.env` заполнен (athlete `i718325`).
  `scripts/intervals_sync.py --whoami` отвечает. Cloudflare требует свой User-Agent — учтено.
- **uv** — установлен в `~/.local/bin` (прав администратора на маке нет, поэтому не brew).
  Python 3.12 стоит через `uv python install 3.12`.

## Где затык

1. **Вход в Garmin.** Системный Python 3.9 тянул старую garth → 401. Через uv с Python 3.12
   библиотека свежая, но Garmin ответил **429 Too Many Requests** — временная блокировка
   после серии неудачных попыток. Нужно подождать 30–60 мин и повторить.
   Если 429 не уйдёт — переключить скрипт с `garth` на `garminconnect`
   (garth объявлена автором неподдерживаемой).
2. **История в intervals.icu.** Garmin отдаёт туда только wellness (вес, пульс покоя),
   активности — лишь новые, с момента подключения. Проверено через `--debug`:
   `/activities` → `[]`, `/wellness` → 89 КБ данных.
   Обход: `scripts/garmin_to_intervals.py` (качает .fit из Garmin, льёт в intervals) —
   не протестирован, упирается в тот же вход в Garmin.
3. **Данные для плана.** Последняя подтверждённая тренировка в репозитории — 09.08
   (бег 8.8 км, ЧСС 143). Что было после — неизвестно, поэтому план на странице устарел.

## Следующие шаги

1. Переждать 429 и выполнить:
   `source $HOME/.local/bin/env`
   `uv run --python 3.12 --with garth --with requests scripts/garmin.py --pull --days 365`
2. Получив выгрузку — пересобрать план: `python3 scripts/autoplan.py --week next`.
3. Загрузить его в часы: `scripts/garmin.py --push`.
4. Поставить автозапуск: `crontab -e` →
   `0 20 * * 0 cd /Users/artur/sites/triathlon && ./scripts/weekly.sh`
   (ноутбук должен быть включён; иначе перенести на сервер).
5. Переписать скрипты на запуск через `uv run`, раз системный Python 3.9 не годится.

## На будущее: много пользователей

Чужие люди пароль от Garmin вводить не будут, а официальный Training API Garmin даёт
только компаниям по заявке. Реальные пути: вход через Strava OAuth (полная история сразу,
но без сна и HRV) и/или intervals.icu OAuth (есть wellness и запись тренировок в часы,
но история из Garmin не подтягивается). Свой прямой вход в Garmin остаётся личным.

## Файлы

| Файл | Зачем |
|---|---|
| `scripts/garmin.py` | вход в Garmin, `--pull` выгрузка, `--push` загрузка плана |
| `scripts/autoplan.py` | генерация недели из данных и настроек |
| `scripts/intervals_sync.py` | intervals.icu: `--whoami`, `--pull`, `--push`, `--debug` |
| `scripts/garmin_to_intervals.py` | перенос истории Garmin → intervals.icu |
| `scripts/weekly.sh` | вся цепочка: факт → план → часы → дашборд |
| `scripts/install.sh` | зависимости, `.env`, строка для расписания |
| `plan-config.json` | дата старта, часы в неделю, зоны, бассейн, дни отдыха |
| `plan-weeks.json` | сгенерированные недели для страницы |
| `garmin-workouts.json` | тренировки для загрузки в часы |
| `.env` | ключи (в git не попадает) |

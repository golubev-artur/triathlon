#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Упаковать локальный токен Garmin (~/.garth) в строку для секрета GitHub.

    python3 scripts/garth_token.py        # напечатает base64 — вставить в секрет GARTH_TOKEN_B64

Нужно, чтобы расписание в GitHub Actions заходило в Garmin без пароля.
Токен живёт около года; когда перестанет работать — перевыпустить тем же способом.
"""
import base64, io, os, sys, tarfile

d = os.path.expanduser("~/.garth")
if not os.path.exists(os.path.join(d, "oauth1_token.json")):
    sys.exit("Токена нет. Сначала войди: python3 scripts/garmin.py --pull --days 7")

buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode="w:gz") as tf:
    for name in sorted(os.listdir(d)):
        tf.add(os.path.join(d, name), arcname=name)
print(base64.b64encode(buf.getvalue()).decode())

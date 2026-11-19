"""
Загрузчик бота для Yandex Cloud Functions (Python 3.12). Вставляется в Яндекс ОДИН раз.

Сам код бота лежит в репозитории: yandex/bot.py. Загрузчик берёт его с GitHub
и раз в 5 минут проверяет, не появилась ли новая версия. Если в новой версии ошибка —
продолжает работать старая.

Переменные окружения функции — те же: MAX_TOKEN, GITHUB_TOKEN, SECRET (и по желанию METRIKA_TOKEN).
"""
import base64
import json
import os
import time
import types
import urllib.request

REPO = os.environ.get("REPO", "3dmodelNagaevo/model3d-nagaevo")
PATH = "yandex/bot.py"
CHECK_EVERY = 300  # секунд

_bot, _sha, _checked = None, None, 0.0


def _load():
    global _bot, _sha, _checked
    if _bot is not None and time.time() - _checked < CHECK_EVERY:
        return _bot
    _checked = time.time()
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{REPO}/contents/{PATH}?ref=main",
            headers={"Authorization": f"Bearer {os.environ.get('GITHUB_TOKEN', '')}",
                     "Accept": "application/vnd.github+json", "User-Agent": "model3d-bot-loader"})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read())
        if d.get("sha") != _sha:
            code = base64.b64decode(d["content"]).decode("utf-8")
            mod = types.ModuleType("bot")
            mod.__file__ = PATH
            exec(compile(code, PATH, "exec"), mod.__dict__)
            _bot, _sha = mod, d.get("sha")
            print("loader: загружена версия", (_sha or "")[:7])
    except Exception as e:
        print("loader: не удалось обновить код:", repr(e))
        if _bot is None:
            raise
    return _bot


def handler(event, context):
    return _load().handler(event, context)

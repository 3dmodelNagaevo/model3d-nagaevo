"""
Мгновенный бот сайта «3D модели Нагаево» — основной код.
В Yandex Cloud вставлен только загрузчик (yandex/index.py): он сам берёт этот файл с GitHub,
так что изменения здесь попадают в бота автоматически, в течение ~5 минут.


MAX присылает сюда каждое событие (webhook):
  • сообщение владельца боту → команда выполняется сразу, ответ за 1–2 секунды;
  • новый / изменённый / удалённый пост в канале → сразу запускается обновление сайта на GitHub.

Каталог, отчёты и уведомления по-прежнему делает GitHub (scripts/sync_max.py).

Переменные окружения функции:
  MAX_TOKEN     — токен бота MAX
  GITHUB_TOKEN  — токен GitHub (доступ к репозиторию: Contents и Actions — Read and write)
  SECRET        — придуманный пароль из латинских букв и цифр (16+ символов)
  REPO          — необязательно, по умолчанию 3dmodelNagaevo/model3d-nagaevo
  METRIKA_TOKEN — необязательно, токен Яндекс Метрики для /стата

Первичная настройка: открыть в браузере  <адрес функции>?setup=<SECRET>
"""
import base64
import hashlib
import hmac
import secrets
import html
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

MAX_TOKEN = os.environ.get("MAX_TOKEN", "").strip()
GH_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
SECRET = os.environ.get("SECRET", "").strip()
REPO = os.environ.get("REPO", "3dmodelNagaevo/model3d-nagaevo").strip()
SITE_URL = os.environ.get("SITE_URL", "https://3dnagaevo.ru/").rstrip("/") + "/"
MAX_HOSTS = ["https://platform-api2.max.ru", "https://platform-api.max.ru"]
UPDATE_TYPES = ["message_created", "message_edited", "message_removed", "bot_started", "message_callback"]
UA = "model3d-bot-yandex"
CHANNEL_URL = os.environ.get("CHANNEL_URL", "https://max.ru/channel_model3Dnagaevo")
BOT_URL = os.environ.get("BOT_URL", "https://max.ru/se15070545_bot")
MASTER_URL = os.environ.get("MASTER_URL", "https://max.ru/u/f9LHodD0cOIASIo07UGTqsR7Fx0LPNcqKwAi9GFiTlXqkxZ281BEJNmiZK0")

# ---------------------------------------------------------------- HTTP

_CTX = None


def ctx():
    """Сертификаты Минцифры подгружаем один раз (нужны для platform-api2.max.ru)."""
    global _CTX
    if _CTX:
        return _CTX
    _CTX = ssl.create_default_context()
    for url in ("https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt",
                "https://gu-st.ru/content/lending/russian_trusted_sub_ca_pem.crt"):
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                _CTX.load_verify_locations(cadata=r.read().decode("ascii"))
        except Exception as e:
            print("cert:", e)
    return _CTX


def http(method, url, headers=None, body=None, timeout=20):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    h = {"User-Agent": UA, **(headers or {})}
    if data is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=timeout, context=ctx()) as r:
        raw = r.read().decode()
        return json.loads(raw) if raw.strip() else {}


def max_api(method, path, params=None, body=None):
    q = urllib.parse.urlencode(params or {})
    err = None
    for host in MAX_HOSTS:
        try:
            return http(method, f"{host}{path}" + (f"?{q}" if q else ""), {"Authorization": MAX_TOKEN}, body)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"MAX {e.code}: {e.read().decode('utf-8', 'replace')[:200]}")
        except Exception as e:
            err = e
    raise RuntimeError(f"MAX недоступен: {err}")


def gh(method, path, body=None):
    return http(method, f"https://api.github.com{path}",
                {"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"}, body)


def gh_file(name):
    """(данные, sha) файла из репозитория."""
    try:
        d = gh("GET", f"/repos/{REPO}/contents/{name}?ref=main")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None, None
        raise
    raw = d.get("content")
    if not raw:  # файл больше 1 МБ — через raw
        req = urllib.request.Request(d["download_url"], headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read()), d["sha"]
    return json.loads(base64.b64decode(raw)), d["sha"]


def gh_put(name, data, sha, message):
    body = {"message": message, "branch": "main",
            "content": base64.b64encode((json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()).decode(),
            "committer": {"name": "catalog-bot", "email": "catalog-bot@users.noreply.github.com"}}
    if sha:
        body["sha"] = sha
    gh("PUT", f"/repos/{REPO}/contents/{name}", body)


def gh_put_bytes(name, data, message):
    gh("PUT", f"/repos/{REPO}/contents/{name}", {
        "message": message, "branch": "main", "content": base64.b64encode(data).decode(),
        "committer": {"name": "catalog-bot", "email": "catalog-bot@users.noreply.github.com"}})


def update_json(name, fn, message, default=None):
    """Правка JSON-файла в репозитории с повтором при конфликте. Возвращает новые данные."""
    for _ in range(3):
        data, sha = gh_file(name)
        data = data if data is not None else (default if default is not None else {})
        fn(data)
        try:
            gh_put(name, data, sha, message)
            return data
        except urllib.error.HTTPError as e:
            if e.code != 409:
                raise
            time.sleep(1)
    raise RuntimeError("не удалось сохранить изменения (конфликт)")


def update_overrides(fn, message):
    return update_json("overrides.json", fn, message)


def run_sync():
    """Запустить обновление сайта на GitHub прямо сейчас."""
    gh("POST", f"/repos/{REPO}/actions/workflows/sync.yml/dispatches", {"ref": "main"})


# ---------------------------------------------------------------- меню и тексты

MENU_BUTTONS = {
    "📊 Статус": "/статус", "🔥 Топ": "/топ", "📈 Статистика": "/стата", "🔄 Обновить": "/обновить",
    "🙈 Скрыть": "/скрыть", "👁 Вернуть": "/показать", "💰 Цена": "/цена", "🖼 Обложка": "/обложка",
    "🎟 Промокоды": "/промо", "🗄 Резервная копия": "/копия", "🚧 Тех. работы": "/техработы", "📸 Новый пост": "/пост", "❓ Помощь": "/помощь", "📝 Шаблон": "/шаблон", "✏️ Изменить пост": "/изменить",
    "💬 Отзыв": "/отзыв", "🗂 Отзывы": "/отзывы", "👥 Доступ": "/доступ", "📜 История": "/история", "↩️ Отменить": "/отменить", "✅ Продано": "/продано", "🧾 Продажи": "/продажи",
    # старые подписи кнопок (из прошлых сообщений)
    "🔥 Топ недели": "/топ", "🙈 Скрыть товар": "/скрыть", "👁 Вернуть товар": "/показать",
    "💰 Изменить цену": "/цена", "🔄 Обновить сайт": "/обновить",
}
# по 2 кнопки в ряд — иначе MAX обрезает подписи
MENU_ROWS = [
    ["📊 Статус", "📈 Статистика"],
    ["✅ Продано", "🧾 Продажи"],
    ["📜 История", "↩️ Отменить"],
    ["🔥 Топ", "🔄 Обновить"],
    ["🙈 Скрыть", "👁 Вернуть"],
    ["💰 Цена", "🖼 Обложка"],
    ["💬 Отзыв", "🗂 Отзывы"],
    ["📸 Новый пост", "✏️ Изменить пост"],
    ["🎟 Промокоды", "📝 Шаблон"],
    ["👥 Доступ", "🗄 Резервная копия"],
    ["🚧 Тех. работы", "❓ Помощь"],
]
POST_TEMPLATE = """Название модели
Пара слов о модели (можно удалить эту строку)
- Размер: (по желанию)
- Цвет: согласовывать в ЛС
- Стоимость: 300 ₽
#антистресс"""

TEMPLATE_HELP = ("📝 Шаблон поста — скопируйте следующее сообщение, вставьте в канал вместе с фото и поменяйте:\n"
                 "• первую строку — название;\n• цену — только число;\n• хэштег — категорию на сайте:\n"
                 "#антистресс #фигурки #декор #авто #дом #подарки #геймерам #набор\n\n"
                 "Строки «Размер» и «Цвет» можно оставить как есть. "
                 "Пост без строки «Стоимость» на сайт не попадёт, а с #нетнасайте — не попадёт специально.")

EN = {"/menu": "/меню", "/status": "/статус", "/top": "/топ", "/stats": "/стата", "/help": "/помощь", "/start": "/помощь"}

HELP = """🤖 Бот сайта. Кнопки — под этим сообщением, или пишите команды:

/статус — сколько товаров на сайте
/топ — популярное за неделю
/стата — статистика за неделю
✅ Продано — затем просто номера: «38», «20 2» (2 шт.), можно несколько сразу
/отменить — отменить последнюю отметку о продажах
/история — все отметки о продажах с номерами
/удалить продажу 7 — удалить отметку #7
/сбросить продажи 38 — обнулить «Продано» у товара №38
/продажи — продажи за неделю и месяц
/скрыть 12 — убрать товар №12 с сайта (пост в канале остаётся)
/показать 12 — вернуть товар на сайт
/цена 12 300 — поменять цену на сайте
/цена 12 сброс — вернуть цену из поста
/обложка 12 — показать фото товара №12 и выбрать главное
/обложка 12 3 — сделать 3-е фото главным
/обновить — обновить сайт прямо сейчас
/изменить 12 — изменить пост товара №12 в канале (только посты, опубликованные ботом)
🎟 Промокоды — создать, выложить в канал, автовыкладка по расписанию
/код ЗИМА15 — проверить промокод из заказа и активировать, когда заказ выполнен
🗄 Резервная копия — архив сайта, данных и кода прямо сейчас
🚧 Тех. работы — баннер или закрыть сайт на время работ
/права 2 — какие разделы доступны админу
/доступ — кто из админов может пользоваться ботом (только для владельца)
/пригласить — ссылка-приглашение: дать доступ человеку, который не админ канала
/шаблон — шаблон поста для канала
/отзыв текст (+ фото) — добавить отзыв на сайт, /отзывы — список
/проверка — отзывы покупателей с сайта, /одобрить 5 или /отклонить 5

📸 Публикация: пришлите фото с подписью
/пост Дракон-антистресс
Цена: 200
#антистресс

Номер товара (№) виден на сайте в карточке товара."""


def keyboard():
    rows = [[{"type": "message", "text": t} for t in r] for r in MENU_ROWS]
    rows.append([{"type": "link", "text": "🛠 Приложение управления", "url": BOT_URL + "?startapp=admin"},
                 {"type": "link", "text": "🌐 Сайт", "url": SITE_URL}])
    return {"type": "inline_keyboard", "payload": {"buttons": rows}}


_capture = []          # приложение управления: ответы бота собираем в ответ API, а не шлём в чат


def send(uid, text, menu=True, buttons=None):
    """buttons — ряды кнопок-действий [[("✅ Опубликовать", "/одобрить 5"), …], …]: нажал — и команда выполнена."""
    if _capture and _capture[-1].get("uid") == uid:
        _capture[-1]["texts"].append(text)
        return
    body = {"text": text[:3900]}
    rows = [[{"type": "callback", "text": b[0][:60], "payload": b[1][:1000]} if len(b) == 2 else
             {"type": "clipboard", "text": b[0][:60], "payload": b[1][:1000]} if b[2] == "clipboard" else
             {"type": "link", "text": b[0][:60], "url": b[1]} for b in row] for row in (buttons or []) if row]
    if menu:
        kb = keyboard()
        kb["payload"]["buttons"] = rows + kb["payload"]["buttons"]
        body["attachments"] = [kb]
    elif rows:
        body["attachments"] = [{"type": "inline_keyboard", "payload": {"buttons": rows}}]
    max_api("POST", "/messages", {"user_id": uid, "disable_link_preview": "true"}, body)


# ---------------------------------------------------------------- команды без номера: бот спрашивает номер

ASK = {
    "/цена": "💰 Напишите номер товара и новую цену через пробел:\n12 300\n\nВернуть цену из поста: 12 сброс",
    "/скрыть": "🙈 Какой товар убрать с сайта? Напишите его номер, например: 12",
    "/показать": "👁 Какой товар вернуть на сайт? Напишите его номер, например: 12",
    "/обложка": "🖼 У какого товара сменить главное фото? Напишите номер, например: 12",
    "/изменить": "✏️ Напишите номер товара и что поменять:\n12 цена 350\n12 размер 10 см\n12 цвет любой\n12 название Дракон большой\n\n"
                 "Или только номер — пришлю весь текст поста.\n(Бот меняет только посты, которые сам опубликовал.)",
    "/удалить отзыв": "🗑 Напишите номер отзыва, который убрать с сайта. Номера — в «🗂 Отзывы».",
    "/удалить продажу": "🗑 Напишите номер отметки из «📜 История», например: 7",
    "/сбросить продажи": "🧹 У какого товара обнулить «Продано»? Напишите номер, например: 38",
    "/разрешить": "👥 Напишите номер админа из списка «👥 Доступ», которому дать доступ.",
    "/запретить": "👥 Напишите номер админа из списка «👥 Доступ», у которого забрать доступ.",
}
ASK_ALIASES = {"/удалить_отзыв": "/удалить отзыв", "удалить отзыв": "/удалить отзыв", "/удалить_продажу": "/удалить продажу",
               "удалить продажу": "/удалить продажу", "/сбросить_продажи": "/сбросить продажи", "/показать": "/показать",
               "/изменить пост": "/изменить", "изменить": "/изменить", "цена": "/цена", "скрыть": "/скрыть", "обложка": "/обложка"}
CANCEL = [[("❌ Отмена", "/отмена")]]
_await = {}            # uid → (команда, время): ждём номер следующим сообщением


def ask_or_fill(uid, text):
    """Голая команда → спросить номер. Следующее сообщение без «/» → дописать к команде.
    Возвращает (текст для выполнения | None, отправлено_ли_сообщение)."""
    bare = re.sub(r"\s+", " ", text.strip().lower())
    bare = ASK_ALIASES.get(bare, bare)
    if bare in ASK:
        _await[uid] = (bare, time.time())
        send(uid, ASK[bare], menu=False, buttons=CANCEL)
        return None, True
    if bare in ("/отмена", "отмена"):
        had = _await.pop(uid, None)
        send(uid, "Отменил." if had else "Нечего отменять.")
        return None, True
    if text.startswith("/") or text in MENU_BUTTONS:
        _await.pop(uid, None)
        return text, False
    w = _await.pop(uid, None)
    if w and time.time() - w[1] < 900:
        return f"{w[0]} {text}", False
    return text, False


def num(s):
    m = re.search(r"\d+", s or "")
    return int(m.group(0)) if m else None


def catalog_items():
    data, _ = gh_file("catalog.json")
    return (data or {}).get("items", [])


def fmt_price(p):
    return "цена по запросу" if p is None else f"{p} ₽"


def top_text(items, key="views_week", label="за неделю", n=5):
    if key == "views_week" and any(i.get("sold_week") for i in items):
        rows = sorted([i for i in items if not i.get("hidden") and (i.get("sold_week") or i.get("views_week"))],
                      key=lambda i: (-(i.get("sold_week") or 0), -(i.get("views_week") or 0)))[:n]
        return "🔥 Хиты недели (продажи, потом просмотры):\n" + "\n".join(
            f"{k}. №{i['no']} {i['title']} — продано {i.get('sold_week', 0)}, {i.get('views_week', 0)} просм."
            for k, i in enumerate(rows, 1))
    rows = sorted([i for i in items if i.get(key) and not i.get("hidden")], key=lambda i: -i[key])[:n]
    if not rows and key != "views":
        return top_text(items, "views", "за всё время", n)
    if not rows:
        return "🔥 Пока нет данных о просмотрах."
    return f"🔥 Популярное {label}:\n" + "\n".join(f"{k}. №{i['no']} {i['title']} — {i[key]} просм." for k, i in enumerate(rows, 1))


def _find(items, no):
    return next((i for i in items if i.get("no") == no), None)


def sales_text(items, sales, days_list=(7, 30)):
    if not sales:
        return "🧾 Продаж пока не отмечено. После продажи нажмите «✅ Продано» и напишите номер товара."
    now = time.time() * 1000
    parts = []
    for days in days_list:
        agg = {}
        for r in sales:
            if r.get("ts", 0) >= now - days * 86400 * 1000:
                agg[r["no"]] = agg.get(r["no"], 0) + int(r.get("qty") or 1)
        if not agg:
            parts.append(f"🧾 За {days} дн.: продаж не было")
            continue
        qty = sum(agg.values())
        money = sum(((_find(items, no) or {}).get("price") or 0) * q for no, q in agg.items())
        top = sorted(agg.items(), key=lambda x: -x[1])[:5]
        money_s = f"{money:,}".replace(",", " ")
        lines = [f"🧾 За {days} дн.: продано {qty} шт., примерно на {money_s} ₽ (по ценам с сайта)"]
        lines += [f"   • №{no} {(_find(items, no) or {}).get('title', '?')} — {q} шт." for no, q in top]
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def status_text(items, ov):
    vis = [i for i in items if not i.get("hidden")]
    lines = [f"📊 На сайте: {len(vis)} товаров"]
    last = max((i.get("ts", 0) for i in items if i.get("source") == "max"), default=0)
    if last:
        lines.append("🕐 Последний товар: " + time.strftime("%d.%m %H:%M", time.gmtime(last / 1000 + 3 * 3600)))
    if ov.get("hidden"):
        lines.append("🙈 Скрыты: " + ", ".join(f"№{n}" for n in ov["hidden"]))
    if ov.get("price"):
        lines.append("💰 Цены изменены вручную: " + ", ".join(f"№{k} → {v} ₽" for k, v in ov["price"].items()))
    return "\n".join(lines)


METRIKA_DEFAULT_ID = "114366870"


def _metrika(path, params, tok):
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"https://api-metrika.yandex.net{path}?{q}",
                                 headers={"Authorization": f"OAuth {tok}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def metrika_from():
    """Момент, с которого считать статистику (кнопка «Начать отсчёт с этого момента») — unix-время или 0."""
    try:
        d, _ = gh_file("settings.json")
        return int((d or {}).get("metrika_from_ts") or 0)
    except Exception:
        return 0


_mtz = {}


def metrika_tz(mid, tok):
    """Часовой пояс счётчика (даты в отчётах Метрики — в нём)."""
    if "tz" not in _mtz:
        try:
            c = _metrika(f"/management/v1/counter/{mid}", {}, tok).get("counter") or {}
            _mtz["tz"] = c.get("time_zone_name") or "Europe/Moscow"
        except Exception:
            _mtz["tz"] = "Europe/Moscow"
    return _mtz["tz"]


def metrika_period(days, mid="", tok=""):
    """→ (date1, фильтр по времени или "", подпись «с какого момента» или "")."""
    import datetime as _dt
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(metrika_tz(mid, tok)) if tok else _dt.timezone(_dt.timedelta(hours=3))
    except Exception:
        tz = _dt.timezone(_dt.timedelta(hours=3))
    now = _dt.datetime.now(tz)
    start = now.date() - _dt.timedelta(days=days - 1)
    ts = metrika_from()
    if ts:
        cut = _dt.datetime.fromtimestamp(ts, tz)
        if cut.date() >= start:
            label = (_dt.datetime.fromtimestamp(ts, _dt.timezone(_dt.timedelta(hours=5)))).strftime("%d.%m.%Y %H:%M")
            return cut.date().isoformat(), f"ym:s:dateTime>='{cut.strftime('%Y-%m-%d %H:%M:%S')}'", label
    return start.isoformat(), "", ""


def metrika_text(days=7):
    """Сводка Метрики: посетители, визиты, источники, цели (нажатия «Заказать» и т.п.)."""
    mid = os.environ.get("METRIKA_ID", "").strip() or METRIKA_DEFAULT_ID
    tok = os.environ.get("METRIKA_TOKEN", "").strip()
    if not tok:
        return ""
    d1, cutf, _l = metrika_period(days, mid, tok)
    period = {"date1": d1, "date2": "today", "ids": mid, "accuracy": "full", "lang": "ru"}
    if cutf:
        period["filters"] = cutf
    lines = []
    try:
        d = _metrika("/stat/v1/data", {**period, "metrics": "ym:s:users,ym:s:visits,ym:s:pageviews"}, tok)
        u, v, p = (int(x) for x in d["totals"])
        lines.append(f"🌐 Сайт за {days} дн.: {u} посетителей, {v} визитов, {p} просмотров страниц")
    except Exception as e:
        print(f"Метрика: {e}")
        return "🌐 Метрика: не удалось получить данные (проверьте токен METRIKA_TOKEN)."
    try:  # откуда пришли
        d = _metrika("/stat/v1/data", {**period, "metrics": "ym:s:visits",
                                       "dimensions": "ym:s:lastTrafficSource", "sort": "-ym:s:visits", "limit": 5}, tok)
        src = [f"{r['dimensions'][0]['name']} — {int(r['metrics'][0])}" for r in d.get("data", []) if r["metrics"][0]]
        if src:
            lines.append("↪️ Откуда: " + ", ".join(src))
        d = _metrika("/stat/v1/data", {**period, "metrics": "ym:s:visits",
                                       "filters": "ym:s:UTMSource=='flyer'" + (f" AND {cutf}" if cutf else "")}, tok)
        fl = int(d["totals"][0]) if d.get("totals") else 0
        if fl:
            lines.append(f"📄 С листовки (QR): {fl} визитов")
    except Exception as e:
        print(f"Метрика (источники): {e}")
    try:  # цели
        goals = _metrika(f"/management/v1/counter/{mid}/goals", {}, tok).get("goals", [])
        goals = [g for g in goals if g.get("id")][:15]
        if goals:
            metrics = ",".join(f"ym:s:goal{g['id']}reaches" for g in goals)
            d = _metrika("/stat/v1/data", {**period, "metrics": metrics}, tok)
            got = [(g["name"], int(n)) for g, n in zip(goals, d.get("totals", []))]
            got = sorted([x for x in got if x[1]], key=lambda x: -x[1])
            lines.append("🎯 Цели: " + (", ".join(f"{n} — {c}" for n, c in got) if got else "пока не было"))
    except Exception as e:
        print(f"Метрика (цели): {e}")
    return "\n".join(lines)


GOAL_NAMES = {"order_max": "Заказ в MAX", "order_tg": "Заказ в Telegram", "order_call": "Звонок", "fav_send_max": "Список из избранного",
              "fav_send_tg": "Список в Telegram", "open_model": "Открыли карточку", "share": "Поделились", "channel_max": "Перешли в канал",
              "contact_max": "Написать в MAX", "find_max": "Найти под заказ", "own_max": "Своя модель", "get_discount": "Получить скидку",
              "promo_check": "Проверка промокода", "review_sent": "Отзыв отправлен", "open_post": "Пост в канале"}


def metrika_data(days=30, date1="", date2="", ignore_cut=False):
    """Метрика для приложения: графики по дням, источники, устройства, города, цели, метки, часы, поисковые фразы."""
    from concurrent.futures import ThreadPoolExecutor
    mid = os.environ.get("METRIKA_ID", "").strip() or METRIKA_DEFAULT_ID
    tok = os.environ.get("METRIKA_TOKEN", "").strip()
    if not tok:
        return {"error": "Не задан токен Метрики (METRIKA_TOKEN в настройках функции в Яндекс Облаке)."}
    d1, cutf, label = metrika_period(days, mid, tok)
    d2 = "today"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date1 or "") and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date2 or ""):
        lo, hi = sorted([date1, date2])                # свой период; отсечка «с этого момента» тоже действует
        _a, cutf, label = metrika_period(3650, mid, tok)
        cut_day = _a if cutf else ""
        d1 = max(lo, cut_day) if cut_day else lo
        d2 = hi
        if cut_day and cut_day > hi:                   # весь период раньше отсечки — данных нет
            d1 = hi
        if cut_day and cut_day < lo:
            cutf = ""
    if ignore_cut and cutf:                            # «показать всё временно» — без отсечки, настройка не меняется
        if date1 and date2:
            d1 = min(date1, date2)
        else:
            import datetime as _dt
            d1 = (_dt.datetime.utcnow() + _dt.timedelta(hours=5)).date().isoformat()
            d1 = (_dt.date.fromisoformat(d1) - _dt.timedelta(days=days - 1)).isoformat()
        cutf = ""
    per = {"date1": d1, "date2": d2, "ids": mid, "accuracy": "full", "lang": "ru"}
    approx = []

    def q(params):
        p = {**per, **params}
        if cutf:                                       # «с этого момента» — фильтр по времени визита
            p["filters"] = f"({params['filters']}) AND {cutf}" if params.get("filters") else cutf
        try:
            return _metrika("/stat/v1/data", p, tok)
        except urllib.error.HTTPError as e:
            if cutf and e.code == 400:                  # Метрика не приняла фильтр по времени — считаем с начала дня
                approx.append(1)
                p.pop("filters", None)
                if params.get("filters"):
                    p["filters"] = params["filters"]
                try:
                    return _metrika("/stat/v1/data", p, tok)
                except Exception as e2:
                    print("metrika:", e2)
            print("metrika:", params.get("dimensions"), e)
            return None
        except Exception as e:
            print("metrika:", params.get("dimensions"), e)
            return None

    def top(dim, limit=8, metric="ym:s:visits", filters=None):
        p = {"metrics": metric, "dimensions": dim, "sort": "-" + metric, "limit": limit}
        if filters:
            p["filters"] = filters
        d = q(p)
        return [[(r["dimensions"][0].get("name") or "—"), int(r["metrics"][0])] for r in (d or {}).get("data", []) if r["metrics"][0]]

    jobs = {
        "totals": lambda: q({"metrics": "ym:s:users,ym:s:visits,ym:s:pageviews,ym:s:bounceRate,ym:s:avgVisitDurationSeconds,ym:s:percentNewVisitors"}),
        "daily": lambda: q({"metrics": "ym:s:users,ym:s:visits", "dimensions": "ym:s:date", "sort": "ym:s:date", "limit": 400}),
        "sources": lambda: top("ym:s:lastTrafficSource", 6),
        "engines": lambda: top("ym:s:lastSourceEngine", 8),
        "devices": lambda: top("ym:s:deviceCategory", 4),
        "cities": lambda: top("ym:s:regionCity", 8),
        "utm": lambda: top("ym:s:UTMSource", 8),
        "phrases": lambda: top("ym:s:lastSearchPhrase", 8),
        "hours": lambda: q({"metrics": "ym:s:visits", "dimensions": "ym:s:hour", "sort": "ym:s:hour", "limit": 24}),
        "models": lambda: top("ym:s:paramsLevel2", 10, "ym:s:visits", "ym:s:paramsLevel1=='title'"),
        "goals": lambda: _metrika(f"/management/v1/counter/{mid}/goals", {}, tok).get("goals", []),
    }
    with ThreadPoolExecutor(12) as ex:
        futs = {k: ex.submit(f) for k, f in jobs.items()}
        res = {}
        for k, f in futs.items():
            try:
                res[k] = f.result()
            except Exception as e:
                print("metrika job", k, e)
                res[k] = None
    t = (res.get("totals") or {}).get("totals") or [0] * 6
    out = {"days": days, "from": label, "approx": bool(approx), "tempAll": bool(ignore_cut and label),
           "totals": {"users": int(t[0]), "visits": int(t[1]), "pageviews": int(t[2]), "bounce": round(t[3], 1),
                      "duration": int(t[4]), "new": round(t[5], 1)},
           "daily": [[r["dimensions"][0]["name"], int(r["metrics"][0]), int(r["metrics"][1])] for r in (res.get("daily") or {}).get("data", [])],
           "hours": [[int(r["dimensions"][0]["name"].split(":")[0]) if r["dimensions"][0].get("name") else 0, int(r["metrics"][0])]
                     for r in (res.get("hours") or {}).get("data", [])]}
    for k in ("sources", "engines", "devices", "cities", "utm", "phrases", "models"):
        out[k] = res.get(k) or []
    goals = [g for g in (res.get("goals") or []) if g.get("id")][:20]
    if goals:
        d = q({"metrics": ",".join(f"ym:s:goal{g['id']}reaches" for g in goals)})
        vals = (d or {}).get("totals") or []
        named = []
        for g, n in zip(goals, vals):
            nm = g.get("name") or ""
            for key, ru in GOAL_NAMES.items():
                if nm == key or key in str((g.get("conditions") or [{}])[0].get("url", "")) or (key in nm and not re.search(r"[а-яё]", nm.lower())):
                    nm = ru
                    break
            named.append([nm, int(n)])
        out["goals"] = sorted(named, key=lambda x: -x[1])
    else:
        out["goals"] = []
    return out


def stats_text(items, sales=None):
    week = 7 * 86400 * 1000
    now = time.time() * 1000
    new = [i for i in items if i.get("source") == "max" and now - i.get("ts", 0) < week]
    views = sum(i.get("views_week", 0) for i in items)
    head = (sales_text(items, sales, (7,)) + "\n\n") if sales is not None else ""
    return head + (f"🆕 Новых товаров за неделю: {len(new)}\n👁 Просмотров постов-товаров за неделю: {views}\n"
            f"📦 Всего на сайте: {len([i for i in items if not i.get('hidden')])}\n"
            + (metrika_text() + "\n" if os.environ.get("METRIKA_TOKEN") else "") + "\n" + top_text(items))


# ---------------------------------------------------------------- публикация /пост

FIELD_RE = re.compile(r"^(размер|цвет|стоимость|цена|материал)\s*[:\-—–]\s*(.*)$", re.I)
TAG_RE = re.compile(r"#([\wё]+)", re.I)


def build_post(text, images):
    body = re.sub(r"^/(пост|post|новый)\s*", "", text.strip(), flags=re.I)
    tags = [t.lower() for t in TAG_RE.findall(body)]
    fields, rest = {}, []
    for raw in body.splitlines():
        line = TAG_RE.sub("", raw).strip(" -•")
        if not line:
            continue
        m = FIELD_RE.match(line)
        if m:
            k = m.group(1).lower()
            fields["price" if k in ("стоимость", "цена") else k] = m.group(2).strip()
        else:
            rest.append(line)
    pm = re.search(r"\d[\d\s]*", fields.get("price", ""))
    price = int(re.sub(r"\D", "", pm.group(0))) if pm else None
    if not rest:
        raise ValueError("нет названия — напишите его сразу после /пост")
    if price is None:
        raise ValueError("нет цены — добавьте строку «Цена: 200»")
    lines = rest + [
        f"- Размер: ({fields.get('размер', 'по желанию').strip('()')})",
        f"- Цвет: {fields.get('цвет', 'согласовывать в ЛС')}",
        f"- Стоимость: {price} ₽",
    ]
    if tags:
        lines.append(" ".join("#" + t for t in tags))
    atts = []
    for a in images[:10]:
        p = a.get("payload") or {}
        if p.get("token"):
            atts.append({"type": "image", "payload": {"token": p["token"]}})
        elif p.get("url"):
            atts.append({"type": "image", "payload": {"url": p["url"]}})
    return "\n".join(lines), atts


# ---------------------------------------------------------------- редактирование постов бота

EDIT_HELP = """✏️ Изменить пост в канале

Бот может менять только посты, которые сам опубликовал (через «📸 Новый пост»). Свои посты меняйте в MAX вручную.

Поменять одну строку:
/изменить 12 цена 350
/изменить 12 размер 10 см
/изменить 12 цвет любой
/изменить 12 название Дракон большой

Заменить весь текст: /изменить 12 — пришлю текст поста, скопируйте, исправьте и отправьте обратно.
Фото в посте остаются прежними. 12 — номер товара на сайте."""

EDIT_FIELDS = {"цена": "Стоимость", "стоимость": "Стоимость", "размер": "Размер", "цвет": "Цвет",
               "материал": "Материал", "название": None}
FIELD_LINE = {"Стоимость": re.compile(r"^\s*[-•]?\s*(стоимость|цена)\s*:", re.I),
              "Размер": re.compile(r"^\s*[-•]?\s*размер\s*:", re.I),
              "Цвет": re.compile(r"^\s*[-•]?\s*цвет\s*:", re.I),
              "Материал": re.compile(r"^\s*[-•]?\s*материал\s*:", re.I)}


def set_field(text, field, value):
    lines = text.splitlines()
    if field is None:                                  # название — первая строка поста
        for i, l in enumerate(lines):
            if l.strip():
                lines[i] = value
                break
        return "\n".join(lines)
    if field == "Стоимость":
        n = re.sub(r"\D", "", value)
        value = f"{n} ₽" if n and not re.search(r"[а-яa-z]", value, re.I) else value
    if field == "Размер" and not value.startswith("("):
        value = f"({value})"
    new = f"- {field}: {value}"
    for i, l in enumerate(lines):
        if FIELD_LINE[field].match(l):
            lines[i] = new
            return "\n".join(lines)
    k = len(lines)                                     # строки нет — вставляем перед хэштегами
    while k > 0 and lines[k - 1].strip().startswith("#"):
        k -= 1
    lines.insert(k, new)
    return "\n".join(lines)


def edit_post(uid, text):
    m = re.match(r"^/?изменить(?:\s+пост)?\s*(\d+)?[ \t]*(.*)$", text, re.S | re.I)
    no = int(m.group(1)) if m and m.group(1) else None
    rest = (m.group(2) if m else "").strip()
    if not no:
        return send(uid, EDIT_HELP)
    item = _find(catalog_items(), no)
    if not item or not item.get("mid"):
        return send(uid, f"Товара №{no} нет на сайте. Номер виден в карточке товара.")
    try:
        cur = (max_api("GET", f"/messages/{item['mid']}").get("body") or {}).get("text") or ""
    except Exception as e:
        return send(uid, f"❌ Не смог прочитать пост №{no}: {e}")
    if not rest:
        send(uid, f"✏️ Пост №{no} «{item['title']}». Скопируйте сообщение ниже, исправьте текст и отправьте обратно "
                  f"(первую строку «/изменить {no}» не удаляйте).\n\nИли поменяйте одну строку: /изменить {no} цена 350",
             menu=False)
        return send(uid, f"/изменить {no}\n{cur}")
    first, _, tail = rest.partition("\n")
    key = first.split()[0].lower().rstrip(":") if first.split() else ""
    if not tail.strip() and key in EDIT_FIELDS:
        value = first.split(None, 1)[1].strip() if len(first.split(None, 1)) > 1 else ""
        if not value:
            return send(uid, f"Напишите новое значение, например: /изменить {no} {key} …")
        new = set_field(cur, EDIT_FIELDS[key], value)
    elif "\n" in text.strip():
        new = rest
    else:
        return send(uid, EDIT_HELP)
    if new.strip() == cur.strip():
        return send(uid, "Текст не изменился — пост оставил как есть.")
    try:
        max_api("PUT", "/messages", {"message_id": item["mid"]}, {"text": new[:4000]})
    except Exception as e:
        err = str(e)
        if any(w in err.lower() for w in ("403", "forbidden", "not allowed", "permission", "own", "access")):
            return send(uid, f"🚫 Пост №{no} публиковал не бот, а человек — MAX не разрешает боту его менять. "
                             f"Исправьте его вручную в канале.\n\n({err[:150]})")
        return send(uid, f"❌ MAX не дал изменить пост №{no}:\n{err[:300]}")
    note = ""
    if EDIT_FIELDS.get(key) == "Стоимость":
        ov, _ = gh_file("overrides.json")
        if str(no) in ((ov or {}).get("price") or {}):
            note = f"\n\n⚠️ На сайте для №{no} стоит цена из «💰 Цена» — она важнее поста. Вернуть цену из поста: /цена {no} сброс"
    return send(uid, f"✅ Пост №{no} изменён в канале. Сайт обновится через 1–2 минуты." + note)



# ---------------------------------------------------------------- промокоды

TZ = 5 * 3600                     # время в расписании — уфимское (UTC+5)
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
EVERY = ["каждый понедельник", "каждый вторник", "каждую среду", "каждый четверг", "каждую пятницу",
         "каждую субботу", "каждое воскресенье"]
PROMO_HELP_NEW = ("🎟 Новый промокод. Напишите одной строкой:\nКОД  СКИДКА  КОЛИЧЕСТВО  ДНЕЙ\n\n"
                  "Например: ЗИМА15 15% 3 7\n— код ЗИМА15, скидка 15%, 3 активации, действует 7 дней после публикации.\n\n"
                  "Скидка — в процентах (15%) или рублях (100₽). Вместо кода напишите «авто» — придумаю сам.")
PROMO_HELP_SCHED = ("📅 Автовыкладка промокодов в канал. Напишите:\nДЕНЬ  ВРЕМЯ  СКИДКА  КОЛИЧЕСТВО  ДНЕЙ\n\n"
                    "Например: пт 18:00 10% 3 7\n— каждую пятницу в 18:00 (по Уфе) в канале выходит новый промокод: "
                    "скидка 10%, 3 активации, действует 7 дней.\n\nЕсли в очереди есть ваши коды — беру их по порядку, "
                    "если нет — придумываю код сам. Выключить: стоп")
_LAT2CYR = str.maketrans("ABCEHKMOPTXY", "АВСЕНКМОРТХУ")
PROMO_HELP_SUB = ("🎁 Скидка за подписку на канал.\nПокупатель жмёт на сайте «Получить скидку» → бот проверяет, что он подписан "
                  "на канал → присылает ему личный промокод (одна активация, один код на человека).\n\n"
                  "Напишите: СКИДКА  ДНЕЙ\nНапример: 10% 14 — скидка 10%, код действует 14 дней.\nВыключить: стоп")


def norm_code(c):
    return re.sub(r"[\s\-_]", "", (c or "").upper().replace("Ё", "Е")).translate(_LAT2CYR)


def plural_act(n):
    n = int(n)
    w = "активация" if n % 10 == 1 and n % 100 != 11 else (
        "активации" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "активаций")
    return f"{n} {w}"


CODE_WORDS = ["ПОДАРОК", "ПЕЧАТЬ", "МОДЕЛЬ", "ДРАКОН", "КОТИК", "СЮРПРИЗ", "РАДОСТЬ", "НАГАЕВО", "ФИГУРКА", "УДАЧА"]


def fmt_disc(c):
    return f"{c['value']}%" if c.get("kind") == "%" else f"{c['value']} ₽"


def parse_disc(s):
    m = re.fullmatch(r"(\d{1,5})\s*(%|₽|р|руб\.?|r)?", (s or "").strip().lower())
    if not m:
        return None
    v = int(m.group(1))
    kind = "%" if m.group(2) == "%" else "₽"
    if kind == "%" and not 1 <= v <= 90:
        return None
    return kind, v


def promo_status(c, now=None):
    now = now or time.time()
    if c.get("status") == "active":
        if c.get("until") and now > c["until"]:
            return "ended", "срок вышел"
        if c.get("limit") and c.get("used", 0) >= c["limit"]:
            return "ended", "закончились"
    return c.get("status", "draft"), ""


def promo_line(c):
    st, why = promo_status(c)
    mark = {"active": "🟢", "queued": "📅", "draft": "📝", "ended": "⚪️"}.get(st, "")
    until = time.strftime(" до %d.%m", time.gmtime(c["until"] + TZ)) if c.get("until") and st == "active" else ""
    return (f"{mark} {c['code']} — {fmt_disc(c)}, активаций {c.get('used', 0)} из {c.get('limit') or '∞'}{until}"
            + (f" ({why})" if why else ""))


def promo_post_text(c):
    until = time.strftime("%d.%m", time.gmtime(c["until"] + TZ)) if c.get("until") else ""
    return (f"🎁 Промокод:\n\n{c['code']}\n\n"
            f"Скидка {fmt_disc(c)} на любой заказ.\n"
            + (f"⚡️ Количество ограничено: всего {plural_act(c['limit'])}"
               + (f", осталось {c['limit'] - c.get('used', 0)}" if c.get("used") else "")
               + ". Промокод активируется, когда заказ с ним выполнен — успейте заказать!\n" if c.get("limit") else "")
            + (f"Действует до {until} включительно." if until else "")
            + f"\n\n👉 Как получить скидку: выберите модель и напишите мастеру заказ, добавив в сообщение промокод. "
              f"Каталог: {SITE_URL}\n#промокод")


def gen_code(existing):
    import random
    while True:
        c = random.choice(CODE_WORDS) + str(random.randint(10, 99))
        if c not in existing:
            return c


def code_hash(code):
    import hashlib, hmac
    return hmac.new((SECRET or "salt").encode(), norm_code(code).encode(), hashlib.sha256).hexdigest()[:24]


def find_code(d, code):
    """Обычные коды хранятся как есть; личные (за подписку) — только хэшем, чтобы их не было видно на GitHub."""
    n = norm_code(code)
    c = next((c for c in d.get("codes", []) if c.get("code") and norm_code(c["code"]) == n), None)
    if c:
        return c
    h = code_hash(code)
    c = next((c for c in d.get("codes", []) if c.get("h") == h), None)
    if c:
        c["code"] = n                                    # только в памяти; при сохранении убирается
    return c


def promo_update(fn, message):
    def wrapped(d):
        fn(d)
        for c in d.get("codes", []):
            if c.get("personal"):
                c.pop("code", None)
    return update_json("promos.json", wrapped, message, {"codes": []})


def promo_publish(c):
    """Включить промокод (сам пост в канал — отдельно, после сохранения: см. promo_post)."""
    c["status"] = "active"
    c["start"] = int(time.time())
    c["until"] = c["start"] + c["days"] * 86400 if c.get("days") else None


def promo_kb(code, copy=True):
    """Кнопки под постом: скопировать код одним нажатием, заказать, каталог."""
    rows = []
    if copy:
        rows.append([{"type": "clipboard", "text": f"📋 Скопировать промокод {code}", "payload": code}])
    rows.append([{"type": "link", "text": "✍️ Заказать у мастера", "url": MASTER_URL},
                 {"type": "link", "text": "🛍 Каталог", "url": SITE_URL + "?utm_source=promo"}])
    return {"type": "inline_keyboard", "payload": {"buttons": rows}}


def post_with_kb(method, params, text, code):
    """Пост/правка с кнопкой «Скопировать»; если MAX её не примет — без неё."""
    for copy in (True, False):
        try:
            return max_api(method, "/messages", params, {"text": text, "attachments": [promo_kb(code, copy)]})
        except Exception as e:
            print("promo kb:", copy, e)
    return max_api(method, "/messages", params, {"text": text})


def promo_post(code):
    d, _ = gh_file("promos.json")
    c = find_code(d or {}, code)
    r = post_with_kb("POST", {"chat_id": config()["channel"]}, promo_post_text(c), c["code"])
    mid = ((r.get("message") or {}).get("body") or {}).get("mid")
    if mid:
        promo_update(lambda dd: find_code(dd, code).update(mid=mid, kb=True), "Бот: пост промокода")


def promo_end_post(c, reason):
    if c.get("mid") and not c.get("post_closed"):
        try:
            max_api("PUT", "/messages", {"message_id": c["mid"]},
                    {"text": promo_post_text(c).replace("🎁 Промокод:", "⛔️ Промокод больше не действует:") + f"\n\n({reason})",
                     "attachments": []})                  # кнопки «Скопировать/Заказать» убираем
            c["post_closed"] = True
        except Exception as e:
            print("promo end post:", e)


def promo_buttons(c):
    st, _ = promo_status(c)
    k = c["code"]
    if st in ("draft", "queued"):
        return [[("📣 Опубликовать сейчас", f"/промо опубликовать {k}")],
                [("📅 В очередь" if st == "draft" else "↩️ Из очереди", f"/промо {'очередь' if st == 'draft' else 'черновик'} {k}"),
                 ("🤫 Без поста", f"/промо тихо {k}")],
                [("🗑 Удалить", f"/промо удалить {k}")]]
    if st == "active":
        return [[("✅ Заказ выполнен — активировать", f"/промо списать {k}")], [("⛔️ Остановить промокод", f"/промо стоп {k}")]]
    return []


def promo_cmd(uid, text):
    rest = re.sub(r"^/?промо(код[ыа]?)?\s*", "", text.strip(), flags=re.I)
    words = rest.split()
    act = words[0].lower() if words else ""
    arg = " ".join(words[1:])

    if not act:                                         # обзор
        d, _ = gh_file("promos.json")
        d = d or {}
        codes = d.get("codes", [])
        sch = d.get("schedule") or {}
        lines = ["🎟 Промокоды\n"]
        if sch.get("on"):
            lines.append(f"📅 Автовыкладка: {EVERY[sch['wd']]} в {sch['hm']} (Уфа) — "
                         f"{sch['value']}{'%' if sch['kind'] == '%' else ' ₽'}, {plural_act(sch['limit'])}, {sch['days']} дн.\n")
        else:
            lines.append("📅 Автовыкладка выключена.\n")
        sub = d.get("sub") or {}
        pers = [c for c in codes if c.get("personal")]
        lines.append(("🎁 Скидка за подписку: " + (f"{sub['value']}{'%' if sub['kind'] == '%' else ' ₽'}, личный код на {sub['days']} дн."
                      if sub.get("on") else "выключена")) + (f" — выдано {len(pers)}, использовано {sum(1 for c in pers if c.get('used'))}" if pers else "") + "\n")
        codes = [c for c in codes if not c.get("personal")]
        show = [c for c in codes if promo_status(c)[0] != "ended"] + [c for c in codes if promo_status(c)[0] == "ended"][-3:]
        lines += [promo_line(c) for c in show] or ["Промокодов пока нет."]
        lines.append("\n🟢 действует  📅 в очереди  📝 черновик  ⚪️ закончился")
        return send(uid, "\n".join(lines), menu=False, buttons=[
            [("➕ Новый промокод", "/промо новый"), ("✅ Проверить код", "/код")],
            [("📅 Автовыкладка", "/промо расписание")] + ([("⏸ Выключить", "/промо расписание стоп")] if sch.get("on") else []),
            [("🎁 Скидка за подписку", "/промо подписка")]])

    if act in ("новый", "создать", "+"):
        p = arg.split()
        if len(p) < 2:
            return send(uid, PROMO_HELP_NEW, menu=False)
        disc = parse_disc(p[1])
        if not disc:
            return send(uid, "Не понял скидку. Напишите, например, 10% или 100₽.\n\n" + PROMO_HELP_NEW, menu=False)
        limit = int(p[2]) if len(p) > 2 and p[2].isdigit() else 0
        days = int(p[3]) if len(p) > 3 and p[3].isdigit() else 7
        res = {}
        def f(d):
            codes = d.setdefault("codes", [])
            code = p[0].upper()
            if code in ("АВТО", "AUTO"):
                code = gen_code({norm_code(c["code"]) for c in codes})
            if not re.fullmatch(r"[A-ZА-ЯЁ0-9\-]{3,20}", code):
                res["err"] = "Код — от 3 до 20 букв и цифр, без пробелов."
                return
            if find_code(d, code):
                res["err"] = f"Промокод {code} уже есть. Придумайте другой."
                return
            c = {"code": code, "kind": disc[0], "value": disc[1], "limit": limit, "days": days, "used": 0,
                 "status": "draft", "created": int(time.time()), "by": uid}
            codes.append(c)
            res["c"] = c
        promo_update(f, "Бот: новый промокод")
        if res.get("err"):
            return send(uid, "❌ " + res["err"])
        c = res["c"]
        return send(uid, f"📝 Создал промокод {c['code']}: скидка {fmt_disc(c)}, "
                         f"{plural_act(c['limit']) if c['limit'] else 'без ограничения активаций'}, действует {c['days']} дн. после публикации.\n\nЧто с ним сделать?",
                    menu=False, buttons=promo_buttons(c))

    if act in ("подписка", "подписчикам"):
        if not arg:
            return send(uid, PROMO_HELP_SUB, menu=False, buttons=[[("⏸ Выключить", "/промо подписка стоп")]])
        if arg.lower() in ("стоп", "выкл", "выключить", "off"):
            promo_update(lambda d: d.setdefault("sub", {}).update(on=False), "Бот: скидка за подписку выключена")
            return send(uid, "⏸ Скидка за подписку выключена. Покупатели увидят, что сейчас её нет.")
        p = arg.split()
        disc = parse_disc(p[0])
        days = int(p[1]) if len(p) > 1 and p[1].isdigit() else 14
        if not disc:
            return send(uid, "Не понял. " + PROMO_HELP_SUB, menu=False)
        promo_update(lambda d: d.setdefault("sub", {}).update(on=True, kind=disc[0], value=disc[1], days=days), "Бот: скидка за подписку")
        return send(uid, f"🎁 Готово: подписчик канала получит у бота личный промокод на {disc[1]}{'%' if disc[0] == '%' else ' ₽'} "
                         f"(одна активация, действует {days} дн.). Один промокод на человека.\n\n"
                         "Когда покупатель назовёт такой код — проверяйте его как обычно: «✅ Проверить код».")

    if act in ("расписание", "автовыкладка", "авто"):
        if not arg:
            return send(uid, PROMO_HELP_SCHED, menu=False, buttons=[[("⏸ Выключить", "/промо расписание стоп")]])
        if arg.lower() in ("стоп", "выкл", "выключить", "off"):
            promo_update(lambda d: d.setdefault("schedule", {}).update(on=False), "Бот: автовыкладка выключена")
            return send(uid, "⏸ Автовыкладка промокодов выключена.")
        p = arg.lower().split()
        try:
            wd = WEEKDAYS.index(p[0][:2])
            hh, mm = map(int, p[1].replace(".", ":").split(":"))
            assert 0 <= hh < 24 and 0 <= mm < 60
            disc = parse_disc(p[2]); assert disc
            limit = int(p[3]) if len(p) > 3 else 3
            days = int(p[4]) if len(p) > 4 else 7
        except Exception:
            return send(uid, "Не понял. " + PROMO_HELP_SCHED, menu=False)
        sch = {"on": True, "wd": wd, "hm": f"{hh:02d}:{mm:02d}", "kind": disc[0], "value": disc[1], "limit": limit, "days": days}
        promo_update(lambda d: d.setdefault("schedule", {}).update(sch), "Бот: автовыкладка промокодов")
        return send(uid, f"📅 Готово: {EVERY[wd]} в {sch['hm']} (по Уфе) в канале будет новый промокод — "
                         f"скидка {disc[1]}{'%' if disc[0] == '%' else ' ₽'}, {limit} шт., действует {days} дн.\n\n"
                         "Свои коды для следующих недель: «➕ Новый промокод» → «📅 В очередь». "
                         "Публикация может задержаться на 5–15 минут.")

    if act in ("опубликовать", "очередь", "черновик", "тихо", "удалить", "стоп", "списать", "вернуть"):
        if not arg:
            return send(uid, "Напишите промокод после команды.")
        res = {}
        def f(d):
            c = find_code(d, arg)
            if not c:
                res["err"] = f"Промокода {arg.upper()} нет."
                return
            st, why = promo_status(c)
            res["c"] = c
            if act in ("опубликовать", "тихо"):
                if st == "active":
                    res["err"] = f"{c['code']} уже действует."
                    return
                promo_publish(c)
            elif act == "очередь":
                c["status"] = "queued"; c["queued"] = int(time.time())
            elif act == "черновик":
                c["status"] = "draft"
            elif act == "удалить":
                if st == "active" and c.get("used"):
                    res["err"] = "Этим кодом уже пользовались — его можно только остановить."
                    return
                d["codes"] = [x for x in d["codes"] if x is not c]
            elif act == "стоп":
                c["status"] = "ended"; c["ended"] = int(time.time())
                promo_end_post(c, "остановлен")
            elif act == "списать":
                if st != "active":
                    res["err"] = f"Код {c['code']} не действует" + (f": {why}." if why else ".")
                    return
                c["used"] = c.get("used", 0) + 1
                c.setdefault("uses", []).append({"ts": int(time.time()), "by": uid})
                if c.get("limit") and c["used"] >= c["limit"]:
                    c["status"] = "ended"; c["ended"] = int(time.time())
                    promo_end_post(c, "все активации использованы")
                else:
                    res["refresh"] = True
            elif act == "вернуть":
                c["used"] = max(0, c.get("used", 0) - 1)
                if c.get("uses"):
                    c["uses"].pop()
                if c["status"] == "ended" and (not c.get("until") or time.time() < c["until"]):
                    c["status"] = "active"
        try:
            promo_update(f, f"Бот: промокод {act}")
        except Exception as e:
            return send(uid, f"❌ Не получилось: {e}")
        if res.get("err"):
            return send(uid, "❌ " + res["err"])
        c = res["c"]
        c.setdefault("code", norm_code(arg))            # личный код: в файле только хэш
        if act == "опубликовать":
            try:
                promo_post(c["code"])
            except Exception as e:
                return send(uid, f"⚠️ Промокод {c['code']} включён, но пост в канал не вышел: {e}\n"
                                 "Опубликуйте его в канале вручную.")
        if res.get("refresh") and c.get("mid"):           # в посте — сколько активаций осталось
            try:
                post_with_kb("PUT", {"message_id": c["mid"]}, promo_post_text(c), c["code"])
            except Exception as e:
                print("promo refresh:", e)
        msg = {"опубликовать": f"📣 Опубликовал промокод {c['code']} в канале.",
               "тихо": f"🤫 Промокод {c['code']} включён без поста — можно выдавать лично.",
               "очередь": f"📅 {c['code']} в очереди — выйдет в канале при следующей автовыкладке.",
               "черновик": f"↩️ {c['code']} убран из очереди.",
               "удалить": f"🗑 Промокод {c['code']} удалён.",
               "стоп": f"⛔️ Промокод {c['code']} остановлен.",
               "списать": f"✅ Промокод {c['code']} активирован (заказ выполнен): активаций {c.get('used')} из {c.get('limit') or '∞'}."
                          + (" Это была последняя активация — промокод закрыт" + (", в посте канала это отмечено." if c.get("mid") else ".")
                             if c["status"] == "ended" else ""),
               "вернуть": f"↩️ Отменил активацию {c['code']}: активаций {c.get('used')}."}[act]
        btns = [] if act == "удалить" else promo_buttons(c)
        if act == "списать":
            btns = [[("↩️ Ошибся — отменить активацию", f"/промо вернуть {c['code']}")]]
        return send(uid, msg, buttons=btns)

    return send(uid, PROMO_HELP_NEW)


ASK.update({"/промо новый": PROMO_HELP_NEW, "/промо расписание": PROMO_HELP_SCHED, "/промо подписка": PROMO_HELP_SUB,
            "/код": "🎟 Напишите промокод из заказа — проверю и активирую, когда заказ выполнен."})


def check_code(uid, text):
    code = re.sub(r"^/?(код|проверить)\s*", "", text.strip(), flags=re.I).strip()
    if not code:
        return send(uid, "Напишите промокод покупателя.")
    d, _ = gh_file("promos.json")
    c = find_code(d or {}, code)
    if not c:
        return send(uid, f"❌ Промокода {code.upper()} нет — покупатель ошибся или придумал.")
    st, why = promo_status(c)
    if st == "active":
        return send(uid, f"✅ {c['code']} действует: скидка {fmt_disc(c)}.\n" + promo_line(c)
                    + "\n\nДелайте скидку на заказ. Когда заказ выполнен и продан — нажмите «Активировать».",
                    buttons=[[("✅ Заказ выполнен — активировать", f"/промо списать {c['code']}")]])
    names = {"draft": "ещё не опубликован (черновик)", "queued": "ещё не опубликован (в очереди)", "ended": "больше не действует"}
    return send(uid, f"⛔️ {c['code']} {names.get(st, st)}" + (f": {why}" if why else "") + ".\n" + promo_line(c),
                buttons=promo_buttons(c))


# ---------------------------------------------------------------- покупатели: своё меню, скидка за подписку

def customer_keyboard(extra=None):
    rows = [[{"type": "callback", "text": lbl, "payload": cmd} for lbl, cmd in row] for row in (extra or [])]
    rows += [[{"type": "callback", "text": "🎁 Получить скидку", "payload": "/скидка"}],
             [{"type": "link", "text": "🛍 Каталог с ценами", "url": SITE_URL + "?utm_source=bot"}],
             [{"type": "link", "text": "📣 Канал", "url": CHANNEL_URL},
              {"type": "link", "text": "✍️ Написать мастеру", "url": MASTER_URL}]]
    return {"type": "inline_keyboard", "payload": {"buttons": rows}}


def send_customer(uid, text, links=None, extra=None):
    """Сообщение покупателю — только с его меню (админское меню покупатель не увидит никогда)."""
    kb = customer_keyboard(extra)
    if links:
        kb["payload"]["buttons"] = [[{"type": "link", "text": l, "url": u}] for l, u in links] + kb["payload"]["buttons"]
    max_api("POST", "/messages", {"user_id": uid, "disable_link_preview": "true"}, {"text": text[:3900], "attachments": [kb]})


CUSTOMER_HELLO = ("Здравствуйте! 👋 Это бот мастерской «3D модели в Нагаево».\n\n"
                  "🎁 Нажмите «Получить скидку» — покажу действующие промокоды.\n"
                  "🛍 Все модели с ценами — в каталоге на сайте.\n"
                  "✍️ Заказ, вопросы, своя модель — пишите мастеру.")


def is_subscribed(uid):
    ch = config()["channel"]
    r = max_api("GET", f"/chats/{ch}/members", {"user_ids": str(uid)})
    return any((m.get("user_id") == uid) for m in (r.get("members") or []))


def uid_hash(uid):
    import hashlib, hmac
    return hmac.new((SECRET or "salt").encode(), f"u{uid}".encode(), hashlib.sha256).hexdigest()[:24]


_sub_tries = {}
_acks = {}


def _recent_ack(uid):
    """Не повторять «Передал мастеру» на каждое сообщение подряд."""
    now = time.time()
    seen = now - _acks.get(uid, 0) < 600
    _acks[uid] = now
    return seen


_promo_ip = {}


def site_promo_check(event, headers):
    """Сайт спрашивает: действует ли промокод? Ничего не списывает."""
    cors = _cors(headers.get("origin", ""))
    if (event.get("httpMethod") or "").upper() == "OPTIONS":
        return {"statusCode": 204, "headers": cors, "body": ""}
    def out(**kw):
        return {"statusCode": 200, "headers": {**cors, "Content-Type": "application/json"}, "body": json.dumps(kw, ensure_ascii=False)}
    ip = ((event.get("requestContext") or {}).get("identity") or {}).get("sourceIp") or headers.get("x-forwarded-for", "?")
    now = time.time()
    hits = [x for x in _promo_ip.get(ip, []) if now - x < 3600]
    if len(hits) >= 30:                                   # защита от перебора кодов
        return out(valid=False, text="Слишком много проверок. Мастер проверит промокод при заказе.")
    _promo_ip[ip] = hits + [now]
    try:
        raw = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode()
        code = str(json.loads(raw).get("code") or "")[:30]
    except Exception:
        code = ""
    if len(norm_code(code)) < 3:
        return out(valid=False, text="❌ Такого промокода нет")
    d, _ = gh_file("promos.json")
    c = find_code(d or {}, code)
    if not c:
        return out(valid=False, text="❌ Такого промокода нет — проверьте, нет ли опечатки")
    st, why = promo_status(c, now)
    if st == "active":
        left = (c["limit"] - c.get("used", 0)) if c.get("limit") else None
        until = time.strftime("%d.%m", time.gmtime(c["until"] + TZ)) if c.get("until") else ""
        txt = f"✅ Промокод действует: скидка {fmt_disc(c)}"
        if left is not None and not c.get("personal"):
            txt += f", осталось {plural_act(left)}"
        if until:
            txt += f", до {until}"
        return out(valid=True, text=txt + ". Он уже добавлен в текст заказа.")
    if st == "ended":
        return out(valid=False, text="❌ Промокод больше не действует" + (f": {why}" if why else ""))
    return out(valid=False, text="❌ Такого промокода нет — проверьте, нет ли опечатки")


def active_public_promos():
    d, _ = gh_file("promos.json")
    return [c for c in (d or {}).get("codes", []) if not c.get("personal") and c.get("mid") and promo_status(c)[0] == "active"]


def give_sub_discount(uid, name):
    d, _ = gh_file("promos.json")
    sub = (d or {}).get("sub") or {}
    if not sub.get("on"):
        act = active_public_promos()
        if act:
            lines = []
            for c in act:
                left = (c["limit"] - c.get("used", 0)) if c.get("limit") else None
                until = time.strftime("%d.%m", time.gmtime(c["until"] + TZ)) if c.get("until") else ""
                lines.append(f"🎁 {c['code']} — скидка {fmt_disc(c)}"
                             + (f", осталось {plural_act(left)}" if left is not None else "") + (f", до {until}" if until else ""))
            for c in act[:3]:
                try:
                    post_with_kb("POST", {"user_id": uid}, f"🎁 {c['code']}", c["code"])
                except Exception as e:
                    print("promo to customer:", e)
            return send_customer(uid, "Сейчас действуют промокоды:\n\n" + "\n".join(lines) +
                                 "\n\n👉 Чтобы получить скидку, выберите модель и напишите мастеру заказ, добавив в сообщение промокод. "
                                 "Количество активаций ограничено: промокод засчитывается, когда заказ выполнен — успейте!")
        return send_customer(uid, "Сейчас свободных промокодов нет 🙁\nНовые выходят в нашем канале — подпишитесь, чтобы не пропустить!",
                             links=[("📣 Подписаться на канал", CHANNEL_URL)])
    uh = uid_hash(uid)
    old = next((c for c in (d or {}).get("codes", []) if c.get("personal") and c.get("u") == uh), None)
    if old:
        when = time.strftime("%d.%m.%Y", time.gmtime(old.get("start", 0) + TZ))
        st, _ = promo_status(old)
        return send_customer(uid, f"Вы уже получили личный промокод {when}"
                             + (" и уже воспользовались им." if old.get("used") else
                                ("." if st == "active" else ", но срок его действия закончился."))
                             + "\nОдин промокод за подписку — на одного человека. Следите за промокодами в канале 😉")
    if time.time() - _sub_tries.get(uid, 0) < 5:
        return
    _sub_tries[uid] = time.time()
    try:
        ok = is_subscribed(uid)
    except Exception as e:
        print("member check:", e)
        return send_customer(uid, "Не получилось проверить подписку — попробуйте через пару минут.")
    if not ok:
        return send_customer(uid, "Чтобы получить скидку, подпишитесь на наш канал 👇\nПотом вернитесь сюда и нажмите «✅ Я подписался».",
                             links=[("📣 Подписаться на канал", CHANNEL_URL)], extra=[[("✅ Я подписался", "/скидка")]])
    res = {}
    def f(dd):
        codes = dd.setdefault("codes", [])
        if any(c.get("personal") and c.get("u") == uh for c in codes):
            return
        have = {norm_code(c["code"]) for c in codes if c.get("code")}
        hashes = {c.get("h") for c in codes}
        while True:
            code = gen_code(have)
            if code_hash(code) not in hashes:
                break
        now = int(time.time())
        res["code"] = code
        res["until"] = now + sub["days"] * 86400
        codes.append({"personal": True, "h": code_hash(code), "u": uh, "kind": sub["kind"], "value": sub["value"],
                      "limit": 1, "used": 0, "days": sub["days"], "status": "active", "start": now, "until": res["until"]})
    promo_update(f, "Бот: промокод за подписку")
    if not res.get("code"):
        return send_customer(uid, "Вы уже получили личный промокод 🙂")
    disc = f"{sub['value']}%" if sub["kind"] == "%" else f"{sub['value']} ₽"
    until = time.strftime("%d.%m", time.gmtime(res["until"] + TZ))
    try:
        post_with_kb("POST", {"user_id": uid}, f"🎁 Ваш личный промокод: {res['code']}", res["code"])
    except Exception as e:
        print("personal code msg:", e)
    send_customer(uid, f"🎉 Спасибо за подписку! Ваш личный промокод:\n\n{res['code']}\n\n"
                       f"Скидка {disc} на один заказ, действует до {until} включительно.\n\n"
                       "👉 Как получить скидку: выберите модель в каталоге и напишите мастеру заказ, "
                       "добавив в сообщение этот промокод (на сайте его можно вписать в поле — добавится в текст заказа сам).",
                  links=[("✍️ Написать заказ мастеру", MASTER_URL)])
    notify_owners(f"🎁 {name or 'Покупатель'} подписался на канал и получил личный промокод на {disc} (до {until}).", kind="promo")


def handle_customer(uid, text, name=""):
    low = (text or "").strip().lower()
    if low in ("/скидка", "скидка", "/skidka", "получить скидку", "🎁 получить скидку", "/discount"):
        return give_sub_discount(uid, name)
    if low in ("/написать", "написать"):
        return send_customer(uid, "✍️ Напишите сюда ваш вопрос или заказ (можно с фото) — я сразу передам мастеру, "
                                  "он ответит в этом чате.")
    return send_customer(uid, CUSTOMER_HELLO)


# ---------------------------------------------------------------- заказы через бота

def parse_start(payload):
    """«o48-<промокод base64url>» или «f12_15_20-…» → (номера, промокод)."""
    m = re.fullmatch(r"([of])([\d_]+)(?:-([A-Za-z0-9_\-]+))?", payload or "")
    if not m:
        return None, ""
    nos = [int(x) for x in m.group(2).split("_") if x][:30]
    promo = ""
    if m.group(3):
        try:
            b = m.group(3) + "=" * (-len(m.group(3)) % 4)
            promo = base64.urlsafe_b64decode(b).decode("utf-8")[:30]
        except Exception:
            promo = ""
    return nos, promo


def customer_title(user):
    name = " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x) or user.get("name") or "Покупатель"
    link = f"https://max.ru/{user['username']}" if user.get("username") else ""
    return name, link


def promo_info(code):
    if not code:
        return None, ""
    d, _ = gh_file("promos.json")
    c = find_code(d or {}, code)
    if not c:
        return None, f"{code.upper()} — такого промокода нет"
    st, why = promo_status(c)
    if st == "active":
        return c, f"{c['code']} — ✅ действует, скидка {fmt_disc(c)}"
    return None, f"{c['code']} — ❌ не действует" + (f" ({why})" if why else "")


_prefs = {}


def wants(uid, kind):
    """Настройки уведомлений: settings.json → notify → id → вид (false — не присылать)."""
    if time.time() - _prefs.get("ts", 0) > 120:
        try:
            st, _ = gh_file("settings.json")
            _prefs.update(ts=time.time(), notify=(st or {}).get("notify") or {})
        except Exception:
            _prefs.update(ts=time.time(), notify={})
    return (_prefs.get("notify") or {}).get(str(uid), {}).get(kind, True) is not False


def notify_owners(text, buttons=None, kind="other"):
    for o in config()["owners"]:
        if not wants(o, kind):
            continue
        try:
            send(o, text, menu=False, buttons=buttons)
        except Exception as e:
            print("notify owner:", e)


def new_order(uid, user, nos, promo):
    items = catalog_items()
    found = [_find(items, n) for n in nos]
    found = [i for i in found if i]
    if not found:
        return send_customer(uid, "Не нашёл эту модель в каталоге 🤔 Напишите сюда, что хотите заказать — передам мастеру.")
    name, link = customer_title(user)
    pc, ptxt = promo_info(promo)
    lines = "\n".join(f"• №{i['no']} {i['title']} — {fmt_price(i.get('price'))}" for i in found)
    send_customer(uid, "✅ Заказ принят и передан мастеру!\n\n" + lines
                  + (f"\n\n🎟 Промокод: {ptxt}" if ptxt else "")
                  + "\n\nНапишите сюда размер и цвет (или «как на фото») и любые пожелания — "
                    "мастер ответит здесь же, в этом чате.")
    btns = [[("💬 Ответить покупателю", f"/ответ {uid}")]]
    if len(found) == 1:
        btns.append([(f"✅ Продано №{found[0]['no']}", f"/продано {found[0]['no']}")])
    if pc:
        btns.append([(f"🎟 Активировать {pc['code']} (после продажи)", f"/промо списать {pc['code']}")])
    notify_owners("🛒 Новый заказ с сайта\n\n" + lines
                  + (f"\n\n🎟 Промокод: {ptxt}" if ptxt else "")
                  + f"\n\n👤 {name}"
                  + "\n\nОтвечайте кнопкой «💬 Ответить» — сообщение уйдёт от бота, ваш аккаунт писать не нужно.", btns, "orders")


def forward_to_owners(uid, user, msg):
    """Сообщение покупателя → админам (с кнопкой «Ответить»)."""
    name, link = customer_title(user)
    body = msg.get("body") or {}
    text = (body.get("text") or "").strip()
    has_media = bool(body.get("attachments"))
    notify_owners(f"💬 {name} пишет боту:" + (f"\n\n«{text[:1500]}»" if text else ""),
                  [[("💬 Ответить", f"/ответ {uid}")]], "orders")
    if has_media and body.get("mid"):
        for o in [x for x in config()["owners"] if wants(x, "orders")]:
            try:
                max_api("POST", "/messages", {"user_id": o}, {"text": "", "link": {"type": "forward", "mid": body["mid"]}})
            except Exception as e:
                print("forward media:", e)


def owner_reply(uid, text):
    m = re.match(r"^/?ответ\s+(\d+)\s*(.*)$", text, re.S | re.I)
    if not m:
        return send(uid, "Нажмите «💬 Ответить» под сообщением покупателя.")
    cid, body = int(m.group(1)), m.group(2).strip()
    if not body:
        _await[uid] = (f"/ответ {cid}", time.time())
        return send(uid, "💬 Напишите ответ покупателю — я перешлю его.", menu=False, buttons=CANCEL)
    try:
        send_customer(cid, "💬 Мастер:\n\n" + body)
    except Exception as e:
        return send(uid, f"❌ Не получилось отправить: {e}")
    return send(uid, "✅ Отправил покупателю. Его ответ придёт сюда.", menu=False,
                buttons=[[("💬 Написать ещё", f"/ответ {cid}")]])


# ---------------------------------------------------------------- приложение управления (мини-приложение MAX)

def webapp_user(init_data):
    """Проверка подписи initData мини-приложения MAX → id пользователя или None."""
    import hashlib, hmac
    try:
        pairs = urllib.parse.parse_qsl(init_data or "", keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return None
    keys = [k for k, _ in pairs]
    if keys.count("hash") != 1 or len(keys) != len(set(keys)):
        return None
    got = dict(pairs).pop("hash")
    rest = sorted((k, v) for k, v in pairs if k != "hash")
    check = "\n".join(f"{k}={v}" for k, v in rest)
    secret = hmac.new(b"WebAppData", MAX_TOKEN.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(hmac.new(secret, check.encode(), hashlib.sha256).hexdigest(), got):
        return None
    d = dict(rest)
    try:
        if time.time() - int(d.get("auth_date", "0")) / (1000 if int(d.get("auth_date", "0")) > 10**11 else 1) > 86400:
            return None
        return int(json.loads(d.get("user") or "{}").get("id"))
    except Exception:
        return None


# ---------------------------------------------------------------- терминал (только владелец и его аккаунты)

TERM_PROTECT = {"index.html", "app.html", "app.js", "config.js", "CNAME", "catalog.json", "access.json", "state.json",
                "yandex/bot.py", "yandex/index.py", "scripts/sync_max.py", "scripts/maxapi.py", "channel_id.txt"}


def _term_path(p):
    p = str(p or "").strip().strip("/").replace("\\", "/")
    if ".." in p.split("/") or p.startswith(".git/"):
        raise ValueError("Недопустимый путь")
    return p


def app_term(uid, req):
    op = req.get("op")
    path = _term_path(req.get("path"))
    q = urllib.parse.quote(path)
    if op == "ls":
        d = gh("GET", f"/repos/{REPO}/contents/{q}?ref=main")
        if isinstance(d, dict):
            return {"files": [{"name": d["name"], "type": "file", "size": d.get("size")}]}
        return {"files": sorted([{"name": x["name"], "type": x["type"], "size": x.get("size")} for x in d],
                                key=lambda x: (x["type"] != "dir", x["name"].lower()))}
    if op == "find":
        word = str(req.get("q") or "").lower()
        t = gh("GET", f"/repos/{REPO}/git/trees/main?recursive=1")
        return {"paths": [x["path"] for x in t.get("tree", []) if x["type"] == "blob" and word in x["path"].lower()][:200]}
    if op == "cat":
        d = gh("GET", f"/repos/{REPO}/contents/{q}?ref=main")
        if isinstance(d, list):
            raise ValueError("Это папка — используйте ls")
        raw = base64.b64decode(d["content"]) if d.get("content") else urllib.request.urlopen(
            urllib.request.Request(d["download_url"], headers={"User-Agent": UA}), timeout=25).read()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return {"binary": True, "size": d.get("size"), "url": d.get("download_url"), "sha": d["sha"]}
        return {"text": text, "sha": d["sha"], "size": d.get("size")}
    if op in ("write", "rm"):
        if not path:
            raise ValueError("Укажите файл")
        if op == "rm":
            if path in TERM_PROTECT:
                raise ValueError("Этот файл нужен для работы сайта или бота — удалять нельзя. Можно отредактировать.")
            d = gh("GET", f"/repos/{REPO}/contents/{q}?ref=main")
            gh("DELETE", f"/repos/{REPO}/contents/{q}", {"message": f"Терминал: удалён {path}", "sha": d["sha"], "branch": "main",
                                                         "committer": {"name": "catalog-bot", "email": "catalog-bot@users.noreply.github.com"}})
            log_action(uid, f"[терминал] удалил {path}")
            return {"msg": f"🗑 {path} удалён. Вернуть: history {path} → restore"}
        text = str(req.get("text") if req.get("text") is not None else "")
        if path.endswith(".json"):
            try:
                json.loads(text)
            except Exception as e:
                raise ValueError(f"Ошибка в JSON, файл НЕ сохранён: {e}")
        if path.endswith(".py"):
            try:
                compile(text, path, "exec")
            except SyntaxError as e:
                raise ValueError(f"Ошибка в Python (строка {e.lineno}: {e.msg}), файл НЕ сохранён — иначе бот перестанет работать.")
        body = {"message": f"Терминал: {path}", "branch": "main", "content": base64.b64encode(text.encode()).decode(),
                "committer": {"name": "catalog-bot", "email": "catalog-bot@users.noreply.github.com"}}
        if req.get("sha"):
            body["sha"] = req["sha"]
        try:
            r = gh("PUT", f"/repos/{REPO}/contents/{q}", body)
        except urllib.error.HTTPError as e:
            if e.code in (409, 422):
                raise ValueError("Файл успели изменить (бот или синхронизация). Откройте его заново и повторите правку.")
            if e.code in (403, 404) and path.startswith(".github/"):
                raise ValueError("GitHub не даёт менять файлы .github/ этим ключом — правьте их на github.com.")
            raise
        log_action(uid, f"[терминал] изменил {path}")
        return {"msg": f"💾 {path} сохранён. Сайт обновится через 1–3 минуты" + (", бот — до 5 минут." if path.startswith("yandex/") else "."),
                "sha": (r.get("content") or {}).get("sha")}
    if op == "history":
        cs = gh("GET", f"/repos/{REPO}/commits?per_page=20" + (f"&path={q}" if path else ""))
        return {"commits": [{"sha": c["sha"][:10], "msg": c["commit"]["message"].split("\n")[0][:90],
                             "date": c["commit"]["committer"]["date"]} for c in cs]}
    if op == "restore":
        ref = re.sub(r"[^0-9a-f]", "", str(req.get("ref") or "").lower())
        if not path or len(ref) < 7:
            raise ValueError("Нужно: restore <файл> <версия>")
        old = gh("GET", f"/repos/{REPO}/contents/{q}?ref={ref}")
        try:
            cur = gh("GET", f"/repos/{REPO}/contents/{q}?ref=main")["sha"]
        except urllib.error.HTTPError:
            cur = None
        content = old.get("content") or base64.b64encode(urllib.request.urlopen(
            urllib.request.Request(old["download_url"], headers={"User-Agent": UA}), timeout=25).read()).decode()
        body = {"message": f"Терминал: {path} возвращён к версии {ref[:7]}", "branch": "main", "content": content.replace("\n", ""),
                "committer": {"name": "catalog-bot", "email": "catalog-bot@users.noreply.github.com"}}
        if cur:
            body["sha"] = cur
        gh("PUT", f"/repos/{REPO}/contents/{q}", body)
        log_action(uid, f"[терминал] вернул {path} к {ref[:7]}")
        return {"msg": f"↩️ {path} возвращён к версии {ref[:7]}."}
    if op == "runs":
        r = gh("GET", f"/repos/{REPO}/actions/runs?per_page=10")
        return {"runs": [{"name": x["name"], "title": (x.get("display_title") or "")[:70], "status": x["status"],
                          "result": x.get("conclusion"), "date": x["created_at"], "url": x["html_url"]} for x in r.get("workflow_runs", [])]}
    if op == "sync":
        run_sync()
        return {"msg": "🔄 Обновление сайта запущено (1–3 минуты)."}
    raise ValueError("Неизвестная команда терминала")


def app_dash(uid):
    from concurrent.futures import ThreadPoolExecutor
    names = ["sales.json", "reviews.json", "promos.json", "overrides.json", "links.json", "catalog.json"]
    with ThreadPoolExecutor(8) as ex:                      # читаем файлы параллельно — быстрее
        fut_cfg = ex.submit(config)
        res = dict(zip(names, ex.map(lambda n: gh_file(n)[0], names)))
        cfg = fut_cfg.result()
    sd, rv, pr, ov, ln, cat = (res[n] for n in names)
    items = (cat or {}).get("items", [])
    codes = []
    pers = []
    for c in (pr or {}).get("codes", []):
        st, why = promo_status(c)
        if c.get("personal"):
            pers.append({"status": st, "used": c.get("used", 0), "value": c.get("value"), "kind": c.get("kind"),
                         "start": c.get("start"), "until": c.get("until")})
            continue
        codes.append({k: c.get(k) for k in ("code", "kind", "value", "limit", "used", "days", "until", "start", "mid")}
                     | {"status": st, "why": why})
    admins = [{"k": k, "name": a["name"], "id": a["id"],
               "role": "owner" if a["id"] in cfg.get("real", ()) else
                       "full" if a["id"] in cfg.get("full", ()) else
                       "on" if a["id"] in cfg["owners"] else "off", "guest": bool(a.get("guest"))}
              for k, a in enumerate(cfg.get("admins", []), 1)]
    hidden = set((ov or {}).get("hidden", []))
    with ThreadPoolExecutor(6) as ex:
        more = dict(zip(["settings.json", "finance.json", "stock.json", "outbox.json", "log.json"],
                        ex.map(lambda n: gh_file(n)[0], ["settings.json", "finance.json", "stock.json", "outbox.json", "log.json"])))
    st = more["settings.json"]
    return {
        "expenses": (more["finance.json"] or {}).get("expenses", []),
        "spools": (more["stock.json"] or {}).get("spools", []),
        "scheduled": [p for p in (more["outbox.json"] or {}).get("posts", []) if not p.get("done") and p.get("at")],
        "log": ((more["log.json"] or {}).get("log") or [])[-200:] if uid in cfg["chief"] else [],
        "notice": (st or {}).get("notice") or {}, "collections": (st or {}).get("collections") or [],
        "texts": (st or {}).get("texts") or {}, "myNotify": ((st or {}).get("notify") or {}).get(str(uid), {}),
        "me": uid, "chief": uid in cfg["chief"], "site": SITE_URL, "real": uid in cfg.get("real", ()),
        "perms": sorted((cfg.get("perms") or {}).get(uid, set())), "permNames": PERMS,
        "adminPerms": {str(k): sorted(v) for k, v in (cfg.get("perms") or {}).items()},
        "maint": (st or {}).get("maint") or {}, "template": POST_TEMPLATE if "POST_TEMPLATE" in globals() else "",
        "items": [{k: i.get(k) for k in ("no", "title", "price", "category", "views", "views_week", "views_month",
                                         "sold", "photos", "photos_orig", "url", "ts", "hidden", "desc", "badge", "pin", "size", "color")}
                  | {"hidden": i.get("no") in hidden} for i in items],
        "hiddenNos": sorted(hidden),
        "overrides": ov or {},
        "sales": (sd or {}).get("sales", []),
        "reviews": (cat or {}).get("reviews", []),
        "reviewsBot": (rv or {}).get("reviews", []),
        "pending": (rv or {}).get("pending", []),
        "promos": codes, "personal": pers,
        "schedule": (pr or {}).get("schedule") or {}, "sub": (pr or {}).get("sub") or {},
        "admins": admins,
        "links": (ln or {}).get("links", [])[-300:] if isinstance(ln, dict) else [],
    }


def max_upload_image(blob):
    """Загрузить картинку в MAX → payload для вложения."""
    import uuid
    info = max_api("POST", "/uploads", {"type": "image"})
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"data\"; filename=\"photo.jpg\"\r\n"
            f"Content-Type: image/jpeg\r\n\r\n").encode() + blob + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(info["url"], data=body, method="POST", headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}", "Authorization": MAX_TOKEN, "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60, context=ctx()) as r:
        res = json.loads(r.read().decode("utf-8", "replace") or "{}")
    if res.get("token"):
        return {"token": res["token"]}
    if res.get("photos"):
        return {"photos": res["photos"]}
    return res


def _b64_images(lst, limit=6):
    out = []
    for x in (lst or [])[:limit]:
        try:
            b = base64.b64decode(str(x).split(",", 1)[-1])
            if 1000 < len(b) < 3_000_000:
                out.append(b)
        except Exception:
            pass
    return out


def app_review_add(uid, req):
    text = str(req.get("text") or "").strip()[:800]
    if len(text) < 2:
        return "Напишите текст отзыва."
    name = str(req.get("name") or "").strip()[:40]
    try:
        no = int(req.get("no")) if str(req.get("no") or "").strip() else None
        stars = max(0, min(5, int(req.get("stars") or 0)))
    except Exception:
        no, stars = None, 0
    data, _ = gh_file("reviews.json")
    data = data or {}
    rid = max([r.get("id", 0) for r in data.get("reviews", []) + data.get("pending", [])] + [0]) + 1
    raw = []
    for k, blob in enumerate(_b64_images(req.get("photos"), 4)):
        path = f"reviews/raw/{rid}_{k}.jpg"
        try:
            gh_put_bytes(path, blob, f"Приложение: фото отзыва {rid}")
            raw.append(path)
        except Exception as e:
            print("app review photo:", e)
    entry = {"id": rid, "ts": int(time.time() * 1000), "text": text, "raw": raw, "no": no}
    if name:
        entry["name"] = name
    if stars:
        entry["stars"] = stars
    update_json("reviews.json", lambda d: d.setdefault("reviews", []).append(entry), f"Приложение: отзыв {rid}", {"reviews": []})
    try:
        run_sync()
    except Exception:
        pass
    return f"💬 Отзыв №{rid} добавлен" + (f" с фото ({len(raw)})" if raw else "") + ". На сайте появится через 1–3 минуты."


def app_post_new(uid, req):
    text = str(req.get("text") or "").strip()[:3900]
    blobs = _b64_images(req.get("photos"), 10)
    if not text and not blobs:
        return "Пустой пост — добавьте текст или фото."
    atts = []
    for b in blobs:
        try:
            atts.append({"type": "image", "payload": max_upload_image(b)})
        except Exception as e:
            print("app post photo:", e)
    last = None
    for attempt in range(5):
        try:
            max_api("POST", "/messages", {"chat_id": config()["channel"]}, {"text": text, **({"attachments": atts} if atts else {})})
            last = None
            break
        except Exception as e:
            last = e
            if "not.ready" in str(e):
                time.sleep(2)
                continue
            break
    if last:
        return f"❌ Пост не вышел: {last}"
    lost = len(blobs) - len(atts)
    return "📣 Пост опубликован в канале" + (f" ({len(atts)} фото)" if atts else "") + ". На сайте появится через 1–3 минуты." + \
        (f" ⚠️ {lost} фото не загрузились." if lost else "")


def site_app(event, headers):
    cors = _cors(headers.get("origin", ""))
    if (event.get("httpMethod") or "").upper() == "OPTIONS":
        return {"statusCode": 204, "headers": cors, "body": ""}
    def out(code, **kw):
        return {"statusCode": code, "headers": {**cors, "Content-Type": "application/json"},
                "body": json.dumps(kw, ensure_ascii=False)}
    try:
        raw = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode()
        req = json.loads(raw)
    except Exception:
        return out(400, error="bad request")
    uid = webapp_user(req.get("initData"))
    if not uid:
        return out(401, error="Откройте приложение из бота в MAX")
    if uid not in config()["owners"]:
        return out(403, error="Нет доступа к управлению")
    act = req.get("action")
    try:
        if act == "dash":
            return out(200, ok=True, data=app_dash(uid))
        cfg = config()
        if act == "metrika":
            return out(200, ok=True, text=metrika_text(int(req.get("days") or 7)))
        PERM_OF = {"card": "items", "bulk": "items", "fin_add": "sales", "fin_del": "sales", "export": "sales",
                   "stock_add": "items", "stock_use": "items", "stock_set": "items", "stock_del": "items",
                   "post_schedule": "posts", "post_unschedule": "posts"}
        if act in PERM_OF:
            if not has_perm(uid, PERM_OF[act], cfg):
                return out(403, error="Нет прав на этот раздел")
            fn = {"card": app_card, "bulk": app_bulk, "fin_add": app_fin, "fin_del": app_fin, "stock_add": app_stock,
                  "stock_use": app_stock, "stock_set": app_stock, "stock_del": app_stock,
                  "post_schedule": app_schedule, "post_unschedule": app_schedule}.get(act)
            msg = app_export(uid) if act == "export" else fn(uid, req)
            log_action(uid, f"[приложение] {act} " + json.dumps({k: v for k, v in req.items() if k not in ("initData", "photos", "action")}, ensure_ascii=False)[:200])
            return out(200, ok=True, texts=[msg])
        if act == "term":
            if uid not in cfg.get("real", ()):
                return out(403, error="Терминал доступен только владельцу")
            try:
                return out(200, ok=True, data=app_term(uid, req))
            except ValueError as e:
                return out(400, error=str(e))
            except urllib.error.HTTPError as e:
                return out(400, error="GitHub: " + ("не найдено" if e.code == 404 else f"ошибка {e.code}"))
        if act == "invite":
            try:
                link, perms = make_invite(uid, req.get("perms"))
            except ValueError as e:
                return out(400, error=str(e))
            log_action(uid, "[приложение] создал приглашение")
            return out(200, ok=True, link=link, texts=["✅ Ссылка-приглашение создана"])
        if act == "set":
            msg = app_set(uid, req, cfg)
            if req.get("key") != "notify":
                log_action(uid, f"[приложение] настройка {req.get('key')}")
            return out(200, ok=True, texts=[msg])
        if act == "review_add":
            if not has_perm(uid, "reviews", cfg):
                return out(403, error="Нет прав на отзывы")
            return out(200, ok=True, texts=[app_review_add(uid, req)])
        if act == "post_new":
            if not has_perm(uid, "posts", cfg):
                return out(403, error="Нет прав на посты")
            return out(200, ok=True, texts=[app_post_new(uid, req)])
        if act == "metrika_data":
            if not has_perm(uid, "stats", cfg):
                return out(403, error="Нет прав на статистику")
            return out(200, ok=True, data=metrika_data(max(1, min(365, int(req.get("days") or 30))),
                                                       str(req.get("date1") or ""), str(req.get("date2") or ""),
                                                       bool(req.get("all"))))
        if act == "cmd":
            text = str(req.get("text") or "")[:3000]
            if not text.startswith("/") or text.startswith("/обложка") and len(text.split()) < 3:
                return out(400, error="Неверная команда")
            _capture.append({"uid": uid, "texts": []})
            try:
                handle_owner(uid, text, [])
            finally:
                got = _capture.pop()["texts"]
            return out(200, ok=True, texts=got)
    except Exception as e:
        print("app:", repr(e))
        return out(500, error=f"Ошибка: {e}")
    return out(400, error="Неизвестное действие")


# ---------------------------------------------------------------- приложение: финансы, пластик, карточки, сайт, отложенные посты

def _nid(lst):
    return max([x.get("id", 0) for x in lst] + [0]) + 1


def app_card(uid, req):
    no = int(req.get("no"))
    if not _find(catalog_items(), no):
        return "Нет такого товара."
    k = str(no)
    def f(d):
        for fld, key in (("title", "title"), ("desc", "desc"), ("cat", "cat")):
            if fld in req:
                v = str(req.get(fld) or "").strip()[:3000 if fld == "desc" else 120]
                if v:
                    d.setdefault(key, {})[k] = v
                else:
                    (d.get(key) or {}).pop(k, None)
        if "badge" in req:
            b = req.get("badge")
            if b in ("hit", "out"):
                d.setdefault("badge", {})[k] = b
            else:
                (d.get("badge") or {}).pop(k, None)
        if "pin" in req:
            pins = [x for x in d.get("pin", []) if x != no]
            if req.get("pin"):
                pins.insert(0, no)
            d["pin"] = pins
        if "cost" in req:
            try:
                c = float(str(req.get("cost")).replace(",", "."))
                d.setdefault("cost", {})[k] = round(c, 2)
            except Exception:
                (d.get("cost") or {}).pop(k, None)
    update_overrides(f, f"Приложение: карточка №{no}")
    run_sync()
    return f"✅ Карточка №{no} сохранена. На сайте — через 1–3 минуты."


def app_bulk(uid, req):
    op = req.get("op")
    nos = [int(x) for x in (req.get("nos") or []) if str(x).isdigit()][:300]
    items = {i["no"]: i for i in catalog_items()}
    nos = [n for n in nos if n in items]
    if not nos:
        return "Не выбрано ни одного товара."
    val = req.get("value")
    def f(d):
        hid = set(d.get("hidden", []))
        for n in nos:
            if op == "hide":
                hid.add(n)
            elif op == "show":
                hid.discard(n)
            elif op == "pct":
                base = d.get("price", {}).get(str(n), items[n].get("price"))
                if isinstance(base, (int, float)) and base:
                    newp = base * (1 + float(val) / 100)
                    d.setdefault("price", {})[str(n)] = int(round(newp / 10.0) * 10) or int(round(newp))
            elif op == "price":
                d.setdefault("price", {})[str(n)] = int(float(val))
            elif op == "reset":
                (d.get("price") or {}).pop(str(n), None)
            elif op == "badge":
                if val in ("hit", "out"):
                    d.setdefault("badge", {})[str(n)] = val
                else:
                    (d.get("badge") or {}).pop(str(n), None)
        d["hidden"] = sorted(hid)
    update_overrides(f, f"Приложение: массово {op} ({len(nos)})")
    run_sync()
    names = {"hide": "скрыто", "show": "возвращено на сайт", "pct": f"цены изменены на {val}%", "price": f"цена {val} ₽",
             "reset": "цены из постов", "badge": "метка изменена"}
    return f"✅ {len(nos)} товар(ов): {names.get(op, op)}. На сайте — через 1–3 минуты."


def app_fin(uid, req):
    act = req.get("action")
    def f(d):
        ex = d.setdefault("expenses", [])
        if act == "fin_add":
            amt = float(str(req.get("amount") or "0").replace(",", "."))
            if amt <= 0:
                raise ValueError("сумма должна быть больше нуля")
            ex.append({"id": _nid(ex), "ts": int(req.get("ts") or time.time() * 1000), "amount": round(amt, 2),
                       "cat": str(req.get("cat") or "Другое")[:40], "note": str(req.get("note") or "")[:200], "by": uid})
        elif act == "fin_del":
            d["expenses"] = [x for x in ex if x.get("id") != int(req.get("id"))]
    update_json("finance.json", f, f"Приложение: {act}", {"expenses": []})
    return "✅ Расход записан." if act == "fin_add" else "🗑 Расход удалён."


def app_stock(uid, req):
    act = req.get("action")
    res = {}
    def f(d):
        sp = d.setdefault("spools", [])
        if act == "stock_add":
            g = int(float(req.get("grams") or 1000))
            sp.append({"id": _nid(sp), "color": str(req.get("color") or "")[:40], "material": str(req.get("material") or "PLA")[:20],
                       "total": g, "left": g, "price": float(req.get("price") or 0), "low": int(req.get("low") or 150),
                       "ts": int(time.time() * 1000)})
            res["msg"] = "✅ Катушка добавлена."
        else:
            x = next((x for x in sp if x.get("id") == int(req.get("id"))), None)
            if not x:
                raise ValueError("нет такой катушки")
            if act == "stock_use":
                x["left"] = max(0, int(x["left"] - float(req.get("grams") or 0)))
                res["msg"] = f"✅ Осталось {x['left']} г ({x['color']} {x['material']})."
                if x["left"] <= x.get("low", 150):
                    res["low"] = x
            elif act == "stock_set":
                x["left"] = max(0, int(float(req.get("grams") or 0)))
                res["msg"] = f"✅ Остаток: {x['left']} г."
            elif act == "stock_del":
                d["spools"] = [y for y in sp if y is not x]
                res["msg"] = "🗑 Катушка убрана."
    update_json("stock.json", f, f"Приложение: {act}", {"spools": []})
    if res.get("low"):
        x = res["low"]
        notify_owners(f"🧵 Заканчивается пластик: {x['color']} {x['material']} — осталось {x['left']} г.", kind="stock")
    return res.get("msg", "Готово")


def app_schedule(uid, req):
    if req.get("action") == "post_unschedule":
        pid = str(req.get("id"))
        update_json("outbox.json", lambda d: d.update(posts=[p for p in d.get("posts", []) if not (p.get("id") == pid and not p.get("done"))]),
                    "Приложение: отменён отложенный пост", {"posts": []})
        return "🗑 Отложенный пост отменён."
    text = str(req.get("text") or "").strip()[:3900]
    at = int(req.get("at") or 0)
    if not text:
        return "Нужен текст поста."
    if at < time.time() + 60:
        return "Время публикации должно быть в будущем."
    pid = f"p{int(time.time())}"
    paths = []
    for k, blob in enumerate(_b64_images(req.get("photos"), 10)):
        path = f"img/outbox/{pid}_{k}.jpg"
        try:
            gh_put_bytes(path, blob, f"Приложение: фото отложенного поста {pid}")
            paths.append(path)
        except Exception as e:
            print("schedule photo:", e)
    entry = {"id": pid, "title": text.split("\n", 1)[0][:60], "text": text, "images": paths, "at": at, "by": uid}
    update_json("outbox.json", lambda d: d.setdefault("posts", []).append(entry), "Приложение: отложенный пост", {"posts": []})
    when = time.strftime("%d.%m в %H:%M", time.gmtime(at + 5 * 3600))
    return f"🕒 Пост запланирован на {when} (по Уфе)" + (f", фото: {len(paths)}" if paths else "") + ". Может выйти на 5–15 минут позже."


def app_set(uid, req, cfg):
    key = req.get("key")
    val = req.get("value")
    if key == "notify":
        update_json("settings.json", lambda d: d.setdefault("notify", {}).update({str(uid): {k: bool(v) for k, v in (val or {}).items()}}),
                    "Приложение: уведомления", {})
        _prefs.clear()
        return "✅ Настройки уведомлений сохранены."
    if not has_perm(uid, "site", cfg):
        return "🚫 Нет прав на раздел «Сайт»."
    if key == "notice":
        v = {"on": bool(val.get("on")), "text": str(val.get("text") or "")[:300], "until": str(val.get("until") or "")[:10],
             "link": str(val.get("link") or "")[:300]}
        update_json("settings.json", lambda d: d.update(notice=v), "Приложение: объявление на сайте", {})
        msg = "📢 Объявление включено." if v["on"] else "📢 Объявление выключено."
    elif key == "collections":
        cols = []
        for c in (val or [])[:20]:
            cols.append({"id": str(c.get("id") or f"c{len(cols) + 1}")[:20], "title": str(c.get("title") or "Подборка")[:60],
                         "nos": [int(n) for n in (c.get("nos") or []) if str(n).isdigit()][:40], "on": bool(c.get("on", True))})
        update_json("settings.json", lambda d: d.update(collections=cols), "Приложение: подборки", {})
        msg = f"✅ Подборки сохранены ({len(cols)})."
    elif key == "texts":
        t = {str(k)[:40]: str(v)[:4000] for k, v in (val or {}).items() if str(v).strip()}
        update_json("settings.json", lambda d: d.update(texts=t), "Приложение: тексты сайта", {})
        msg = "✅ Тексты сайта сохранены."
    else:
        return "Неизвестная настройка."
    run_sync()
    return msg + " На сайте — через 1–3 минуты."


def app_export(uid):
    import io, csv, uuid
    items = {i["no"]: i for i in catalog_items()}
    sd, _ = gh_file("sales.json")
    fin, _ = gh_file("finance.json")
    ov, _ = gh_file("overrides.json")
    cost = (ov or {}).get("cost") or {}
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Продажи"])
    w.writerow(["Дата", "№", "Модель", "Кол-во", "Цена", "Сумма", "Себестоимость", "Прибыль"])
    for r in sorted((sd or {}).get("sales", []), key=lambda r: r.get("ts", 0)):
        it = items.get(r.get("no")) or {}
        q = int(r.get("qty") or 1)
        p = r.get("p") if isinstance(r.get("p"), (int, float)) else it.get("price")
        p = p if isinstance(p, (int, float)) else 0
        c = float(cost.get(str(r.get("no"))) or 0)
        w.writerow([time.strftime("%d.%m.%Y", time.gmtime(r.get("ts", 0) / 1000 + 5 * 3600)), r.get("no"), it.get("title", ""),
                    q, p, p * q, c * q, p * q - c * q])
    w.writerow([])
    w.writerow(["Расходы"])
    w.writerow(["Дата", "Категория", "Сумма", "Комментарий"])
    for x in sorted((fin or {}).get("expenses", []), key=lambda x: x.get("ts", 0)):
        w.writerow([time.strftime("%d.%m.%Y", time.gmtime(x.get("ts", 0) / 1000 + 5 * 3600)), x.get("cat"), x.get("amount"), x.get("note", "")])
    blob = ("﻿" + buf.getvalue()).encode("utf-8")          # BOM — чтобы Excel понял кириллицу
    info = max_api("POST", "/uploads", {"type": "file"})
    boundary = uuid.uuid4().hex
    name = time.strftime("prodazhi_%Y-%m-%d.csv", time.gmtime(time.time() + 5 * 3600))
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"data\"; filename=\"{name}\"\r\n"
            f"Content-Type: text/csv\r\n\r\n").encode() + blob + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(info["url"], data=body, method="POST", headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}", "Authorization": MAX_TOKEN, "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60, context=ctx()) as r:
        res = json.loads(r.read().decode("utf-8", "replace") or "{}")
    payload = {"token": res.get("token") or info.get("token")}
    for attempt in range(5):
        try:
            max_api("POST", "/messages", {"user_id": uid}, {"text": "📤 Выгрузка продаж и расходов — откройте в Excel.",
                                                             "attachments": [{"type": "file", "payload": payload}]})
            break
        except Exception as e:
            if "not.ready" in str(e) and attempt < 4:
                time.sleep(2)
                continue
            raise
    return "📤 Файл отправлен вам в чат с ботом — откройте его в Excel."


# ---------------------------------------------------------------- обработка событий

_cache = {}


def member_name(m):
    n = m.get("name") or " ".join(x for x in (m.get("first_name"), m.get("last_name")) if x)
    return (n or m.get("username") or str(m.get("user_id"))).strip()


def config():
    """Кто может управлять ботом (кэш на 10 минут):
    владелец канала — всегда; другие админы — только если владелец разрешил (/разрешить)."""
    if _cache.get("ts", 0) > time.time() - 600:
        return _cache
    state, _ = gh_file("state.json")
    ch = None
    try:
        d = gh("GET", f"/repos/{REPO}/contents/channel_id.txt?ref=main")
        ch = int(base64.b64decode(d["content"]).decode().strip())
    except Exception as e:
        print("channel:", e)
    admins = []
    try:
        data = max_api("GET", f"/chats/{ch}/members/admins")
        admins = [{"id": m["user_id"], "name": member_name(m), "owner": bool(m.get("is_owner"))}
                  for m in data.get("members") or [] if not m.get("is_bot")]
    except Exception as e:
        print("admins:", e)
    access, _ = gh_file("access.json")
    known = {a["id"] for a in admins}
    for gid, gname in ((access or {}).get("guests") or {}).items():   # приглашённые по ссылке (не админы канала)
        if int(gid) not in known:
            admins.append({"id": int(gid), "name": gname, "owner": False, "guest": True})
    allowed = set((access or {}).get("allowed", []))
    full = set((access or {}).get("full", []))     # админы с полными правами — как у владельца
    chief = {a["id"] for a in admins if a["owner"]}
    real = set(chief)
    same = set((access or {}).get("as_owner", []))  # аккаунты владельца — права в точности как у владельца
    if not any(not a.get("guest") for a in admins):  # MAX не ответил — берём последний известный список
        owners = set((state or {}).get("owners", []))
        chief = set((state or {}).get("chief", [])) or owners
    elif not chief:                                # MAX не сообщил, кто владелец — пускаем всех админов, как раньше
        chief = {a["id"] for a in admins if not a.get("guest")}
        owners = chief | (allowed & {a["id"] for a in admins})
    else:
        ids = {a["id"] for a in admins}
        chief = chief | (full & ids)
        owners = chief | (allowed & ids)
    if not any(not a.get("guest") for a in admins):
        chief |= full; owners |= full | allowed
    if real or not any(not a.get("guest") for a in admins):
        ids = {a["id"] for a in admins} | owners
        real |= same & ids
        chief |= real; owners |= real
    perms = {}
    raw = (access or {}).get("perms") or {}
    for u in owners:
        if u in chief:
            perms[u] = set(PERMS)                      # владелец и полные права — всё
        elif str(u) in raw:
            perms[u] = set(raw[str(u)]) & set(PERMS)
        else:
            perms[u] = set(PERMS) - {"access"}         # доступ без настройки — всё, кроме выдачи прав
    _cache.update(ts=time.time(), owners=owners, chief=chief, real=real, full=full,
                  admins=admins, allowed=allowed, channel=ch, perms=perms)
    return _cache


# ---------------------------------------------------------------- права админов по разделам

PERMS = {
    "sales": "🧾 Продажи и заказы",
    "items": "🧩 Товары: цены, скрыть, обложки",
    "posts": "📸 Посты в канал",
    "promo": "🎟 Промокоды",
    "reviews": "💬 Отзывы",
    "stats": "📈 Статистика и Метрика",
    "site": "🛠 Сайт: обновить, тех. работы, копия",
    "access": "👥 Доступ и права админов",
}
PERM_RULES = [
    ("sales", r"^/?(продано|продажи|история|отменить|удалить[ _]продажу|сбросить[ _]продажи|ответ)\b"),
    ("items", r"^/?(цена|скрыть|показать|обложка)\b"),
    ("posts", r"^/?(пост|изменить)\b"),
    ("promo", r"^/?(промо|код|проверить)\b"),
    ("reviews", r"^/?(отзыв|отзывы|удалить[ _]отзыв|одобрить|опубликовать|отклонить|проверка|на проверке)\b"),
    ("stats", r"^/?(стата|статистика|статус|топ|метрика)\b"),
    ("site", r"^/?(обновить|копия|бэкап|backup|техработы|тех\.?работы)\b"),
    ("access", r"^/?(доступ|разрешить|запретить|права|admins|пригласить)\b"),
]


CHANGE_RE = re.compile(r"^/?(продано|отменить|удалить[ _]продажу|сбросить[ _]продажи|цена\s+\d|скрыть\s+\d|показать\s+\d|"
                       r"обложка\s+\d+\s+\d|пост|изменить\s+\d+\s*\S|отзыв\s+\S|удалить[ _]отзыв|одобрить\s+\d|отклонить\s+\d|"
                       r"промо\s+(новый|создать|опубликовать|очередь|черновик|тихо|удалить|стоп|списать|вернуть|расписание\s+\S|подписка\s+\S)|"
                       r"тех\.?\s?работы\s+\S|права\s+\d+\s+\S|разрешить\s+\d|запретить\s+\d+\s+да|метрика\s+\S|копия|обновить|ответ\s+\d+\s+\S)", re.I)


def is_change(text, images=None):
    t = (text or "").strip()
    return bool(images) or bool(CHANGE_RE.match(t)) or is_sales_list(t.lower())


def log_action(uid, what):
    """Журнал действий админов (log.json, последние 500)."""
    try:
        cfg = config()
        name = next((a["name"] for a in cfg.get("admins", []) if a["id"] == uid), str(uid))
        entry = {"ts": int(time.time()), "uid": uid, "name": name, "what": str(what)[:300]}
        update_json("log.json", lambda d: d.update(log=(d.get("log") or [])[-499:] + [entry]), "Журнал: " + name, {"log": []})
    except Exception as e:
        print("log:", e)


def perm_for(text):
    low = (text or "").strip().lower()
    if is_sales_list(low):
        return "sales"
    for p, rx in PERM_RULES:
        if re.match(rx, low):
            return p
    return None


def has_perm(uid, p, cfg=None):
    cfg = cfg or config()
    return p is None or p in (cfg.get("perms") or {}).get(uid, set(PERMS) if uid in cfg["chief"] else set())


_asked = {}


def access_text(cfg):
    lines = []
    for k, a in enumerate(cfg["admins"], 1):
        mark = "👑 владелец" if a["id"] in cfg.get("real", ()) else (
            "⭐ полные права" if a["id"] in cfg.get("full", ()) else
            "✅ есть доступ" if a["id"] in cfg["owners"] else "🚫 нет доступа")
        lines.append(f"{k}. {a['name']}{' (по приглашению)' if a.get('guest') else ''} — {mark}")
    if not lines:
        return "Не удалось получить список админов канала. Попробуйте позже."
    return ("👥 Админы канала и доступ к боту:\n" + "\n".join(lines) +
            "\n\nДать или забрать доступ — кнопками ниже.\n"
            "➕ Дать доступ человеку, который не админ канала, — кнопка «Пригласить» (одноразовая ссылка на 24 часа).\n\n"
            "⭐ Полные права — всё как у владельца, включая выдачу доступа.\n"
            "⚙️ Какие разделы доступны админу — /права (или кнопка «⚙️ Права» в приложении).\n"
            "Без доступа админ не может пользоваться ботом: команды, продажи, цены, отзывы.")


MAINT_HELP = ("🚧 Технические работы на сайте\n\n"
              "🟡 Баннер — сайт работает, сверху висит предупреждение (например, «обновляем каталог, цены могут меняться»).\n"
              "🔴 Закрыть сайт — посетители видят экран «Технические работы» с вашим текстом и кнопками связи.\n"
              "Вы и админы (телефоны, где открыто приложение управления) видите сайт как обычно.\n\n"
              "Свой текст и время: /техработы закрыть до 18:00 Обновляем каталог\n"
              "Включается на сайте за 1–3 минуты.")


def maint_cmd(uid, text):
    rest = re.sub(r"^/?тех\.?\s?работы\s*", "", text.strip(), flags=re.I)
    m = re.match(r"^(баннер|закрыть|выкл\w*|стоп|off)?\s*(?:до\s+(\d{1,2}[:.]\d{2}(?:\s+\d{1,2}\.\d{1,2})?))?\s*(.*)$", rest, re.I | re.S)
    mode = (m.group(1) or "").lower() if m else ""
    if not mode:
        d, _ = gh_file("settings.json")
        cur = (d or {}).get("maint") or {}
        st = ("🔴 Сайт закрыт для посетителей" if cur.get("mode") == "closed" else "🟡 Включён баннер") if cur.get("on") else "🟢 Сайт работает в обычном режиме"
        extra = (f"\nТекст: «{cur.get('text')}»" if cur.get("on") and cur.get("text") else "") + (f"\nДо: {cur['until']}" if cur.get("on") and cur.get("until") else "")
        return send(uid, f"{MAINT_HELP}\n\nСейчас: {st}{extra}", menu=False, buttons=[
            [("🟡 Баннер", "/техработы баннер"), ("🔴 Закрыть сайт", "/техработы закрыть")],
            [("🟢 Выключить", "/техработы выкл")]])
    if mode in ("закрыть",) and "подтверждаю" not in rest.lower():
        return send(uid, "⚠️ Закрыть сайт для посетителей? Они увидят только экран «Технические работы».", menu=False,
                    buttons=[[("🔴 Да, закрыть", f"/техработы {rest} подтверждаю".replace("  ", " ")), ("❌ Отмена", "/отмена")]])
    txt = re.sub(r"\s*подтверждаю\s*$", "", (m.group(3) or "").strip(), flags=re.I)
    until = (m.group(2) or "").replace(".", ":", 1) if m.group(2) else ""
    if mode.startswith("выкл") or mode in ("стоп", "off"):
        val = {"on": False}
    else:
        val = {"on": True, "mode": "closed" if mode == "закрыть" else "banner", "text": txt[:300], "until": until,
               "since": int(time.time())}
    update_json("settings.json", lambda d: d.update(maint=val), "Бот: технические работы", {})
    try:
        run_sync()
    except Exception as e:
        print("maint sync:", e)
    if not val["on"]:
        return send(uid, "🟢 Технические работы выключены — сайт снова работает как обычно (через 1–3 минуты).")
    return send(uid, ("🔴 Сайт закрыт для посетителей" if val["mode"] == "closed" else "🟡 На сайте включён баннер")
                + (f" с текстом «{val['text']}»" if val["text"] else "") + (f" до {until}" if until else "")
                + ". Заработает через 1–3 минуты.\nВыключить: /техработы выкл", buttons=[[("🟢 Выключить", "/техработы выкл")]])


def perms_cmd(uid, text):
    """/права 2 — показать и переключать права админа №2; /права 2 +sales -promo; /права 2 все|полные|нет."""
    cfg = config()
    if uid not in cfg["chief"]:
        return send(uid, "Настраивать права может только владелец канала или админ с полными правами.")
    m = re.match(r"^/?права\s*(\d+)?\s*(.*)$", text.strip(), re.I | re.S)
    n = int(m.group(1)) if m and m.group(1) else 0
    if not n or not 1 <= n <= len(cfg["admins"]):
        return send(uid, access_text(cfg), menu=False, buttons=[[(f"⚙️ Права: {a['name'][:24]}", f"/права {k}")]
                                                             for k, a in enumerate(cfg["admins"], 1)
                                                             if not (a["id"] in cfg.get("real", ()))])
    target = cfg["admins"][n - 1]
    tid = target["id"]
    if tid in cfg.get("real", ()):
        return send(uid, "У владельца канала есть все права всегда.")
    ops = (m.group(2) or "").lower().split()
    if ops:
        if any(o in ("полные", "full") for o in ops) and uid not in cfg.get("real", ()):
            return send(uid, "Выдавать полные права может только владелец канала.")
        if tid in cfg.get("full", ()) and uid not in cfg.get("real", ()):
            return send(uid, "Менять права админа с полными правами может только владелец канала.")
        def f(d):
            perms = d.setdefault("perms", {})
            cur = set(perms.get(str(tid), [p for p in PERMS if p != "access"]))
            allowed = set(d.get("allowed", []))
            full = set(d.get("full", []))
            for o in ops:
                if o in ("полные", "full"):
                    full.add(tid); allowed.add(tid); cur = set(PERMS)
                elif o in ("все", "всё", "all"):
                    full.discard(tid); allowed.add(tid); cur = set(PERMS) - {"access"}
                elif o in ("нет", "ничего", "none"):
                    full.discard(tid); allowed.discard(tid); cur = set()
                elif o[:1] in "+-" and o[1:] in PERMS:
                    full.discard(tid)
                    (cur.add if o[0] == "+" else cur.discard)(o[1:])
                    allowed.add(tid)
            perms[str(tid)] = sorted(cur)
            if not cur:
                allowed.discard(tid)
            d["allowed"], d["full"] = sorted(allowed), sorted(full)
        update_json("access.json", f, f"Бот: права админа {tid}", {"allowed": []})
        _cache.clear()
        cfg = config()
    full = tid in cfg.get("full", ())
    mine = (cfg.get("perms") or {}).get(tid, set())
    lines = [f"⚙️ Права: {target['name']}" + (" — ⭐ полные права" if full else ("" if mine else " — 🚫 нет доступа")), ""]
    lines += [("✅ " if p in mine else "▫️ ") + name for p, name in PERMS.items()]
    lines.append("\nНажимайте кнопки, чтобы включить или выключить раздел.")
    btns = []
    keys = list(PERMS)
    for i in range(0, len(keys), 2):
        btns.append([((("✅ " if p in mine else "➕ ") + PERMS[p].split(" ", 1)[1][:18]), f"/права {n} {'-' if p in mine else '+'}{p}")
                     for p in keys[i:i + 2]])
    row = [("Всё, кроме прав", f"/права {n} все"), ("🚫 Ничего", f"/права {n} нет")]
    if uid in cfg.get("real", ()):
        row.insert(0, ("⭐ Полные", f"/права {n} полные"))
    btns.append(row)
    return send(uid, "\n".join(lines), menu=False, buttons=btns)


def change_access(uid, text, allow):
    cfg = config()
    if uid not in cfg["chief"]:
        return send(uid, "Выдавать доступ может только владелец канала или админ с полными правами.")
    n = num(text)
    target = None
    if n and 1 <= n <= len(cfg["admins"]):
        target = cfg["admins"][n - 1]
    elif n:
        target = next((a for a in cfg["admins"] if a["id"] == n), None)
    if not target:
        return send(uid, "Не нашёл такого админа. Посмотрите номера: /доступ")
    if target["id"] in cfg.get("real", ()):
        return send(uid, "У владельца доступ есть всегда.")
    if not allow and target["id"] in cfg.get("full", ()) and uid not in cfg.get("real", ()):
        return send(uid, "Снять полные права может только владелец канала.")
    if not allow and not re.search(r"\bда\b", text.lower()):       # забрать доступ — только после подтверждения
        k = cfg["admins"].index(target) + 1
        extra = " и полные права" if target["id"] in cfg.get("full", ()) else ""
        return send(uid, f"⚠️ Забрать доступ{extra} у «{target['name']}»?\nПосле этого он не сможет управлять ботом.",
                    menu=False, buttons=[[("✅ Да, забрать", f"/запретить {k} да"), ("❌ Отмена", "/отмена")]])
    def f(d):
        s = set(d.get("allowed", []))
        (s.add if allow else s.discard)(target["id"])
        d["allowed"] = sorted(s)
        if not allow:
            d["full"] = sorted(set(d.get("full", [])) - {target["id"]})
            (d.get("guests") or {}).pop(str(target["id"]), None)
    update_json("access.json", f, f"Бот: доступ {'выдан' if allow else 'снят'}", {"allowed": []})
    _cache.clear()
    try:
        send(target["id"], "✅ Владелец канала дал вам доступ к боту сайта. Напишите /меню." if allow
             else "🚫 Владелец канала закрыл вам доступ к боту сайта.", menu=allow)
    except Exception as e:
        print("notify admin:", e)
    return send(uid, (f"✅ {target['name']} теперь может пользоваться ботом." if allow
                      else f"🚫 {target['name']} больше не может пользоваться ботом.") + "\n\n" + access_text(config()))


def invite_hash(token):
    return hmac.new((SECRET or "salt").encode(), f"inv{token}".encode(), hashlib.sha256).hexdigest()[:24]


def make_invite(uid, perms=None):
    """Одноразовая ссылка-приглашение на 24 часа. В репозитории хранится только хэш."""
    cfg = config()
    if uid not in cfg["chief"]:
        raise ValueError("Приглашать может только владелец канала или админ с полными правами.")
    perms = [p for p in (perms or [p for p in PERMS if p != "access"]) if p in PERMS]
    if "access" in perms and uid not in cfg.get("real", ()):
        perms.remove("access")
    if not perms:
        raise ValueError("Отметьте хотя бы один раздел.")
    token = secrets.token_urlsafe(9).replace("-", "x").replace("_", "y")
    now = int(time.time())
    def f(d):
        inv = {k: v for k, v in (d.get("invites") or {}).items() if v.get("exp", 0) > now}
        inv[invite_hash(token)] = {"exp": now + 86400, "perms": perms, "by": uid}
        d["invites"] = inv
    update_json("access.json", f, "Бот: приглашение", {"allowed": []})
    return BOT_URL + ("&" if "?" in BOT_URL else "?") + "start=inv_" + token, perms


def invite_cmd(uid, text):
    try:
        link, perms = make_invite(uid)
    except ValueError as e:
        return send(uid, str(e))
    send(uid, "➕ Приглашение готово. Перешлите ссылку ниже человеку, которому хотите дать доступ к боту и приложению управления.\n\n"
              "• работает 24 часа и только один раз;\n"
              "• права: " + ", ".join(PERMS[p].split(" ", 1)[1] for p in perms) + " (поменять потом — «⚙️ Права» в «👥 Доступ»);\n"
              "• забрать доступ можно в любой момент в «👥 Доступ».\n\nНе выкладывайте ссылку в канал и группы.", menu=False)
    return send(uid, link, menu=False, buttons=[[("📋 Скопировать ссылку", link, "clipboard")]])


def accept_invite(uid, name, token):
    now = int(time.time())
    h = invite_hash(token)
    got = {}
    def f(d):
        inv = d.get("invites") or {}
        v = inv.pop(h, None)
        if v and v.get("exp", 0) > now:
            got.update(v)
            d.setdefault("guests", {})[str(uid)] = (name or f"Пользователь {uid}")[:40]
            d["allowed"] = sorted(set(d.get("allowed", [])) | {uid})
            d.setdefault("perms", {})[str(uid)] = sorted(v.get("perms") or [])
        d["invites"] = {k: x for k, x in inv.items() if x.get("exp", 0) > now}
    update_json("access.json", f, "Бот: приглашение принято", {"allowed": []})
    if not got:
        return send(uid, "😔 Ссылка-приглашение уже использована или устарела. Попросите владельца прислать новую.", menu=False)
    _cache.clear()
    log_action(uid, "принял приглашение")
    for chief in config()["chief"]:
        if chief != uid:
            try:
                send(chief, f"➕ «{name}» принял приглашение и получил доступ к боту.\nПрава и доступ: /доступ")
            except Exception as e:
                print("notify chief:", e)
    return send(uid, "✅ Готово! У вас есть доступ к управлению ботом сайта «3D модели в Нагаево».\n"
                     "Пользуйтесь кнопками меню или откройте «🛠 Приложение управления».")


def deny(uid, cfg):
    """Админ без доступа написал боту — отказ и запрос владельцу (не чаще раза в час)."""
    send(uid, "🚫 У вас нет доступа к управлению ботом. Я отправил запрос владельцу канала.", menu=False)
    if time.time() - _asked.get(uid, 0) < 3600:
        return
    _asked[uid] = time.time()
    k = next((i for i, a in enumerate(cfg["admins"], 1) if a["id"] == uid), None)
    name = next((a["name"] for a in cfg["admins"] if a["id"] == uid), str(uid))
    for chief in cfg["chief"]:
        try:
            send(chief, f"🔐 Админ «{name}» хочет пользоваться ботом.\nРазрешить: /разрешить {k}\nВсе доступы: /доступ")
        except Exception as e:
            print("notify chief:", e)





REVIEW_HELP = ("💬 Как добавить отзыв на сайт (в канал он не попадёт):\n\n"
               "Пришлите фото (товар у покупателя или скриншот переписки) и в подписи:\n\n"
               "/отзыв Купили дракона сыну — играет каждый день! — Анна\n\n"
               "• можно без фото — просто текст после /отзыв;\n"
               "• можно указать товар: добавьте №12 — на сайте появится ссылка на него;\n"
               "• на скриншотах закрасьте фамилию и телефон покупателя.\n\n"
               "🗂 Отзывы — список, /удалить отзыв 3 — убрать с сайта.")


def add_review(uid, text, images):
    body = re.sub(r"^/?(отзыв|review)\s*:?\s*", "", text.strip(), flags=re.I).strip()
    if not body and not images:
        return send(uid, REVIEW_HELP)
    m = re.search(r"№\s*(\d+)", body)
    no = int(m.group(1)) if m else None
    rid_holder = {}
    def reserve(d):
        rid = max([r.get("id", 0) for r in d.get("reviews", []) + d.get("pending", [])] + [0]) + 1
        rid_holder["id"] = rid
    data, _ = gh_file("reviews.json")
    reserve(data or {"reviews": []})
    rid = rid_holder["id"]
    raw = []
    for k, a in enumerate(images[:4]):
        url = (a.get("payload") or {}).get("url")
        if not url:
            continue
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=25, context=ctx()) as r:
                blob = r.read()
            path = f"reviews/raw/{rid}_{k}.jpg"
            gh_put_bytes(path, blob, f"Бот: фото отзыва {rid}")
            raw.append(path)
        except Exception as e:
            print("review photo:", e)
    entry = {"id": rid, "ts": int(time.time() * 1000), "text": body[:800], "raw": raw, "no": no}
    update_json("reviews.json", lambda d: d.setdefault("reviews", []).append(entry), f"Бот: отзыв {rid}", {"reviews": []})
    item = _find(catalog_items(), no) if no else None
    extra = f"\nТовар: №{no} {item['title']}" if item else (f"\n⚠️ Товара №{no} нет на сайте — ссылки не будет." if no else "")
    lost = len(images) - len(raw)
    return send(uid, f"💬 Отзыв №{rid} добавлен на сайт" + (f" с фото ({len(raw)})" if raw else "") + "." + extra
                + (f"\n⚠️ {lost} фото не удалось сохранить." if lost > 0 else "")
                + "\nПоявится через 1–2 минуты. Убрать: /удалить отзыв " + str(rid))


def list_reviews(uid):
    data, _ = gh_file("reviews.json")
    revs = (data or {}).get("reviews", [])
    pend = (data or {}).get("pending", [])
    tail = (f"\n\n⭐ На проверке с сайта: {len(pend)} — /проверка" if pend else "")
    if not revs:
        return send(uid, "Отзывов на сайте пока нет.\n\n" + REVIEW_HELP + tail)
    lines = [f"№{r['id']} — {time.strftime('%d.%m', time.gmtime(r['ts'] / 1000 + 3 * 3600))}"
             f"{' ' + r['name'] if r.get('name') else ''}: "
             f"{(r.get('text') or '(без текста)')[:60]}{' 📷' if r.get('raw') else ''}" for r in revs[-15:]]
    return send(uid, "🗂 Отзывы на сайте:\n" + "\n".join(lines) + "\n\nУбрать: /удалить отзыв 3" + tail)


def delete_review(uid, text):
    rid = num(text)
    if not rid:
        return send(uid, "Напишите номер отзыва: /удалить отзыв 3\nНомера — в «🗂 Отзывы».")
    gone = []
    def f(d):
        before = len(d.get("reviews", []))
        d["reviews"] = [r for r in d.get("reviews", []) if r.get("id") != rid]
        gone.append(before != len(d["reviews"]))
    update_json("reviews.json", f, f"Бот: удалён отзыв {rid}", {"reviews": []})
    return send(uid, f"🗑 Отзыв №{rid} убран с сайта." if gone and gone[0] else f"Отзыва №{rid} нет.")


# ---------------------------------------------------------------- отзывы покупателей с сайта

SITE_ORIGINS = ("https://3dnagaevo.ru", "https://www.3dnagaevo.ru", "https://3dmodelnagaevo.github.io")
_rev_ip = {}


def _cors(origin):
    o = origin if origin in SITE_ORIGINS or (origin or "").startswith("http://localhost") else SITE_ORIGINS[0]
    return {"Access-Control-Allow-Origin": o, "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type", "Vary": "Origin"}


# автопроверка отзывов: нормальные — сразу на сайт, сомнительные — админам, мусор — отбрасываем
_LAT = str.maketrans("aeopcxykmthb3ё", "аеорсхукмтнвзе")
BAD_ANY = re.compile(r"пизд|ху[йияюе][^д]|хуё|залуп|гандон|пид[ао]?р|шлюх|манд[ао]в|муд[ао]к|мудил|долбо[её]б|[её]бан|[её]бну|[её]бал|въеб|выеб|заеб|отъеб|уеб|ебл[аои]|ебать|ебуч")
BAD_WORD = re.compile(r"(?:^|[^а-я])(?:бля|сук[аиу]|ебл|еби|говн|дроч|срал|сран|жоп|урод|дебил|идиот|тупо[йе]|мраз|твар|лох|чмо|скотин)")
URL_RE = re.compile(r"(https?://|www\.|\b[a-z0-9-]+\.(?:ru|com|net|org|su|рф|io|me|xyz|top|site|online|shop|store|info|biz|pro|club)\b|t\.me/|@[a-z0-9_]{4,})", re.I)
PHONE_RE = re.compile(r"(?:\+?\d[\s\-()]*){10,}")
EMAIL_RE = re.compile(r"\S+@\S+\.\S+")


def review_check(name, text, stars, no, existing):
    """→ ("ok" | "moderate" | "reject", причина)."""
    low = (name + " " + text).lower().translate(_LAT)
    letters = re.findall(r"[а-яa-z]", low)
    links = len(URL_RE.findall(text)) + len(URL_RE.findall(name))
    if links >= 2 or not re.search(r"[а-яa-z]{2}", text.lower()):
        return "reject", "ссылки или бессмыслица"
    if re.search(r"(.)\1{6,}", text) or (letters and len(set(letters)) < 4):
        return "reject", "бессмыслица"
    if any(r.get("text", "").strip().lower() == text.strip().lower() and r.get("no") == no for r in existing):
        return "reject", "повтор"
    why = []
    if BAD_ANY.search(low) or BAD_WORD.search(low):
        why.append("грубые слова")
    if links:
        why.append("ссылка")
    if PHONE_RE.search(text) or EMAIL_RE.search(text):
        why.append("телефон или почта")
    if stars and stars <= 2:
        why.append(f"низкая оценка ({stars}★)")
    if re.search(r"\d", name) or len(name.split()) > 3:
        why.append("странное имя")
    caps = [c for c in text if c.isalpha()]
    if len(caps) >= 12 and sum(c.isupper() for c in caps) / len(caps) > 0.6:
        why.append("капс")
    cyr = len(re.findall(r"[а-яё]", text.lower()))
    if letters and cyr / max(1, len(re.findall(r"[a-zа-яё]", text.lower()))) < 0.5:
        why.append("не по-русски")
    if len(text) < 8:
        why.append("слишком коротко")
    return ("moderate", ", ".join(why)) if why else ("ok", "")


def site_review(event, headers):
    """Отзыв с сайта → reviews.json (pending) → владельцу на проверку."""
    cors = _cors(headers.get("origin", ""))
    if (event.get("httpMethod") or "").upper() == "OPTIONS":
        return {"statusCode": 204, "headers": cors, "body": ""}
    def out(code, **kw):
        return {"statusCode": code, "headers": {**cors, "Content-Type": "application/json"},
                "body": json.dumps(kw, ensure_ascii=False)}
    try:
        raw = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode()
        d = json.loads(raw)
    except Exception:
        return out(400, error="Не удалось прочитать отзыв")
    if d.get("web"):                                   # скрытое поле — его заполняют только спам-боты
        return out(200, ok=True)
    name = re.sub(r"\s+", " ", str(d.get("name") or "")).strip()[:40]
    text = str(d.get("text") or "").strip()[:800]
    try:
        no = int(d.get("no"))
        stars = max(0, min(5, int(d.get("stars") or 0)))
    except Exception:
        return out(400, error="Неверные данные")
    if len(name) < 2:
        return out(400, error="Напишите имя")
    if len(text) < 3:
        return out(400, error="Напишите пару слов о модели")
    ip = ((event.get("requestContext") or {}).get("identity") or {}).get("sourceIp") or headers.get("x-forwarded-for", "?")
    now = time.time()
    hits = [x for x in _rev_ip.get(ip, []) if now - x < 3600]
    if len(hits) >= 3:
        return out(429, error="Слишком много отзывов подряд. Попробуйте позже.")
    _rev_ip[ip] = hits + [now]
    item = _find(catalog_items(), no)
    if not item:
        return out(400, error="Модель не найдена")
    entry, verdict = {}, {}
    def f(dd):
        pend = dd.setdefault("pending", [])
        revs = dd.setdefault("reviews", [])
        v, why = review_check(name, text, stars, no, revs + pend)
        verdict.update(v=v, why=why)
        if v == "reject":
            return
        if v == "moderate" and len(pend) >= 50:
            raise RuntimeError("очередь отзывов переполнена")
        rid = max([r.get("id", 0) for r in revs + pend] + [0]) + 1
        entry.update(id=rid, ts=int(now * 1000), no=no, name=name, stars=stars, text=text, raw=[], src="site")
        if v == "ok":
            revs.append(dict(entry, auto=True))
        else:
            pend.append(dict(entry, why=why))
    try:
        update_json("reviews.json", f, f"Сайт: отзыв о №{no}", {"reviews": []})
    except Exception as e:
        print("site review:", e)
        return out(503, error="Не получилось отправить. Попробуйте позже.")
    if verdict.get("v") == "reject":
        print("review rejected:", verdict.get("why"), name, text[:80])
        return out(200, ok=True, published=False)       # спамеру не сообщаем, что отзыв отброшен
    stars_txt = "  " + "★" * stars + "☆" * (5 - stars) if stars else ""
    if verdict["v"] == "ok":
        msg = f"✅ Новый отзыв опубликован автоматически\n\n№{no} «{item['title']}»\n{name}{stars_txt}\n«{text}»"
        btns = [[("🗑 Убрать с сайта", f"/удалить отзыв {entry['id']}")]]
    else:
        msg = f"⭐ Отзыв с сайта — нужна проверка ({verdict['why']})\n\n№{no} «{item['title']}»\n{name}{stars_txt}\n«{text}»"
        btns = [[("✅ Опубликовать", f"/одобрить {entry['id']}"), ("🗑 Удалить", f"/отклонить {entry['id']}")]]
    notify_owners(msg, btns, "reviews")
    return out(200, ok=True, published=verdict["v"] == "ok")


def moderate_review(uid, text, approve):
    rid = num(text)
    if not rid:
        return pending_reviews(uid)
    res = {}
    def f(d):
        pend = d.get("pending", [])
        r = next((x for x in pend if x.get("id") == rid), None)
        if not r:
            return
        d["pending"] = [x for x in pend if x.get("id") != rid]
        if approve:
            d.setdefault("reviews", []).append(r)
        res["r"] = r
    update_json("reviews.json", f, f"Бот: отзыв {rid} {'опубликован' if approve else 'отклонён'}", {"reviews": []})
    r = res.get("r")
    if not r:
        return send(uid, f"Отзыва №{rid} на проверке нет (возможно, его уже обработал другой админ).")
    if approve:
        return send(uid, f"✅ Отзыв №{rid} от {r.get('name')} опубликован. На сайте появится через 1–2 минуты.",
                    buttons=[[("🗑 Всё-таки убрать", f"/удалить отзыв {rid}")]])
    return send(uid, f"🗑 Отзыв №{rid} от {r.get('name')} удалён.")


def pending_reviews(uid):
    data, _ = gh_file("reviews.json")
    pend = (data or {}).get("pending", [])
    if not pend:
        return send(uid, "Отзывов на проверке нет.")
    for r in pend[-10:]:
        send(uid, f"⭐ №{r['id']} — {r.get('name')} о товаре №{r.get('no')}"
                  + (f" ({r['why']})" if r.get("why") else "") + f"\n«{(r.get('text') or '')[:500]}»",
             menu=False, buttons=[[("✅ Опубликовать", f"/одобрить {r['id']}"), ("🗑 Удалить", f"/отклонить {r['id']}")]])
    return send(uid, f"На проверке: {len(pend)}. Нажимайте кнопки под отзывами.")


SALE_ONLY_RE = re.compile(r"^[\d\s,;:xх×*\-—–.шт]+$", re.I)
SALE_CHUNK_RE = re.compile(r"^(\d+)(?:\s*[xх×*\-—–:]?\s*(\d+))?\s*(?:шт\.?)?$", re.I)


def parse_sales(text):
    """«38 2\\n20, 15х3» → ([(38, 2), (20, 1), (15, 3)], [нераспознанные куски])."""
    pairs, bad = [], []
    for chunk in re.split(r"[\n,;]+", text):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = SALE_CHUNK_RE.match(chunk)
        if m:
            pairs.append((int(m.group(1)), max(1, min(int(m.group(2) or 1), 100))))
        else:
            bad.append(chunk)
    return pairs, bad


def is_sales_list(text):
    return bool(text) and bool(re.search(r"\d", text)) and bool(SALE_ONLY_RE.match(text))


SALES_HELP = ("✅ Пришлите номера проданных товаров — можно сразу несколько, каждый с новой строки или через запятую:\n\n"
              "38\n20 2\n15\n\n"
              "(число после номера — сколько штук, если больше одной)\n"
              "Номер есть в карточке товара на сайте и в тексте заказа. Ошиблись — /отменить.")


# ---------------------------------------------------------------- история продаж: просмотр и удаление

def ensure_sale_ids(d):
    """У каждой отметки о продаже — постоянный номер (#), по нему её можно удалить."""
    nxt = max([r.get("id", 0) for r in d.get("sales", [])] + [0]) + 1
    for r in d.get("sales", []):
        if not r.get("id"):
            r["id"] = nxt
            nxt += 1
    return nxt


def history_text(items, sales, limit=25):
    if not sales:
        return "Отметок о продажах пока нет."
    rows = []
    for r in sales[-limit:][::-1]:
        it = next((i for i in items if i.get("no") == r.get("no")), None)
        date = time.strftime("%d.%m", time.gmtime(r.get("ts", 0) / 1000 + 3 * 3600))
        rows.append(f"#{r.get('id', '?')} · {date} · №{r['no']} {(it or {}).get('title', '?')[:28]} × {r.get('qty', 1)}")
    more = f"\n…и ещё {len(sales) - limit} более старых" if len(sales) > limit else ""
    return ("📜 История продаж (новые сверху):\n" + "\n".join(rows) + more +
            "\n\nУдалить одну отметку: /удалить продажу 7  (номер после #)\n"
            "Обнулить «Продано» у товара: /сбросить продажи 38  (номер товара)")


def delete_sale(d, sid):
    before = len(d.get("sales", []))
    d["sales"] = [r for r in d.get("sales", []) if r.get("id") != sid]
    return before != len(d["sales"])


def reset_item_sales(d, no):
    before = sum(int(r.get("qty") or 1) for r in d.get("sales", []) if r.get("no") == no)
    d["sales"] = [r for r in d.get("sales", []) if r.get("no") != no]
    return before


def record_sales(uid, pairs, bad):
    items = catalog_items()
    good = [(no, q) for no, q in pairs if _find(items, no)]
    unknown = [no for no, q in pairs if not _find(items, no)]
    if not good:
        msg = "Не нашёл таких номеров на сайте: " + ", ".join(f"№{n}" for n in unknown) if unknown else "Не понял номера."
        return send(uid, msg + "\n\n" + SALES_HELP)
    batch = int(time.time() * 1000)
    def add(d):
        nxt = ensure_sale_ids(d)
        d.setdefault("sales", []).extend(
            {"id": nxt + k, "no": no, "qty": q, "ts": batch, "b": batch,
             **({"p": _find(items, no).get("price")} if isinstance(_find(items, no).get("price"), (int, float)) else {})}
            for k, (no, q) in enumerate(good))
    data = update_json("sales.json", add,
        "Бот: продано " + ", ".join(f"№{no}×{q}" for no, q in good), {"sales": []})
    lines = []
    for no, q in good:
        total = sum(int(r.get("qty") or 1) for r in data["sales"] if r.get("no") == no)
        lines.append(f"• №{no} {_find(items, no)['title']} × {q} (всего {total})")
    msg = f"✅ Записал продажи ({sum(q for _, q in good)} шт.):\n" + "\n".join(lines)
    if unknown:
        msg += "\n\n⚠️ Не нашёл на сайте и пропустил: " + ", ".join(f"№{n}" for n in unknown)
    if bad:
        msg += "\n⚠️ Не понял: " + ", ".join(bad)
    btns = []
    try:
        d, _ = gh_file("promos.json")
        act = [c for c in (d or {}).get("codes", []) if c.get("code") and promo_status(c)[0] == "active"]
        btns = [[(f"🎟 Был промокод {c['code']} — активировать", f"/промо списать {c['code']}")] for c in act[:4]]
        btns.append([("🎟 Другой промокод (личный)", "/код")])
    except Exception as e:
        print("sales promo buttons:", e)
    return send(uid, msg + "\n\nЗаказ был с промокодом? Нажмите его ниже — активация засчитается.\n"
                     "Ошиблись с продажей — /отменить (отменит всю эту пачку).", buttons=btns)


def handle_owner(uid, text, images):
    if text in MENU_BUTTONS:
        text = MENU_BUTTONS[text]
    if not images:
        text, done = ask_or_fill(uid, text)
        if done:
            return
    need = "posts" if images and re.match(r"^/?пост", (text or "").lower()) else (
        "reviews" if images and re.match(r"^/?отзыв", (text or "").lower()) else perm_for(text))
    if need and not has_perm(uid, need):
        return send(uid, f"🚫 У вас нет прав на раздел «{PERMS[need]}». Попросите владельца канала выдать их: «👥 Доступ».")
    if is_change(text, images):
        log_action(uid, text if not images else f"{text} (+{len(images)} фото)")
    low = text.lower()
    if low.startswith("/отзыв") and not low.startswith("/отзывы") or low.startswith("отзыв:") or "#отзыв" in low:
        return add_review(uid, text.replace("#отзыв", "").replace("#Отзыв", ""), images)
    if low in ("/отзывы", "отзывы"):
        return list_reviews(uid)
    if re.match(r"^/?удалить[ _]отзыв", low):
        return delete_review(uid, text)
    if re.match(r"^/start\s+inv_", low):
        return send(uid, "У вас уже есть доступ к боту — приглашение не понадобилось. Его можно переслать другому человеку.")
    if low in ("/пригласить", "пригласить", "➕ пригласить"):
        return invite_cmd(uid, text)
    if low in ("/доступ", "доступ", "/admins"):
        cfg = config()
        if uid not in cfg["chief"]:
            return send(uid, "Список доступов видит только владелец канала и админы с полными правами.")
        btns = []
        for k, a in enumerate(cfg["admins"], 1):
            if a["id"] in cfg.get("real", ()):
                continue
            short = a["name"][:20]
            btns.append([(f"⚙️ Права", f"/права {k}"), (f"🚫 Забрать: {short}", f"/запретить {k}")] if a["id"] in cfg["owners"]
                        else [(f"✅ Дать доступ: {short}", f"/разрешить {k}")])
        btns.append([("➕ Пригласить по ссылке", "/пригласить")])
        return send(uid, access_text(cfg), menu=False, buttons=btns)
    if low.startswith("/разрешить"):
        return change_access(uid, text, True)
    if low.startswith("/запретить"):
        return change_access(uid, text, False)
    if re.match(r"^/?тех\.?\s?работы", low):
        return maint_cmd(uid, text)
    if re.match(r"^/?права\b", low):
        return perms_cmd(uid, text)
    if low in ("/копия", "копия", "/бэкап", "/backup"):
        try:
            gh("POST", f"/repos/{REPO}/actions/workflows/backup.yml/dispatches",
               {"ref": "main", "inputs": {"to": str(uid)}})
        except Exception as e:
            return send(uid, f"❌ Не получилось запустить копию: {e}")
        return send(uid, "🗄 Собираю резервную копию — пришлю архив сюда через 1–3 минуты.\n"
                         "(Автоматически копия приходит каждое воскресенье.)")
    if low.startswith("/метрика"):
        import datetime as _dt
        if "сброс" in low:
            now = int(time.time())
            update_json("settings.json", lambda d: (d.pop("metrika_from", None), d.update(metrika_from_ts=now)),
                        "Бот: статистика с этого момента", {})
            when = (_dt.datetime.utcfromtimestamp(now) + _dt.timedelta(hours=5)).strftime("%d.%m.%Y %H:%M")
            return send(uid, f"🧹 Статистика сайта теперь считается с {when} (по Уфе) — всё, что было раньше, не учитывается. "
                             "Прошлые данные в Метрике сохранились — их можно снова показать: /метрика всё")
        if "всё" in low or "все" in low:
            update_json("settings.json", lambda d: (d.pop("metrika_from", None), d.pop("metrika_from_ts", None)),
                        "Бот: статистика за весь период", {})
            return send(uid, "📈 Статистика снова показывается за весь выбранный период.")
        return send(uid, "/метрика сброс — считать статистику сайта с сегодняшнего дня\n/метрика всё — показывать всё")
    if re.match(r"^/?ответ\b", low):
        return owner_reply(uid, text)
    if re.match(r"^/?промо", low):
        return promo_cmd(uid, text)
    if re.match(r"^/?(код|проверить)\b", low):
        return check_code(uid, text)
    if re.match(r"^/?(одобрить|опубликовать)\b", low):
        return moderate_review(uid, text, True)
    if re.match(r"^/?отклонить\b", low):
        return moderate_review(uid, text, False)
    if low in ("/проверка", "проверка", "/на проверке"):
        return pending_reviews(uid)
    if re.match(r"^/?изменить", low):
        return edit_post(uid, text)
    if is_sales_list(text):                       # просто номера: «38 2», «20» … — это продажи
        pairs, bad = parse_sales(text)
        return record_sales(uid, pairs, bad)
    cmd = text.split()[0].lower() if text else ""
    cmd = EN.get(cmd, cmd)

    if cmd in ("/помощь", "помощь", "/start"):
        return send(uid, HELP)
    if cmd in ("/меню", "меню"):
        return send(uid, "Меню 👇")
    if cmd in ("/статус",):
        ov, _ = gh_file("overrides.json")
        return send(uid, status_text(catalog_items(), ov or {}))
    if cmd == "/топ":
        return send(uid, top_text(catalog_items()))
    if cmd in ("/стата", "/статистика"):
        sd, _ = gh_file("sales.json")
        return send(uid, stats_text(catalog_items(), (sd or {}).get("sales", [])))
    if cmd == "/продано":
        rest = text.split(None, 1)[1] if len(text.split(None, 1)) > 1 else ""
        if not rest.strip():
            return send(uid, SALES_HELP)
        pairs, bad = parse_sales(rest)
        return record_sales(uid, pairs, bad)
    if cmd in ("/история", "история"):
        sd, _ = gh_file("sales.json")
        sd = sd or {"sales": []}
        if any(not r.get("id") for r in sd.get("sales", [])):     # старые отметки без номеров — пронумеровать
            sd = update_json("sales.json", ensure_sale_ids, "Бот: номера отметок продаж", {"sales": []})
        return send(uid, history_text(catalog_items(), sd.get("sales", [])))
    if re.match(r"^/?удалить[ _]продаж", text.lower()):
        sid = num(text)
        if not sid:
            return send(uid, "Напишите номер отметки из «📜 История»: /удалить продажу 7")
        res = []
        update_json("sales.json", lambda d: res.append(delete_sale(d, sid)), f"Бот: удалена продажа #{sid}", {"sales": []})
        return send(uid, f"🗑 Отметка #{sid} удалена. «Продано» на сайте обновится через 1–2 минуты."
                    if res and res[0] else f"Отметки #{sid} нет — посмотрите номера в «📜 История».")
    if re.match(r"^/?сбросить[ _]продаж", text.lower()):
        no = num(text)
        if not no:
            return send(uid, "Напишите номер товара: /сбросить продажи 38 — у него станет «Продано: 0».")
        res = []
        update_json("sales.json", lambda d: res.append(reset_item_sales(d, no)), f"Бот: сброс продаж №{no}", {"sales": []})
        it = _find(catalog_items(), no)
        name = f"№{no} {it['title']}" if it else f"№{no}"
        return send(uid, f"🧹 Удалил все отметки продаж у {name} ({res[0]} шт.). На сайте «Продано» исчезнет через 1–2 минуты."
                    if res and res[0] else f"У {name} отметок о продажах нет.")
    if cmd == "/отменить":
        removed = []
        def f(d):
            sales = d.get("sales") or []
            if not sales:
                return
            last_b = sales[-1].get("b")
            while sales and (sales[-1].get("b") == last_b if last_b else not removed):
                removed.append(sales.pop())
        update_json("sales.json", f, "Бот: отмена последних продаж", {"sales": []})
        if not removed:
            return send(uid, "Отметок о продажах пока нет.")
        return send(uid, "↩️ Отменил последнюю отметку: " + ", ".join(f"№{r['no']}×{r.get('qty', 1)}" for r in reversed(removed)))
    if cmd == "/продажи":
        sd, _ = gh_file("sales.json")
        return send(uid, sales_text(catalog_items(), (sd or {}).get("sales", [])))
    if cmd == "/шаблон":
        send(uid, TEMPLATE_HELP, menu=False)
        return send(uid, POST_TEMPLATE)
    if cmd == "/обновить":
        run_sync()
        return send(uid, "🔄 Запустил обновление. Сайт обновится через 1–2 минуты.")
    if cmd in ("/скрыть", "/показать"):
        no = num(text)
        if not no:
            return send(uid, f"Напишите номер товара, например: {cmd} 12\nНомер есть на сайте в карточке товара.")
        def f(ov):
            h = set(ov.get("hidden", []))
            (h.add if cmd == "/скрыть" else h.discard)(no)
            ov["hidden"] = sorted(h)
        update_overrides(f, f"Бот: {cmd} {no}")
        item = next((i for i in catalog_items() if i.get("no") == no), None)
        name = f": {item['title']}" if item else " (такого номера сейчас нет на сайте)"
        return send(uid, (f"🙈 Скрыл с сайта №{no}" if cmd == "/скрыть" else f"👁 Вернул на сайт №{no}") + name +
                    "\nСайт обновится через 1–2 минуты.")
    if cmd == "/цена":
        parts = text.split()
        no = num(parts[1]) if len(parts) > 1 else None
        if not no or len(parts) < 3:
            return send(uid, "Напишите номер товара и новую цену, например:\n/цена 12 300\n\nВернуть цену из поста: /цена 12 сброс")
        if parts[2].lower() in ("сброс", "reset", "-"):
            update_overrides(lambda ov: ov.setdefault("price", {}).pop(str(no), None), f"Бот: /цена {no} сброс")
            return send(uid, f"↩️ Цена №{no} снова берётся из поста. Сайт обновится через 1–2 минуты.")
        p = num(parts[2])
        update_overrides(lambda ov: ov.setdefault("price", {}).__setitem__(str(no), p), f"Бот: /цена {no} {p}")
        return send(uid, f"💰 Цена №{no} → {p} ₽. Сайт обновится через 1–2 минуты.")
    if cmd == "/обложка":
        parts = text.split()
        no = num(parts[1]) if len(parts) > 1 else None
        k = num(parts[2]) if len(parts) > 2 else None
        if not no:
            return send(uid, "Напишите номер товара, например: /обложка 12\nПришлю его фото с номерами — выберете главное.")
        if not k:
            item = next((i for i in catalog_items() if i.get("no") == no), None)
            if not item:
                return send(uid, f"Товара №{no} нет на сайте. Номер виден в карточке товара.")
            ov, _ = gh_file("overrides.json")
            base = item.get("photos_orig") or item.get("photos") or []
            if len(base) < 2:
                return send(uid, f"У «{item['title']}» одно фото — выбирать не из чего.")
            for n, ph in enumerate(base[:10], 1):
                try:
                    max_api("POST", "/messages", {"user_id": uid}, {
                        "text": f"Фото {n}", "attachments": [{"type": "image", "payload": {"url": SITE_URL + ph}}]})
                except Exception as e:
                    print("photo:", e)
            cur = ((ov or {}).get("cover") or {}).get(str(no), 1)
            nums = list(range(1, min(len(base), 10) + 1))
            btns = [[(f"{'✅ ' if n == cur else ''}Фото {n}", f"/обложка {no} {n}") for n in nums[i:i + 4]] for i in range(0, len(nums), 4)]
            return send(uid, f"🖼 «{item['title']}»: сейчас главное — фото {cur}.\nКакое сделать главным?",
                        menu=False, buttons=btns)
        def f(ov):
            c = ov.setdefault("cover", {})
            if k <= 1:
                c.pop(str(no), None)
            else:
                c[str(no)] = k
        update_overrides(f, f"Бот: /обложка {no} {k}")
        return send(uid, (f"🖼 Готово: у №{no} главным будет фото {k}." if k > 1 else f"🖼 Готово: у №{no} фото как в посте.")
                    + "\nСайт обновится через 1–2 минуты.")
    if cmd in ("/пост", "/post", "/новый"):
        if not images and text.strip().lower() in ("/пост", "/post", "/новый"):
            return send(uid, "📸 Пришлите фото товара и в подписи к нему:\n\n/пост Название\nЦена: 200\n#категория\n\n"
                             "Можно добавить строки «Размер: …», «Цвет: …» и описание.")
        try:
            post_text, atts = build_post(text, images)
        except ValueError as e:
            return send(uid, f"❌ Не опубликовал: {e}\n\nПример:\n/пост Дракон-антистресс\nЦена: 200\n#антистресс")
        try:
            max_api("POST", "/messages", {"chat_id": config()["channel"]},
                    {"text": post_text, **({"attachments": atts} if atts else {})})
        except Exception as e:
            return send(uid, f"❌ MAX не дал опубликовать. Проверьте, что у бота в канале есть право «Публиковать посты».\n\n{e}")
        return send(uid, "✅ Опубликовал в канал. На сайте появится через 1–2 минуты.")
    if images and not text:
        return send(uid, "Чтобы опубликовать товар, пришлите фото с подписью, начинающейся с /пост.")
    return send(uid, "Не понял команду. Нажмите «❓ Помощь» или выберите кнопку в меню.")


# ---------------------------------------------------------------- библиотека ссылок (групповой чат)

LINK_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
SITES = {"makerworld": "MakerWorld", "printables": "Printables", "thingiverse": "Thingiverse",
         "cults3d": "Cults3D", "thangs": "Thangs", "myminifactory": "MyMiniFactory", "crealitycloud": "Creality Cloud",
         "sketchfab": "Sketchfab", "grabcad": "GrabCAD", "3dtoday": "3DToday", "3dsky": "3DSky", "cgtrader": "CGTrader",
         "makeronline": "Makeronline", "pinshape": "Pinshape", "youmagine": "YouMagine", "patreon": "Patreon",
         "etsy": "Etsy", "aliexpress": "AliExpress", "ozon": "Ozon", "wildberries": "WB", "wb.ru": "WB", "avito": "Авито",
         "youtube": "YouTube", "youtu.be": "YouTube", "rutube": "Rutube", "pinterest": "Pinterest",
         "vk.com": "ВК", "t.me": "Telegram", "max.ru": "MAX", "disk.yandex": "Яндекс Диск", "drive.google": "Google Диск"}
LINKS_HELP = """🔖 Библиотека ссылок

Просто кидайте сюда ссылки на модели — бот сохранит каждую под номером.
Подпишите своими словами и хэштегами, по ним потом удобно искать:
https://makerworld.com/... дракон шарнирный #антистресс #подарок

🔍 найти дракон — поиск по названию, подписи, тегам и сайту
📋 /ссылки — последние; /ссылки #антистресс или /ссылки makerworld — фильтр
🏷 /теги — все теги
➕ /тег 12 #дракон — добавить теги к ссылке №12
✅ /готово 12 — отметить «напечатано»; /ссылки готово или /ссылки план — фильтр
🗑 /удалить ссылку 12
📥 /импорт — забрать ссылки из старых сообщений этого чата"""


def link_site(url):
    host = re.sub(r"^https?://(www\.)?", "", url.lower()).split("/")[0]
    for k, v in SITES.items():
        if k in host:
            return v
    return host


def link_title(url):
    """Название из адреса: makerworld.com/en/models/2210286-articulated-dragon → «articulated dragon»."""
    path = re.sub(r"[?#].*$", "", re.sub(r"^https?://[^/]+", "", url))
    seg = [x for x in path.split("/") if x]
    best = ""
    for x in reversed(seg):
        x = re.sub(r"^\d+[-_]?", "", urllib.parse.unquote(x))
        if re.search(r"[A-Za-zА-Яа-я]{3,}", x) and x.lower() not in ("models", "model", "thing", "en", "ru", "design", "files"):
            best = x
            break
    return re.sub(r"[-_+]+", " ", best).strip()[:80]


def norm_url(u):
    """Одна и та же модель — одна ссылка: без «#…», а у сайтов моделей и без «?…»."""
    u = u.rstrip(").,;!").split("#")[0]
    if link_site(u) in ("MakerWorld", "Printables", "Thingiverse", "Cults3D", "Thangs", "MyMiniFactory",
                        "Creality Cloud", "Sketchfab", "CGTrader", "3DToday", "Makeronline"):
        u = u.split("?")[0]
    return u.rstrip("/")


def link_line(e):
    date = time.strftime("%d.%m", time.gmtime(e.get("ts", 0) / 1000 + 3 * 3600))
    name = e.get("note") or e.get("title") or e.get("site")
    tags = " ".join("#" + t for t in e.get("tags", []))
    done = " ✅" if e.get("status") == "done" else ""
    return f"№{e['id']}{done} · {date} · {name[:70]} ({e.get('site')}) {tags}".rstrip() + f"\n{e['url']}"


def chat_send(chat_id, text):
    max_api("POST", "/messages", {"chat_id": chat_id, "disable_link_preview": "true"}, {"text": text[:3900]})


def save_links(chat_id, text, sender, mid):
    urls = list(dict.fromkeys(norm_url(u) for u in LINK_RE.findall(text)))
    tags = [t.lower() for t in re.findall(r"#([\wё]+)", LINK_RE.sub(" ", text))]   # #… внутри ссылок — не теги
    note = re.sub(r"\s+", " ", TAG_RE.sub("", LINK_RE.sub("", text))).strip(" -—:,.")[:120]
    saved = []
    def f(d):
        lst = d.setdefault("links", [])
        nxt = max([e.get("id", 0) for e in lst] + [0]) + 1
        known = {e["url"]: e for e in lst}
        for u in urls:
            if u in known:                      # уже есть — дополняем подпись и теги
                e = known[u]
                e["tags"] = list(dict.fromkeys(e.get("tags", []) + tags))
                if note and not e.get("note"):
                    e["note"] = note
                saved.append(("old", e))
                continue
            e = {"id": nxt, "url": u, "site": link_site(u), "title": link_title(u), "note": note, "tags": tags,
                 "ts": int(time.time() * 1000), "chat": chat_id, "mid": mid, "by": sender, "status": ""}
            lst.append(e)
            saved.append(("new", e))
            nxt += 1
    update_json("links.json", f, "Бот: ссылки " + ", ".join(urls)[:60], {"links": []})
    lines = [("🔖 №{} сохранена: " if kind == "new" else "🔁 №{} уже была, обновил: ").format(e["id"])
             + (e.get("note") or e.get("title") or e["site"])[:60] + (" " + " ".join("#" + t for t in e["tags"]) if e["tags"] else "")
             for kind, e in saved]
    if lines:
        chat_send(chat_id, "\n".join(lines))


def import_links(chat_id):
    """Забрать ссылки из старых сообщений чата (до 500 последних)."""
    msgs, oldest = [], None
    for _ in range(5):
        params = {"chat_id": chat_id, "count": 100}
        if oldest:
            params["to"] = oldest - 1
        batch = (max_api("GET", "/messages", params) or {}).get("messages") or []
        batch = [m for m in batch if not oldest or int(m.get("timestamp") or 0) < oldest]
        if not batch:
            break
        msgs += batch
        oldest = min(int(m.get("timestamp") or 0) for m in batch)
        if len(batch) < 100:
            break
    found = []
    for m in msgs:
        body = m.get("body") or {}
        t = body.get("text") or ""
        if (m.get("sender") or {}).get("is_bot") or not LINK_RE.search(t):
            continue
        found.append((int(m.get("timestamp") or 0), t, (m.get("sender") or {}).get("user_id"), body.get("mid")))
    found.sort()
    added = []
    def f(d):
        lst = d.setdefault("links", [])
        known = {e["url"] for e in lst}
        nxt = max([e.get("id", 0) for e in lst] + [0]) + 1
        for ts, t, by, mid in found:
            tags = [x.lower() for x in re.findall(r"#([\wё]+)", LINK_RE.sub(" ", t))]
            note = re.sub(r"\s+", " ", TAG_RE.sub("", LINK_RE.sub("", t))).strip(" -—:,.")[:120]
            for u in dict.fromkeys(norm_url(x) for x in LINK_RE.findall(t)):
                if u in known:
                    continue
                lst.append({"id": nxt, "url": u, "site": link_site(u), "title": link_title(u), "note": note,
                            "tags": tags, "ts": ts, "chat": chat_id, "mid": mid, "by": by, "status": ""})
                known.add(u)
                added.append(nxt)
                nxt += 1
    if found:
        update_json("links.json", f, f"Бот: импорт ссылок из чата ({len(found)} сообщ.)", {"links": []})
    return chat_send(chat_id, f"📥 Просмотрел {len(msgs)} сообщений, добавил ссылок: {len(added)}."
                     + (f" Номера №{added[0]}–№{added[-1]}." if added else "") + "\nИскать: найти дракон")


def find_links(links, query):
    q = query.lower().strip()
    if not q:
        return links
    if q in ("готово", "напечатано", "done"):
        return [e for e in links if e.get("status") == "done"]
    if q in ("план", "в планах", "todo", "не готово"):
        return [e for e in links if e.get("status") != "done"]
    words = q.split()
    def hay(e):
        return " ".join([e.get("title", ""), e.get("note", ""), e.get("site", ""), e.get("url", ""),
                         " ".join("#" + t for t in e.get("tags", []))]).lower()
    return [e for e in links if all(w in hay(e) for w in words)]


def handle_group(msg, chat_id):
    body = msg.get("body") or {}
    text = (body.get("text") or "").strip()
    sender = (msg.get("sender") or {})
    if sender.get("is_bot") or not text:
        return
    low = text.lower()
    m = re.match(r"^(?:/найти|найти|/поиск|поиск|\?)\s*(.*)$", low, re.S)
    if m:
        data, _ = gh_file("links.json")
        res = find_links((data or {}).get("links", []), m.group(1))
        if not res:
            return chat_send(chat_id, f"🔍 По запросу «{m.group(1)}» ничего нет. /ссылки — последние, /теги — все теги.")
        res = sorted(res, key=lambda e: -e.get("ts", 0))
        return chat_send(chat_id, f"🔍 Найдено: {len(res)}\n\n" + "\n\n".join(link_line(e) for e in res[:12])
                         + ("\n\n…показаны 12 последних, уточните запрос" if len(res) > 12 else ""))
    if low.startswith("/ссылки") or low == "ссылки":
        data, _ = gh_file("links.json")
        q = text.split(None, 1)[1] if len(text.split(None, 1)) > 1 else ""
        res = sorted(find_links((data or {}).get("links", []), q), key=lambda e: -e.get("ts", 0))
        if not res:
            return chat_send(chat_id, "Ссылок пока нет." if not q else f"По «{q}» ничего нет.")
        return chat_send(chat_id, f"📋 Ссылок: {len(res)}" + (f" ({q})" if q else "") + "\n\n"
                         + "\n\n".join(link_line(e) for e in res[:12]))
    if low.startswith("/теги") or low == "теги":
        data, _ = gh_file("links.json")
        cnt = {}
        for e in (data or {}).get("links", []):
            for t in e.get("tags", []):
                cnt[t] = cnt.get(t, 0) + 1
        if not cnt:
            return chat_send(chat_id, "Тегов пока нет. Добавляйте #теги в подпись к ссылке или /тег 12 #дракон")
        return chat_send(chat_id, "🏷 Теги:\n" + "\n".join(f"#{t} — {n}" for t, n in sorted(cnt.items(), key=lambda x: -x[1])))
    if low.startswith("/тег"):
        no = num(text.split(None, 1)[1]) if len(text.split()) > 1 else None
        tags = [t.lower() for t in re.findall(r"#([\wё]+)", text)]
        if not no or not tags:
            return chat_send(chat_id, "Формат: /тег 12 #дракон #подарок")
        ok = []
        def f(d):
            for e in d.get("links", []):
                if e.get("id") == no:
                    e["tags"] = list(dict.fromkeys(e.get("tags", []) + tags))
                    ok.append(e)
        update_json("links.json", f, f"Бот: теги ссылки {no}", {"links": []})
        return chat_send(chat_id, f"🏷 №{no}: " + " ".join("#" + t for t in ok[0]["tags"]) if ok else f"Ссылки №{no} нет.")
    if re.match(r"^/(готово|напечатано|план)\b", low):
        no = num(text)
        status = "" if low.startswith("/план") else "done"
        ok = []
        def f(d):
            for e in d.get("links", []):
                if e.get("id") == no:
                    e["status"] = status
                    ok.append(e)
        if not no:
            return chat_send(chat_id, "Формат: /готово 12")
        update_json("links.json", f, f"Бот: статус ссылки {no}", {"links": []})
        return chat_send(chat_id, (f"✅ №{no} отмечена как напечатанная." if status else f"📝 №{no} снова в планах.") if ok else f"Ссылки №{no} нет.")
    if re.match(r"^/?удалить[ _]ссылк", low):
        no = num(text)
        ok = []
        def f(d):
            before = len(d.get("links", []))
            d["links"] = [e for e in d.get("links", []) if e.get("id") != no]
            ok.append(before != len(d["links"]))
        if not no:
            return chat_send(chat_id, "Формат: /удалить ссылку 12")
        update_json("links.json", f, f"Бот: удалена ссылка {no}", {"links": []})
        return chat_send(chat_id, f"🗑 №{no} удалена." if ok and ok[0] else f"Ссылки №{no} нет.")
    if low in ("/помощь", "/help", "/start", "помощь"):
        return chat_send(chat_id, LINKS_HELP)
    if low.startswith("/импорт"):
        return import_links(chat_id)
    if LINK_RE.search(text):
        return save_links(chat_id, text, sender.get("user_id"), body.get("mid"))


def handle_update(u):
    t = u.get("update_type")
    cfg = config()
    msg = u.get("message") or {}
    rec = msg.get("recipient") or {}

    # события канала → сразу обновляем сайт
    if t in ("message_created", "message_edited") and rec.get("chat_type") == "channel":
        if not rec.get("chat_id") or rec.get("chat_id") == cfg.get("channel"):
            run_sync()
        return
    if t == "message_removed":
        if u.get("chat_id") in (None, cfg.get("channel")):
            run_sync()
        return

    # групповые чаты (например «ДЛЯ ссылок…») — библиотека ссылок
    if t == "message_created" and rec.get("chat_type") == "chat" and rec.get("chat_id") != cfg.get("channel"):
        return handle_group(msg, rec.get("chat_id"))

    if t == "message_callback":
        cb = u.get("callback") or {}
        cuid = (cb.get("user") or {}).get("user_id")
        try:
            max_api("POST", "/answers", {"callback_id": cb.get("callback_id")}, {"notification": "Выполняю…"})
        except Exception as e:
            print("answer:", e)
        if cuid in cfg["owners"] and cb.get("payload"):
            _await.pop(cuid, None)
            return handle_owner(cuid, cb["payload"], [])
        if cuid and cb.get("payload"):
            return handle_customer(cuid, cb["payload"], (cb.get("user") or {}).get("first_name") or "")
        return

    uid = (msg.get("sender") or u.get("user") or {}).get("user_id")
    if uid not in cfg["owners"]:
        if not uid or rec.get("chat_type") not in (None, "dialog"):
            return
        user = msg.get("sender") or u.get("user") or {}
        name = user.get("first_name") or user.get("name") or ""
        if t == "bot_started":
            payload = u.get("payload") or ""
            if payload.startswith("inv_"):
                return accept_invite(uid, name, payload[4:])
            if payload.lower() in ("skidka", "discount", "promo"):
                return give_sub_discount(uid, name)
            nos, promo = parse_start(payload)
            if nos:
                return new_order(uid, user, nos, promo)
            return send_customer(uid, CUSTOMER_HELLO)
        if t != "message_created":
            return
        text = ((msg.get("body") or {}).get("text") or "").strip()
        sm = re.match(r"^/start\s+(\S+)", text)
        if sm and sm.group(1).startswith("inv_"):
            return accept_invite(uid, name, sm.group(1)[4:])
        if sm:                                          # ссылка открыта в уже начатом чате
            nos, promo = parse_start(sm.group(1))
            if nos:
                return new_order(uid, user, nos, promo)
            if sm.group(1).lower() == "skidka":
                return give_sub_discount(uid, name)
        is_admin = uid in {a["id"] for a in cfg.get("admins", [])}
        if is_admin and (text in MENU_BUTTONS or (text.startswith("/") and text.lower() not in ("/start", "/скидка", "/skidka"))):
            return deny(uid, cfg)               # админ без доступа пробует команды
        low = text.lower()
        if low in ("/start", "/skidka", "/скидка", "скидка", "получить скидку", "🎁 получить скидку", "/discount", "привет", "здравствуйте", ""):
            if not text and (msg.get("body") or {}).get("attachments"):
                return forward_to_owners(uid, user, msg)
            return handle_customer(uid, text, name)
        # вставили текст заказа с сайта («… (№48) … промокод: X») — оформляем заказ
        nos = [int(x) for x in re.findall(r"№\s*(\d+)", text)][:30]
        if nos and re.search(r"заказ", low):
            pm = re.search(r"промокод:\s*([^\s,.;]+)", text, re.I)
            return new_order(uid, user, nos, pm.group(1) if pm else "")
        forward_to_owners(uid, user, msg)               # остальное — мастеру
        return send_customer(uid, "✉️ Передал мастеру — он ответит здесь же.") if not _recent_ack(uid) else None
    if t == "bot_started":
        return send(uid, HELP)
    if t == "message_created" and rec.get("chat_type") == "dialog":
        body = msg.get("body") or {}
        images = [a for a in body.get("attachments") or [] if a.get("type") == "image"]
        return handle_owner(uid, (body.get("text") or "").strip(), images)


# ---------------------------------------------------------------- настройка через браузер

def setup_page(func_url, action):
    out = []

    def row(ok, title, detail=""):
        out.append(f"<tr><td>{'✅' if ok else '❌'}</td><td><b>{html.escape(title)}</b><br>"
                   f"<small>{html.escape(str(detail))}</small></td></tr>")

    try:
        me = max_api("GET", "/me")
        row(True, "Токен MAX", f"бот: {me.get('name') or me.get('first_name')} (@{me.get('username')})")
    except Exception as e:
        row(False, "Токен MAX", e)
    try:
        r = gh("GET", f"/repos/{REPO}")
        row(bool(r.get("permissions", {}).get("push", True)), "Токен GitHub", f"репозиторий {r.get('full_name')}")
        cfg = config()
        row(bool(cfg["owners"]), "Владельцы бота", f"{len(cfg['owners'])} чел. из админов канала")
        row(bool(cfg["channel"]), "Канал", cfg["channel"])
    except Exception as e:
        row(False, "Токен GitHub", e)
    try:
        if action == "setup":
            max_api("POST", "/subscriptions", None, {"url": func_url, "update_types": UPDATE_TYPES, "secret": SECRET})
        elif action == "off":
            max_api("DELETE", "/subscriptions", {"url": func_url})
        subs = max_api("GET", "/subscriptions").get("subscriptions") or []
        mine = [s for s in subs if s.get("url") == func_url]
        if action == "off":
            row(not mine, "Webhook отключён", "бот снова работает через GitHub (с задержкой)")
        else:
            row(bool(mine), "Webhook подключён", func_url if mine else f"подписки: {subs}")
    except Exception as e:
        row(False, "Webhook", e)
    page = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
            "<title>Настройка бота</title><body style='font-family:system-ui;max-width:640px;margin:30px auto;padding:0 16px'>"
            "<h2>Настройка мгновенного бота</h2><table cellpadding=8>" + "".join(out) + "</table>"
            "<p>Если всё ✅ — напишите боту в MAX <b>/меню</b>, он должен ответить сразу.</p></body>")
    return {"statusCode": 200, "headers": {"Content-Type": "text/html; charset=utf-8"}, "body": page}


_sub_checked = [0.0]


def ensure_subscription(func_url):
    """Раз в час проверяем, что вебхук получает все нужные события (например, нажатия кнопок)."""
    if time.time() - _sub_checked[0] < 3600:
        return
    _sub_checked[0] = time.time()
    try:
        subs = max_api("GET", "/subscriptions").get("subscriptions") or []
        mine = next((x for x in subs if (x.get("url") or "").rstrip("/") == func_url.rstrip("/")), None)
        if mine and set(UPDATE_TYPES) - set(mine.get("update_types") or UPDATE_TYPES):
            max_api("POST", "/subscriptions", None, {"url": mine["url"], "update_types": UPDATE_TYPES, "secret": SECRET})
            print("subscription: добавлены события", set(UPDATE_TYPES) - set(mine.get("update_types") or []))
    except Exception as e:
        print("subscription check:", e)


def handler(event, context):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    q = event.get("queryStringParameters") or {}
    func_url = os.environ.get("FUNC_URL") or f"https://functions.yandexcloud.net/{context.function_name}"

    # отзыв покупателя и проверка промокода с сайта (публичный адрес, без секрета)
    if "review" in q:
        return site_review(event, headers)
    if "promo" in q:
        return site_promo_check(event, headers)
    if "app" in q:
        return site_app(event, headers)

    # настройка из браузера: ?setup=SECRET (подключить), ?off=SECRET (отключить), ?check=SECRET
    for action in ("setup", "off", "check"):
        if q.get(action):
            if not SECRET or q[action] != SECRET:
                return {"statusCode": 403, "body": "wrong secret"}
            return setup_page(func_url, action)

    if not SECRET or headers.get("x-max-bot-api-secret") != SECRET:
        return {"statusCode": 403, "body": "forbidden"}
    ensure_subscription(func_url)
    try:
        raw = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode()
        handle_update(json.loads(raw))
    except Exception as e:  # всегда 200, иначе MAX будет повторять событие
        print("error:", repr(e))
    return {"statusCode": 200, "body": "ok"}

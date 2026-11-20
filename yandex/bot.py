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
UPDATE_TYPES = ["message_created", "message_edited", "message_removed", "bot_started"]
UA = "model3d-bot-yandex"

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
    "📸 Новый пост": "/пост", "❓ Помощь": "/помощь", "📝 Шаблон": "/шаблон",
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
    ["📸 Новый пост", "📝 Шаблон"],
    ["👥 Доступ", "❓ Помощь"],
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
/доступ — кто из админов может пользоваться ботом (только для владельца)
/шаблон — шаблон поста для канала
/отзыв текст (+ фото) — добавить отзыв на сайт, /отзывы — список

📸 Публикация: пришлите фото с подписью
/пост Дракон-антистресс
Цена: 200
#антистресс

Номер товара (№) виден на сайте в карточке товара."""


def keyboard():
    rows = [[{"type": "message", "text": t} for t in r] for r in MENU_ROWS]
    rows.append([{"type": "link", "text": "🌐 Открыть сайт", "url": SITE_URL}])
    return {"type": "inline_keyboard", "payload": {"buttons": rows}}


def send(uid, text, menu=True):
    body = {"text": text[:3900]}
    if menu:
        body["attachments"] = [keyboard()]
    max_api("POST", "/messages", {"user_id": uid, "disable_link_preview": "true"}, body)


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


def metrika_text(days=7):
    """Сводка Метрики: посетители, визиты, источники, цели (нажатия «Заказать» и т.п.)."""
    mid = os.environ.get("METRIKA_ID", "").strip() or METRIKA_DEFAULT_ID
    tok = os.environ.get("METRIKA_TOKEN", "").strip()
    if not tok:
        return ""
    period = {"date1": f"{days}daysAgo", "date2": "today", "ids": mid, "accuracy": "full"}
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
                                       "filters": "ym:s:UTMSource=='flyer'"}, tok)
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
    allowed = set((access or {}).get("allowed", []))
    full = set((access or {}).get("full", []))     # админы с полными правами — как у владельца
    chief = {a["id"] for a in admins if a["owner"]}
    real = set(chief)
    if not admins:                                 # MAX не ответил — берём последний известный список
        owners = set((state or {}).get("owners", []))
        chief = set((state or {}).get("chief", [])) or owners
    elif not chief:                                # MAX не сообщил, кто владелец — пускаем всех админов, как раньше
        chief = {a["id"] for a in admins}
        owners = chief
    else:
        ids = {a["id"] for a in admins}
        chief = chief | (full & ids)
        owners = chief | (allowed & ids)
    if not admins:
        chief |= full; owners |= full | allowed
    _cache.update(ts=time.time(), owners=owners, chief=chief, real=real, full=full,
                  admins=admins, allowed=allowed, channel=ch)
    return _cache


_asked = {}


def access_text(cfg):
    lines = []
    for k, a in enumerate(cfg["admins"], 1):
        mark = "👑 владелец" if a["owner"] and a["id"] in cfg.get("real", ()) else (
            "⭐ полные права" if a["id"] in cfg.get("full", ()) else
            "✅ есть доступ" if a["id"] in cfg["owners"] else "🚫 нет доступа")
        lines.append(f"{k}. {a['name']} — {mark}")
    if not lines:
        return "Не удалось получить список админов канала. Попробуйте позже."
    return ("👥 Админы канала и доступ к боту:\n" + "\n".join(lines) +
            "\n\nДать доступ: /разрешить 2\nЗабрать: /запретить 2\n(число — номер в этом списке)\n\n"
            "⭐ Полные права — всё как у владельца, включая выдачу доступа.\n"
            "Без доступа админ не может пользоваться ботом: команды, продажи, цены, отзывы.")


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
    if target["owner"] and target["id"] in cfg.get("real", ()):
        return send(uid, "У владельца доступ есть всегда.")
    if not allow and target["id"] in cfg.get("full", ()) and uid not in cfg.get("real", ()):
        return send(uid, "Снять полные права может только владелец канала.")
    def f(d):
        s = set(d.get("allowed", []))
        (s.add if allow else s.discard)(target["id"])
        d["allowed"] = sorted(s)
        if not allow:
            d["full"] = sorted(set(d.get("full", [])) - {target["id"]})
    update_json("access.json", f, f"Бот: доступ {'выдан' if allow else 'снят'}", {"allowed": []})
    _cache.clear()
    try:
        send(target["id"], "✅ Владелец канала дал вам доступ к боту сайта. Напишите /меню." if allow
             else "🚫 Владелец канала закрыл вам доступ к боту сайта.", menu=allow)
    except Exception as e:
        print("notify admin:", e)
    return send(uid, (f"✅ {target['name']} теперь может пользоваться ботом." if allow
                      else f"🚫 {target['name']} больше не может пользоваться ботом.") + "\n\n" + access_text(config()))


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
        rid = max([r.get("id", 0) for r in d.get("reviews", [])] + [0]) + 1
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
    if not revs:
        return send(uid, "Отзывов из бота пока нет.\n\n" + REVIEW_HELP)
    lines = [f"№{r['id']} — {time.strftime('%d.%m', time.gmtime(r['ts'] / 1000 + 3 * 3600))}: "
             f"{(r.get('text') or '(без текста)')[:60]}{' 📷' if r.get('raw') else ''}" for r in revs[-15:]]
    return send(uid, "🗂 Отзывы на сайте (из бота):\n" + "\n".join(lines) + "\n\nУбрать: /удалить отзыв 3")


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
            {"id": nxt + k, "no": no, "qty": q, "ts": batch, "b": batch} for k, (no, q) in enumerate(good))
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
    return send(uid, msg + "\n\nОшиблись — /отменить (отменит всю эту пачку). Можно присылать следующие номера.")


def handle_owner(uid, text, images):
    if text in MENU_BUTTONS:
        text = MENU_BUTTONS[text]
    low = text.lower()
    if low.startswith("/отзыв") and not low.startswith("/отзывы") or low.startswith("отзыв:") or "#отзыв" in low:
        return add_review(uid, text.replace("#отзыв", "").replace("#Отзыв", ""), images)
    if low in ("/отзывы", "отзывы"):
        return list_reviews(uid)
    if re.match(r"^/?удалить[ _]отзыв", low):
        return delete_review(uid, text)
    if low in ("/доступ", "доступ", "/admins"):
        cfg = config()
        if uid not in cfg["chief"]:
            return send(uid, "Список доступов видит только владелец канала и админы с полными правами.")
        return send(uid, access_text(cfg))
    if low.startswith("/разрешить"):
        return change_access(uid, text, True)
    if low.startswith("/запретить"):
        return change_access(uid, text, False)
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
            return send(uid, f"🖼 «{item['title']}»: сейчас главное — фото {cur}.\n"
                             f"Напишите, например: /обложка {no} 2")
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

    uid = (msg.get("sender") or u.get("user") or {}).get("user_id")
    if uid not in cfg["owners"]:
        if uid in {a["id"] for a in cfg.get("admins", [])} and rec.get("chat_type") in (None, "dialog"):
            deny(uid, cfg)
        return  # посторонние — молчим (покупателей бот пока не обслуживает)
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


def handler(event, context):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    q = event.get("queryStringParameters") or {}
    func_url = os.environ.get("FUNC_URL") or f"https://functions.yandexcloud.net/{context.function_name}"

    # настройка из браузера: ?setup=SECRET (подключить), ?off=SECRET (отключить), ?check=SECRET
    for action in ("setup", "off", "check"):
        if q.get(action):
            if not SECRET or q[action] != SECRET:
                return {"statusCode": 403, "body": "wrong secret"}
            return setup_page(func_url, action)

    if not SECRET or headers.get("x-max-bot-api-secret") != SECRET:
        return {"statusCode": 403, "body": "forbidden"}
    try:
        raw = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode()
        handle_update(json.loads(raw))
    except Exception as e:  # всегда 200, иначе MAX будет повторять событие
        print("error:", repr(e))
    return {"statusCode": 200, "body": "ok"}

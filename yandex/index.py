"""
Мгновенный бот сайта «3D модели Нагаево» — функция Yandex Cloud Functions (Python 3.12).

MAX присылает сюда каждое событие (webhook):
  • сообщение владельца боту → команда выполняется сразу, ответ за 1–2 секунды;
  • новый / изменённый / удалённый пост в канале → сразу запускается обновление сайта на GitHub.

Каталог, отчёты и уведомления по-прежнему делает GitHub (scripts/sync_max.py).

Переменные окружения функции:
  MAX_TOKEN     — токен бота MAX
  GITHUB_TOKEN  — токен GitHub (доступ к репозиторию: Contents и Actions — Read and write)
  SECRET        — придуманный пароль из латинских букв и цифр (16+ символов)
  REPO          — необязательно, по умолчанию 3dmodelNagaevo/model3d-nagaevo

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
SITE_URL = os.environ.get("SITE_URL", "https://3dmodelnagaevo.github.io/model3d-nagaevo/").rstrip("/") + "/"
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


def update_overrides(fn, message):
    """Правка overrides.json с повтором при конфликте."""
    for _ in range(3):
        ov, sha = gh_file("overrides.json")
        ov = ov or {}
        fn(ov)
        try:
            gh_put("overrides.json", ov, sha, message)
            return
        except urllib.error.HTTPError as e:
            if e.code != 409:
                raise
            time.sleep(1)
    raise RuntimeError("не удалось сохранить изменения (конфликт)")


def run_sync():
    """Запустить обновление сайта на GitHub прямо сейчас."""
    gh("POST", f"/repos/{REPO}/actions/workflows/sync.yml/dispatches", {"ref": "main"})


# ---------------------------------------------------------------- меню и тексты

MENU_BUTTONS = {
    "📊 Статус": "/статус", "🔥 Топ недели": "/топ", "📈 Статистика": "/стата",
    "🙈 Скрыть товар": "/скрыть", "👁 Вернуть товар": "/показать",
    "💰 Изменить цену": "/цена", "📸 Новый пост": "/пост", "❓ Помощь": "/помощь",
    "🔄 Обновить сайт": "/обновить",
}
MENU_ROWS = [
    ["📊 Статус", "🔥 Топ недели", "📈 Статистика"],
    ["🙈 Скрыть товар", "👁 Вернуть товар"],
    ["💰 Изменить цену", "📸 Новый пост"],
    ["🔄 Обновить сайт", "❓ Помощь"],
]
EN = {"/menu": "/меню", "/status": "/статус", "/top": "/топ", "/stats": "/стата", "/help": "/помощь", "/start": "/помощь"}

HELP = """🤖 Бот сайта. Кнопки — под этим сообщением, или пишите команды:

/статус — сколько товаров на сайте
/топ — популярное за неделю
/стата — статистика за неделю
/скрыть 12 — убрать товар №12 с сайта (пост в канале остаётся)
/показать 12 — вернуть товар на сайт
/цена 12 300 — поменять цену на сайте
/цена 12 сброс — вернуть цену из поста
/обновить — обновить сайт прямо сейчас

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
    rows = sorted([i for i in items if i.get(key) and not i.get("hidden")], key=lambda i: -i[key])[:n]
    if not rows and key != "views":
        return top_text(items, "views", "за всё время", n)
    if not rows:
        return "🔥 Пока нет данных о просмотрах."
    return f"🔥 Популярное {label}:\n" + "\n".join(f"{k}. №{i['no']} {i['title']} — {i[key]} просм." for k, i in enumerate(rows, 1))


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


def stats_text(items):
    week = 7 * 86400 * 1000
    now = time.time() * 1000
    new = [i for i in items if i.get("source") == "max" and now - i.get("ts", 0) < week]
    views = sum(i.get("views_week", 0) for i in items)
    return (f"🆕 Новых товаров за неделю: {len(new)}\n👁 Просмотров постов-товаров за неделю: {views}\n"
            f"📦 Всего на сайте: {len([i for i in items if not i.get('hidden')])}\n\n" + top_text(items))


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


def config():
    """Владельцы и id канала — из репозитория (кэш на 10 минут)."""
    if _cache.get("ts", 0) > time.time() - 600:
        return _cache
    state, _ = gh_file("state.json")
    ch = None
    try:
        d = gh("GET", f"/repos/{REPO}/contents/channel_id.txt?ref=main")
        ch = int(base64.b64decode(d["content"]).decode().strip())
    except Exception as e:
        print("channel:", e)
    _cache.update(ts=time.time(), owners=set((state or {}).get("owners", [])), channel=ch)
    return _cache


def handle_owner(uid, text, images):
    if text in MENU_BUTTONS:
        text = MENU_BUTTONS[text]
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
        return send(uid, stats_text(catalog_items()))
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

    uid = (msg.get("sender") or u.get("user") or {}).get("user_id")
    if uid not in cfg["owners"]:
        return  # не владелец — молчим (покупателей бот пока не обслуживает)
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

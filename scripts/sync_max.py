#!/usr/bin/env python3
"""
Синхронизация каталога сайта с постами канала MAX.

Что делает:
  1. Берёт последние посты канала через Bot API MAX (бот должен быть админом канала).
  2. Находит посты-товары: с хэштегом #каталог ИЛИ со строкой «Стоимость:» / «Цена:».
     Пост с #нетнасайте никогда не попадает на сайт.
  3. Достаёт название, цену, размер, цвет, описание, категорию и фото.
  4. Обновляет catalog.json и папку photos/. Ручные товары (source != "max") не трогает.

Переменные окружения:
  MAX_TOKEN       — токен бота (обязательно, хранится в секретах GitHub)
  MAX_CHANNEL_ID  — id канала (необязательно: если не задан, бот найдёт канал сам)

Только стандартная библиотека Python, ничего устанавливать не нужно.
"""
import json
import os
import re
import ssl
import sys
import time
import pathlib
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
CATALOG = ROOT / "catalog.json"
PHOTOS = ROOT / "photos"
CERTS = ROOT / ".certs"

API_HOSTS = ["https://platform-api2.max.ru", "https://platform-api.max.ru"]
FETCH_COUNT = 100          # сколько последних постов смотреть (максимум API — 100)
MAX_PHOTOS = 8             # сколько фото брать из одного поста

# Хэштег → категория на сайте
HASHTAG_CATEGORIES = {
    "авто": "Авто", "машины": "Авто", "машина": "Авто",
    "антистресс": "Игрушки и антистресс", "игрушки": "Игрушки и антистресс", "игрушка": "Игрушки и антистресс",
    "фигурки": "Фигурки и декор", "фигурка": "Фигурки и декор", "декор": "Фигурки и декор",
    "дом": "Для дома", "длядома": "Для дома", "полезное": "Для дома",
    "подарки": "Подарки", "подарок": "Подарки",
    "геймерам": "Для геймеров", "игры": "Для геймеров",
}
# Если хэштега категории нет — угадываем по словам в названии/описании
KEYWORD_CATEGORIES = [
    ("Авто", r"bmw|бмв|mercedes|мерседес|audi|ауди|porsche|порш|toyota|nissan|lada|лада|машин|автомобил|\bавто"),
    ("Для геймеров", r"геймпад|джойстик|playstation|xbox|nintendo|геймер|контроллер"),
    ("Игрушки и антистресс", r"антистресс|спиннер|spinner|фиджет|fidget|игрушк|волчок|катан|меч|дракон|ящериц|осьминог|слизень"),
    ("Фигурки и декор", r"фигурк|статуэт|декор|ваз|бюст|кашпо|горшок|светильник|ночник|лампа"),
    ("Для дома", r"подставк|держател|органайзер|крючок|кронштейн|полк|для дома|кухн|ванн"),
    ("Подарки", r"подар|сувенир|брелок|магнит"),
]
DEFAULT_CATEGORY = "Разное"

FIELD_RE = re.compile(r"^(размер|цвет|стоимость|цена|материал)\s*[:\-—–]\s*(.*)$", re.I)
HASHTAG_RE = re.compile(r"#([\wё]+)", re.I)
BULLET_RE = re.compile(r"^[\s\-–—•*·▪️▫️🔹🔸✅➖➡️👉]+")


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- HTTP

def ssl_context():
    """Системные сертификаты + сертификаты Минцифры (нужны для platform-api2.max.ru)."""
    ctx = ssl.create_default_context()
    if CERTS.is_dir():
        for f in sorted(CERTS.glob("*.crt")):
            try:
                ctx.load_verify_locations(cafile=str(f))
            except Exception as e:  # битый файл не должен ломать запуск
                log(f"  ! не удалось загрузить сертификат {f.name}: {e}")
    return ctx


CTX = ssl_context()


class ApiError(Exception):
    pass


def api_get(path, params, token):
    """GET к Bot API MAX. Пробует новый домен, при сетевой/SSL ошибке — старый."""
    query = urllib.parse.urlencode(params)
    last_err = None
    for host in API_HOSTS:
        url = f"{host}{path}?{query}" if query else f"{host}{path}"
        req = urllib.request.Request(url, headers={"Authorization": token, "User-Agent": "model3d-catalog-sync"})
        try:
            with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            if e.code == 401:
                raise ApiError("MAX ответил 401: неверный или перевыпущенный токен. Обновите секрет MAX_TOKEN.")
            if e.code == 403:
                raise ApiError("MAX ответил 403: у бота нет доступа к каналу. Проверьте, что бот — администратор канала.")
            last_err = f"{host}: HTTP {e.code} {body}"
        except Exception as e:  # SSL, DNS, таймаут — пробуем следующий домен
            last_err = f"{host}: {e}"
        log(f"  ! {last_err}")
    raise ApiError(f"Не удалось обратиться к API MAX. Последняя ошибка: {last_err}")


def download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "model3d-catalog-sync"})
    with urllib.request.urlopen(req, timeout=60, context=CTX) as r:
        data = r.read()
    if len(data) < 200:
        raise ValueError("слишком маленький файл")
    dest.write_bytes(data)


# ---------------------------------------------------------------- MAX

CHANNEL_FILE = ROOT / "channel_id.txt"


def find_channel(token):
    """Ищет id канала. GET /chats с июня 2026 не работает, поэтому:
    1) MAX_CHANNEL_ID из настроек GitHub; 2) сохранённый channel_id.txt;
    3) события бота (GET /updates): добавление в канал и новые посты."""
    env = os.environ.get("MAX_CHANNEL_ID", "").strip()
    if env:
        return int(env)
    if CHANNEL_FILE.exists():
        saved = CHANNEL_FILE.read_text().strip()
        if saved:
            return int(saved)

    data = api_get("/updates", {"limit": 100, "timeout": 0}, token)
    found = {}
    for u in data.get("updates") or []:
        msg = u.get("message") or {}
        rec = msg.get("recipient") or {}
        if rec.get("chat_type") == "channel" and rec.get("chat_id"):
            found[rec["chat_id"]] = "пост в канале"
        if u.get("update_type") in ("bot_added", "chat_title_changed") and u.get("chat_id"):
            if u.get("is_channel", True):
                found.setdefault(u["chat_id"], u.get("update_type"))
    if not found:
        raise ApiError(
            "Не удалось определить канал. Выложите в канал любой пост (можно удалить потом) "
            "и запустите снова — или укажите id канала в переменной MAX_CHANNEL_ID.")
    for cid, why in found.items():
        log(f"  найден чат {cid} ({why})")
    # проверяем, что это канал и бот в нём админ
    for cid in found:
        try:
            info = api_get(f"/chats/{cid}", {}, token)
        except ApiError as e:
            log(f"  ! {cid}: {e}")
            continue
        if info.get("type") == "channel":
            log(f"  канал: {info.get('title')!r} id={cid}")
            CHANNEL_FILE.write_text(f"{cid}\n")
            return cid
    raise ApiError("Бот видит чаты, но не канал. Проверьте, что бот — администратор канала.")


def fetch_posts(token, chat_id):
    data = api_get("/messages", {"chat_id": chat_id, "count": FETCH_COUNT}, token)
    msgs = data.get("messages") or []
    # комментарии к постам приходят с post_id — их пропускаем
    return [m for m in msgs if not (m.get("recipient") or {}).get("post_id")]


# ---------------------------------------------------------------- разбор поста

def clean_value(v):
    v = v.strip().strip("()[]").strip()
    v = re.sub(r"\s+", " ", v)
    return v[:1].upper() + v[1:] if v else v


def parse_price(v):
    m = re.search(r"\d[\d\s]*", v or "")
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(0))
    return int(digits) if digits else None


def guess_category(tags, text):
    for t in tags:
        if t in HASHTAG_CATEGORIES:
            return HASHTAG_CATEGORIES[t]
    low = text.lower()
    for cat, pattern in KEYWORD_CATEGORIES:
        if re.search(pattern, low):
            return cat
    return DEFAULT_CATEGORY


def parse_post(msg):
    """Пост → товар (dict) или None, если пост — не товар."""
    body = msg.get("body") or {}
    text = (body.get("text") or "").strip()
    if not text:
        return None
    tags = [t.lower() for t in HASHTAG_RE.findall(text)]
    if any(t in ("нетнасайте", "скрыть", "несайт") for t in tags):
        return None

    lines = []
    for raw in text.splitlines():
        line = HASHTAG_RE.sub("", raw)
        line = BULLET_RE.sub("", line).strip()
        if line:
            lines.append(line)

    fields, rest = {}, []
    for line in lines:
        m = FIELD_RE.match(line)
        if m:
            key = m.group(1).lower()
            key = "price" if key in ("стоимость", "цена") else {"размер": "size", "цвет": "color", "материал": "material"}[key]
            fields[key] = m.group(2).strip()
        else:
            rest.append(line)

    is_product = "каталог" in tags or "catalog" in tags or "price" in fields
    if not is_product or not rest:
        return None

    title = re.sub(r"^[^\w«\"(]+", "", rest[0]).strip(" .:") or rest[0]
    desc = " ".join(rest[1:]).strip()

    photos = []
    for att in body.get("attachments") or []:
        if att.get("type") == "image":
            url = (att.get("payload") or {}).get("url")
            if url:
                photos.append(url)

    return {
        "mid": str(body.get("mid")),
        "ts": int(msg.get("timestamp") or 0),
        "title": title[:120],
        "category": guess_category(tags, title + " " + desc),
        "price": parse_price(fields.get("price")),
        "size": clean_value(fields.get("size") or "") or "По желанию",
        "color": clean_value(fields.get("color") or "") or "Согласовываем в ЛС",
        "material": clean_value(fields.get("material") or ""),
        "desc": desc[:600],
        "tags": " ".join(tags),
        "url": ((msg.get("stat") or {}).get("url")) or "",
        "_photo_urls": photos[:MAX_PHOTOS],
    }


# ---------------------------------------------------------------- каталог

def safe_name(s):
    return re.sub(r"[^\w\-]", "_", s)[:60]


def load_catalog():
    if CATALOG.exists():
        data = json.loads(CATALOG.read_text("utf-8"))
        return data.get("items", []) if isinstance(data, dict) else data
    return []


def sync(messages, old_items, downloader=download):
    """Чистая логика синхронизации. Возвращает новый список товаров."""
    manual = [i for i in old_items if i.get("source") != "max"]
    old_max = {i["mid"]: i for i in old_items if i.get("source") == "max"}
    next_no = max([i.get("no", 0) for i in old_items] + [0]) + 1

    PHOTOS.mkdir(exist_ok=True)
    fresh = {}
    for msg in messages:
        item = parse_post(msg)
        if not item:
            continue
        urls = item.pop("_photo_urls")
        old = old_max.get(item["mid"])
        photos = []
        for n, url in enumerate(urls):
            fname = f"{safe_name(item['mid'])}_{n}.jpg"
            dest = PHOTOS / fname
            if not dest.exists():
                try:
                    downloader(url, dest)
                except Exception as e:
                    log(f"  ! фото не скачалось ({item['title']}): {e}")
                    continue
            photos.append(f"photos/{fname}")
        if not photos and old:
            photos = old.get("photos", [])
        item["photos"] = photos
        item["source"] = "max"
        if old:
            item["no"] = old["no"]
        else:
            item["no"] = next_no
            next_no += 1
            log(f"  + новый товар №{item['no']}: {item['title']}")
        fresh[item["mid"]] = item

    # Товары, которых нет в свежей выборке:
    #   - пост старше окна выборки → оставляем (просто давно выложен);
    #   - пост внутри окна → его удалили или убрали из каталога → удаляем с сайта.
    window_full = len(messages) >= FETCH_COUNT
    oldest_ts = min((int(m.get("timestamp") or 0) for m in messages), default=0)
    for mid, old in old_max.items():
        if mid in fresh:
            continue
        if window_full and old.get("ts", 0) < oldest_ts:
            fresh[mid] = old
        else:
            log(f"  - убран с сайта: {old.get('title')}")

    max_items = sorted(fresh.values(), key=lambda i: i.get("ts", 0), reverse=True)
    items = max_items + manual

    # чистим фото, на которые больше никто не ссылается
    used = {p for i in items for p in i.get("photos", [])}
    for f in PHOTOS.glob("*.jpg"):
        if f"photos/{f.name}" not in used:
            f.unlink()
    return items


def main():
    token = os.environ.get("MAX_TOKEN", "").strip()
    if not token:
        log("Нет MAX_TOKEN. Добавьте токен бота в Settings → Secrets and variables → Actions.")
        return 1
    had_channel_file = CHANNEL_FILE.exists()
    try:
        chat_id = find_channel(token)
        log(f"Читаю посты канала {chat_id}…")
        messages = fetch_posts(token, chat_id)
    except ApiError as e:
        log(f"ОШИБКА: {e}")
        return 1
    log(f"  получено постов: {len(messages)}")

    old_items = load_catalog()
    items = sync(messages, old_items)

    new_json = json.dumps({"updated": int(time.time()), "items": items}, ensure_ascii=False, indent=2)
    old_cmp = json.dumps(old_items, ensure_ascii=False, sort_keys=True)
    changed = json.dumps(items, ensure_ascii=False, sort_keys=True) != old_cmp
    if changed:
        CATALOG.write_text(new_json + "\n", "utf-8")
    log(f"Готово. Товаров на сайте: {len(items)} (из канала: {sum(1 for i in items if i.get('source') == 'max')}). "
        f"{'Каталог обновлён.' if changed else 'Изменений нет.'}")

    if not had_channel_file and CHANNEL_FILE.exists():
        changed = True  # сохранить найденный id канала, чтобы не искать заново
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"changed={'true' if changed else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

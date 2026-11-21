#!/usr/bin/env python3
"""
Синхронизация сайта с каналом MAX + бот для владельца.

Каждый запуск (раз в ~15 минут, GitHub Actions):
  1. Читает личные сообщения боту от владельца канала и выполняет команды
     (/скрыть, /цена, /пост …) — см. scripts/bot.py.
  2. Забирает все посты канала, находит товары (строка «Стоимость:»/«Цена:» или #каталог)
     и отзывы (#отзыв). #нетнасайте — пост не попадает на сайт.
  3. Обновляет catalog.json, фото (сжатые + миниатюры), страницы товаров m/N.html.
  4. Считает просмотры постов за неделю/месяц (для «Популярного»).
  5. Шлёт владельцу уведомления и раз в неделю — отчёт.

Переменные окружения:
  MAX_TOKEN       — токен бота (секрет GitHub, обязательно)
  MAX_CHANNEL_ID  — id канала (необязательно, найдётся сам и сохранится в channel_id.txt)
  OWNER_IDS       — id владельцев через запятую (необязательно, берутся из админов канала)
  SITE_URL        — адрес сайта (для превью ссылок и QR)
  METRIKA_ID / METRIKA_TOKEN — Яндекс Метрика для отчётов (необязательно)
"""
import datetime as dt
import json
import os
import re
import sys
import time

import maxapi
from maxapi import ApiError, log, ROOT
import photos as photolib
import promo
import outbox

CATALOG = ROOT / "catalog.json"
STATE = ROOT / "state.json"
OVERRIDES = ROOT / "overrides.json"   # /скрыть и /цена из бота (правят и GitHub, и Yandex Cloud)
SALES = ROOT / "sales.json"           # отметки «/продано» из бота
REVIEWS = ROOT / "reviews.json"       # отзывы, присланные боту (/отзыв)
PROMOS = ROOT / "promos.json"         # промокоды (управление — в боте)
PHOTOS = ROOT / "photos"
CHANNEL_FILE = ROOT / "channel_id.txt"
SITE_URL = (os.environ.get("SITE_URL") or "https://3dnagaevo.ru/").rstrip("/") + "/"

FETCH_COUNT = 100          # постов за один запрос (максимум API)
MAX_PAGES = 30             # до 3000 постов истории
MAX_PHOTOS = 8             # фото с одного поста
VIEWS_REFRESH_SEC = 3 * 3600   # просмотры обновляем раз в 3 часа, чтобы не плодить коммиты
MSK = dt.timezone(dt.timedelta(hours=3))

HASHTAG_CATEGORIES = {
    "авто": "Авто", "машины": "Авто", "машина": "Авто",
    "антистресс": "Игрушки и антистресс", "игрушки": "Игрушки и антистресс", "игрушка": "Игрушки и антистресс",
    "фигурки": "Фигурки и декор", "фигурка": "Фигурки и декор", "декор": "Фигурки и декор",
    "дом": "Для дома", "длядома": "Для дома", "полезное": "Для дома",
    "подарки": "Подарки", "подарок": "Подарки",
    "геймерам": "Для геймеров", "игры": "Для геймеров",
    "набор": "Наборы", "наборы": "Наборы",
    "длядетей": "Игрушки и антистресс", "детям": "Игрушки и антистресс", "спиннер": "Игрушки и антистресс",
    "волчок": "Игрушки и антистресс", "фиджет": "Игрушки и антистресс",
    "интерьер": "Фигурки и декор", "статуэтка": "Фигурки и декор", "ваза": "Фигурки и декор",
    "длякухни": "Для дома", "органайзер": "Для дома", "подставка": "Для дома", "хозяйство": "Для дома",
    "сувенир": "Подарки", "сувениры": "Подарки", "брелок": "Подарки",
    "автомобили": "Авто", "модели_авто": "Авто", "моделиавто": "Авто",
}
KEYWORD_CATEGORIES = [
    ("Наборы", r"\bнабор|комплект"),
    ("Авто", r"bmw|бмв|mercedes|мерседес|audi|ауди|porsche|порш|toyota|nissan|lada|лада|машин|автомобил|\bавто"),
    ("Для геймеров", r"геймпад|джойстик|playstation|xbox|nintendo|геймер|контроллер"),
    ("Игрушки и антистресс", r"антистресс|спиннер|spinner|фиджет|fidget|игрушк|волчок|катан|меч|дракон|ящериц|осьминог|слизень|балансир"),
    ("Фигурки и декор", r"фигурк|статуэт|скульптур|декор|ваз|бюст|кашпо|горшок|светильник|ночник|лампа|шкатулк|кот|котён|котен|моаи|корабл|рим\b|замок|ангел"),
    ("Для дома", r"подставк|держател|органайзер|крючок|кронштейн|полк|для дома|кухн|ванн"),
    ("Подарки", r"подар|сувенир|брелок|магнит"),
]
DEFAULT_CATEGORY = "Разное"
HIDE_TAGS = {"нетнасайте", "скрыть", "несайт", "промокод"}
REVIEW_TAGS = {"отзыв", "отзывы"}

# цена прямо в заголовке: «Логопедический набор 3D — 1000 руб»
TITLE_PRICE_RE = re.compile(r"\s*[—–\-:|]?\s*(?:от\s*)?(\d[\d\s]{0,6}\d|\d)\s*(?:₽|руб(?:лей|\.)?|р\.?)(?=\s|$|[!.,)])", re.I)
FIELD_RE = re.compile(r"^(размер|цвет|стоимость|цена|материал)\s*[:\-—–]\s*(.*)$", re.I)
HASHTAG_RE = re.compile(r"#([\wё]+)", re.I)
BULLET_RE = re.compile(r"^[\s\-–—•*·▪️▫️🔹🔸✅➖➡️👉]+")


# ---------------------------------------------------------------- файлы

def load_json(path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return default


def dump_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")


def load_catalog():
    data = load_json(CATALOG, {})
    if isinstance(data, list):
        return data, []
    return data.get("items", []), data.get("reviews", [])


# ---------------------------------------------------------------- канал

def find_channel(token):
    env = os.environ.get("MAX_CHANNEL_ID", "").strip()
    if env:
        return int(env)
    if CHANNEL_FILE.exists() and CHANNEL_FILE.read_text().strip():
        return int(CHANNEL_FILE.read_text().strip())
    data = maxapi.get("/updates", {"limit": 100, "timeout": 0}, token)
    found = []
    for u in data.get("updates") or []:
        rec = (u.get("message") or {}).get("recipient") or {}
        if rec.get("chat_type") == "channel" and rec.get("chat_id"):
            found.append(rec["chat_id"])
        if u.get("update_type") == "bot_added" and u.get("chat_id"):
            found.append(u["chat_id"])
    for cid in dict.fromkeys(found):
        try:
            if maxapi.get(f"/chats/{cid}", {}, token).get("type") == "channel":
                CHANNEL_FILE.write_text(f"{cid}\n")
                return cid
        except ApiError as e:
            log(f"  ! {cid}: {e}")
    raise ApiError("Не удалось определить канал. Выложите в канал любой пост и запустите снова "
                   "или задайте переменную MAX_CHANNEL_ID.")


def fetch_posts(token, chat_id):
    """Все посты канала: листаем историю страницами по 100."""
    def page(**extra):
        data = maxapi.get("/messages", {"chat_id": chat_id, "count": FETCH_COUNT, **extra}, token)
        return [m for m in (data.get("messages") or []) if not (m.get("recipient") or {}).get("post_id")]

    seen, result = set(), []

    def add(msgs):
        new = [m for m in msgs if (m.get("body") or {}).get("mid") not in seen]
        for m in new:
            seen.add((m.get("body") or {}).get("mid"))
            result.append(m)
        return new

    batch = page()
    add(batch)
    pages, complete = 1, len(batch) < FETCH_COUNT
    while not complete and pages < MAX_PAGES:
        oldest = min(int(m.get("timestamp") or 0) for m in result)
        got = None
        for key in ("to", "from"):   # в документации неоднозначно — пробуем оба
            try:
                cand = page(**{key: oldest - 1})
            except ApiError as e:
                log(f"  ! листание ({key}): {e}")
                continue
            older = [m for m in cand if int(m.get("timestamp") or 0) < oldest]
            if older:
                got = (cand, add(older))
                break
        if not got or not got[1]:
            break
        pages += 1
        complete = len(got[0]) < FETCH_COUNT
    log(f"  просмотрено постов: {len(result)} (страниц: {pages}{', вся история' if complete else ''})")
    return result, complete


# ---------------------------------------------------------------- разбор поста

def clean_value(v):
    v = re.sub(r"\s+", " ", v.strip().strip("()[]").strip())
    return v[:1].upper() + v[1:] if v else v


def parse_price(v):
    m = re.search(r"\d[\d\s]*", v or "")
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(0))
    return int(digits) if digits else None


def maybe_post_reminder(notifier, state):
    """Если в канале нет новых постов больше суток — напомнить владельцам (раз в сутки, днём по Уфе)."""
    import datetime as _dt
    last = state.get("last_post_ts") or 0
    if not last or not notifier.owners:
        return
    now = time.time()
    hour = (_dt.datetime.utcnow() + _dt.timedelta(hours=5)).hour
    gap = now - last
    if gap < 86400 or not 11 <= hour < 21 or now - state.get("last_post_reminder", 0) < 86400:
        return
    days = int(gap // 86400)
    word = "день" if days % 10 == 1 and days % 100 != 11 else ("дня" if 2 <= days % 10 <= 4 and not 12 <= days % 100 <= 14 else "дней")
    notifier.send(kind="reminders", text=f"📸 В канале нет новых постов уже {days} {word}.\n\n"
                  "Подписчики и сайт ждут новинок — выложите что-нибудь: новую модель, процесс печати, отзыв покупателя "
                  "или промокод. Готовый образец поста — кнопка «📝 Шаблон», выложить через бота — «📸 Новый пост».")
    state["last_post_reminder"] = int(now)


def guess_category(tags, text):
    for t in tags:
        t = t.replace("_", "")                     # #Для_дома = #длядома
        if t in HASHTAG_CATEGORIES:
            return HASHTAG_CATEGORIES[t]
    low = text.lower()
    for cat, pattern in KEYWORD_CATEGORIES:
        if re.search(pattern, low):
            return cat
    return DEFAULT_CATEGORY


def split_post(text):
    """Текст поста → (хэштеги, поля {price,size,color,material}, прочие строки)."""
    tags = [t.lower().replace("ё", "е") for t in HASHTAG_RE.findall(text)]
    fields, rest = {}, []
    for raw in text.splitlines():
        line = BULLET_RE.sub("", HASHTAG_RE.sub("", raw)).strip()
        if not line:
            continue
        m = FIELD_RE.match(line)
        if m:
            key = m.group(1).lower()
            key = "price" if key in ("стоимость", "цена") else {"размер": "size", "цвет": "color", "материал": "material"}[key]
            fields[key] = m.group(2).strip()
        else:
            rest.append(line)
    return tags, fields, rest


URL_RE = re.compile(r"https?://\S+|(?:max\.ru|t\.me|vk\.com|wa\.me)/\S+", re.I)


def clean_desc(lines):
    """Описание для сайта: без ссылок и строк «Заказ: …» (на сайте свои кнопки заказа)."""
    out = []
    for line in lines:
        if re.match(r"^\W*(заказ|заказать|написать|пишите|контакты?|связь)\b", line, re.I) and URL_RE.search(line):
            continue
        line = URL_RE.sub("", line).strip(" :—–-")
        if line:
            out.append(line)
    return "\n".join(out).strip()


def image_urls(body):
    out = []
    for att in body.get("attachments") or []:
        if att.get("type") == "image":
            url = (att.get("payload") or {}).get("url")
            if url:
                out.append(url)
    return out


def classify(msg):
    """Пост → ("item", dict) | ("review", dict) | ("warn", title) | (None, None)."""
    body = msg.get("body") or {}
    text = (body.get("text") or "").strip()
    if not text:
        return None, None
    tags, fields, rest = split_post(text)
    if HIDE_TAGS & {t.replace("_", "") for t in tags}:
        return None, None
    base = {
        "mid": str(body.get("mid")),
        "ts": int(msg.get("timestamp") or 0),
        "url": msg.get("url") or (msg.get("stat") or {}).get("url") or "",   # публичная ссылка на пост
        "_photo_urls": image_urls(body)[:MAX_PHOTOS],
    }
    views = (msg.get("stat") or {}).get("views")
    if REVIEW_TAGS & {t.replace("_", "") for t in tags}:
        return "review", {**base, "text": " ".join(rest)[:800], "_photo_urls": base["_photo_urls"][:1]}
    # нет строки «Стоимость:», но цена есть в первой строке и есть фото — это тоже товар (наборы и т.п.)
    if "price" not in fields and rest and base["_photo_urls"]:
        m = TITLE_PRICE_RE.search(rest[0])
        if m:
            fields["price"] = m.group(1)
            rest[0] = (rest[0][:m.start()] + rest[0][m.end():]).strip(" —–-:|")
    is_product = "каталог" in tags or "catalog" in tags or "price" in fields
    if not is_product or not rest:
        if rest and base["_photo_urls"] and ({"size", "color"} & set(fields)):
            return "warn", rest[0][:80]          # похоже на товар, но без цены
        return None, None
    title = re.sub(r"^[^\w«\"(]+", "", rest[0]).strip(" .:") or rest[0]
    desc = clean_desc(rest[1:])
    item = {
        **base,
        "title": title[:120],
        "category": guess_category(tags, title + " " + desc),
        "price": parse_price(fields.get("price")),
        "size": clean_value(fields.get("size") or "") or "По желанию",
        "color": clean_value(fields.get("color") or "") or "Согласовываем в ЛС",
        "material": clean_value(fields.get("material") or ""),
        "desc": desc[:600],
        "tags": " ".join(tags),
    }
    if isinstance(views, int):
        item["views"] = views
    return "item", item


# ---------------------------------------------------------------- фото

def safe_name(s):
    return re.sub(r"[^\w\-]", "_", s)[:60]


def fetch_photos(item_or_review, old=None):
    urls = item_or_review.pop("_photo_urls")
    out = []
    for n, url in enumerate(urls):
        fname = f"{safe_name(item_or_review['mid'])}_{n}.jpg"
        dest = PHOTOS / fname
        if not dest.exists():
            try:
                photolib.save_photo(maxapi.download(url), dest)
            except Exception as e:
                log(f"  ! фото не скачалось ({item_or_review.get('title', 'отзыв')}): {e}")
                continue
        out.append(f"photos/{fname}")
    if not out and old:
        out = old.get("photos_orig") or old.get("photos", [])
    return out


# ---------------------------------------------------------------- синхронизация

def add_bot_reviews(reviews, bot_reviews):
    """Отзывы из бота: исходные фото из reviews/raw/ → сжатые копии в photos/."""
    for r in bot_reviews:
        photos = []
        for k, raw in enumerate(r.get("raw", [])[:4]):
            src = ROOT / raw
            dest = PHOTOS / f"rev_{r['id']}_{k}.jpg"
            if not dest.exists() and src.exists():
                try:
                    photolib.save_photo(src.read_bytes(), dest)
                except Exception as e:
                    log(f"  ! фото отзыва {r['id']}: {e}")
                    continue
            if dest.exists():
                photos.append(f"photos/{dest.name}")
        mid = f"bot{r['id']}"
        text = re.sub(r"\s*№\s*\d+", "", r.get("text", "")).strip()
        reviews[mid] = {"mid": mid, "ts": r.get("ts", 0), "text": text, "photos": photos,
                        "url": "", "no": r.get("no"), "source": r.get("src") or "bot", "id": r["id"]}
        if r.get("name"):
            reviews[mid]["name"] = r["name"]
        if r.get("stars"):
            reviews[mid]["stars"] = r["stars"]


def sync(messages, old_items, old_reviews, state, whole_history, ov=None, bot_reviews=()):
    """Возвращает (items, reviews, events)."""
    events = {"added": [], "removed": [], "warn": []}
    manual = [i for i in old_items if i.get("source") != "max"]
    old_max = {i["mid"]: i for i in old_items if i.get("source") == "max"}
    old_rev = {r.get("mid"): r for r in old_reviews}
    next_no = max([i.get("no", 0) for i in old_items] + [0]) + 1
    PHOTOS.mkdir(exist_ok=True)
    now_ms = int(time.time() * 1000)

    fresh, reviews = {}, {}
    for msg in messages:
        kind, data = classify(msg)
        if kind == "item":
            old = old_max.get(data["mid"])
            data["photos"] = fetch_photos(data, old)
            data["source"] = "max"
            if old:
                data["no"] = old["no"]
                if "views" not in data and "views" in old:
                    data["views"] = old["views"]
            else:
                data["no"] = next_no
                next_no += 1
                if old_items:  # на самом первом запуске не спамим
                    events["added"].append(data)
            fresh[data["mid"]] = data
        elif kind == "review":
            data["photos"] = fetch_photos(data, old_rev.get(data["mid"]))
            reviews[data["mid"]] = data
        elif kind == "warn":
            mid = str((msg.get("body") or {}).get("mid"))
            recent = now_ms - int(msg.get("timestamp") or 0) < 3 * 86400 * 1000
            if recent and mid not in state.setdefault("warned", []):
                state["warned"] = (state["warned"] + [mid])[-200:]
                events["warn"].append(data)

    oldest_ts = min((int(m.get("timestamp") or 0) for m in messages), default=0)
    for mid, old in old_max.items():
        if mid in fresh:
            continue
        if not whole_history and old.get("ts", 0) < oldest_ts:
            fresh[mid] = old            # пост за пределами просмотренной истории — не трогаем
        else:
            events["removed"].append(old)
            log(f"  - убран с сайта: {old.get('title')}")
    for d in events["added"]:
        log(f"  + новый товар №{d['no']}: {d['title']}")

    # ручные правки из бота: скрыть / цена
    ov = ov or {}
    hidden = set(ov.get("hidden", []))
    prices = ov.get("price", {})
    covers = ov.get("cover", {})
    for it in list(fresh.values()) + manual:
        k = covers.get(str(it.get("no")))
        base = it.pop("photos_orig", None) or it.get("photos") or []   # порядок как в посте
        it["photos"] = list(base)
        if isinstance(k, int) and 1 < k <= len(base):   # выбранное фото — первым (обложка)
            it["photos"] = [base[k - 1]] + base[:k - 1] + base[k:]
            it["photos_orig"] = base
        it.pop("hidden", None)
        if it.get("no") in hidden:
            it["hidden"] = True
        if str(it.get("no")) in prices:
            it["price"] = prices[str(it["no"])]
        # правки карточки из приложения: название, описание, категория, метка, закрепление
        orig = it.pop("_orig", None) or {}
        for f, v in orig.items():
            it[f] = v
        key = str(it.get("no"))
        new_orig = {}
        for f, src in (("title", "title"), ("desc", "desc"), ("category", "cat")):
            val = (ov.get(src) or {}).get(key)
            if val:
                new_orig[f] = it.get(f)
                it[f] = val
        if new_orig:
            it["_orig"] = new_orig
        it.pop("badge", None); it.pop("pin", None)
        if (ov.get("badge") or {}).get(key) in ("hit", "out"):
            it["badge"] = ov["badge"][key]
        if it.get("no") in (ov.get("pin") or []):
            it["pin"] = (ov.get("pin") or []).index(it["no"]) + 1

    items = sorted(fresh.values(), key=lambda i: i.get("ts", 0), reverse=True) + manual
    add_bot_reviews(reviews, bot_reviews)
    reviews = sorted(reviews.values(), key=lambda r: r["ts"], reverse=True)

    used = {p for x in items + reviews for p in x.get("photos", [])}
    for f in PHOTOS.glob("*.jpg"):
        if f"photos/{f.name}" not in used:
            f.unlink()
            t = photolib.thumb_path(f)
            if t.exists():
                t.unlink()
    return items, reviews, events


# ---------------------------------------------------------------- просмотры

def update_views(items, old_items, state):
    """views_week / views_month — прирост просмотров с начала недели/месяца.
    Чтобы не коммитить каждые 15 минут, свежие просмотры берём раз в 3 часа."""
    now = time.time()
    old_views = {i.get("mid"): i.get("views") for i in old_items}
    refresh = now - state.get("views_ts", 0) >= VIEWS_REFRESH_SEC
    if refresh:
        state["views_ts"] = int(now)
    else:
        for it in items:
            if it.get("mid") and old_views.get(it["mid"]) is not None:
                it["views"] = old_views[it["mid"]]

    msk = dt.datetime.now(MSK)
    cur = {i["mid"]: i.get("views", 0) for i in items if i.get("source") == "max"}
    month_key = msk.strftime("%Y-%m")
    if state.get("snap_month", {}).get("key") != month_key:
        state["snap_month"] = {"key": month_key, "views": cur}
    if "snap_week" not in state:
        state["snap_week"] = {"ts": int(now), "views": cur}
    for it in items:
        if it.get("source") != "max" or "views" not in it:
            continue
        v = it["views"]
        it["views_week"] = max(0, v - state["snap_week"]["views"].get(it["mid"], 0))
        it["views_month"] = max(0, v - state["snap_month"]["views"].get(it["mid"], 0))


# ---------------------------------------------------------------- продажи

def apply_sales(items, sales):
    """sold / sold_week / sold_month у каждого товара — из отметок «/продано»."""
    now = time.time() * 1000
    week, month = 7 * 86400 * 1000, 30 * 86400 * 1000
    tot, wk, mo = {}, {}, {}
    for r in sales:
        no, q, ts = r.get("no"), int(r.get("qty") or 1), int(r.get("ts") or 0)
        tot[no] = tot.get(no, 0) + q
        if now - ts < week:
            wk[no] = wk.get(no, 0) + q
        if now - ts < month:
            mo[no] = mo.get(no, 0) + q
    for it in items:
        for key in ("sold", "sold_week", "sold_month"):
            it.pop(key, None)
        no = it.get("no")
        if tot.get(no):
            it["sold"] = tot[no]
            if wk.get(no):
                it["sold_week"] = wk[no]
            if mo.get(no):
                it["sold_month"] = mo[no]


# ---------------------------------------------------------------- main

def main():
    import bot
    import pages

    token = os.environ.get("MAX_TOKEN", "").strip()
    if not token:
        log("Нет MAX_TOKEN. Добавьте токен бота в Settings → Secrets and variables → Actions.")
        return 1

    state = load_json(STATE, {})
    ov = load_json(OVERRIDES, {})
    for k in ("hidden", "price"):            # перенос из старого формата
        if k in state:
            ov.setdefault(k, state.pop(k))
    ov_before = json.dumps(ov, sort_keys=True)
    old_items, old_reviews = load_catalog()
    notifier = bot.Notifier(token)
    try:
        chat_id = find_channel(token)
        owners = bot.get_owners(token, chat_id, state)
        notifier.owners = owners
        bot.ensure_commands(token, state)
        log(f"Канал {chat_id}, владельцев: {len(owners)}")

        # 1) команды от владельца (до синхронизации, чтобы /скрыть и /пост сработали сразу)
        sales_doc = load_json(SALES, {"sales": []})
        sales_before = json.dumps(sales_doc, sort_keys=True)
        deferred = bot.process_commands(token, chat_id, owners, state, notifier, ov, sales_doc)
        if json.dumps(sales_doc, sort_keys=True) != sales_before:
            dump_json(SALES, sales_doc)
        dump_json(STATE, state)   # сохраняем сразу: команды не выполнятся повторно
        if json.dumps(ov, sort_keys=True) != ov_before or not OVERRIDES.exists():
            dump_json(OVERRIDES, ov)

        # 2) посты канала → каталог
        messages, whole = fetch_posts(token, chat_id)
    except ApiError as e:
        log(f"ОШИБКА: {e}")
        notifier.send(f"⚠️ Синхронизация сайта не удалась:\n{e}", kind="errors")
        dump_json(STATE, state)
        return 1

    bot_reviews = load_json(REVIEWS, {}).get("reviews", [])
    items, reviews, events = sync(messages, old_items, old_reviews, state, whole, ov, bot_reviews)
    update_views(items, old_items, state)
    apply_sales(items, sales_doc.get("sales", []))

    # 3) страницы товаров, QR
    pages.generate(items, SITE_URL)
    pages.make_qr(SITE_URL)

    catalog = {"items": items, "reviews": reviews}
    if state.get("bot_link"):
        catalog["bot"] = state["bot_link"]
    _settings = load_json(ROOT / "settings.json", {}) if (ROOT / "settings.json").exists() else {}
    _maint = _settings.get("maint") or {}
    if _maint.get("on"):
        catalog["maint"] = {k: _maint.get(k) for k in ("mode", "text", "until")}
    _notice = _settings.get("notice") or {}
    if _notice.get("on") and _notice.get("text") and (not _notice.get("until") or _notice["until"] >= dt.date.today().isoformat()):
        catalog["notice"] = {k: _notice.get(k) for k in ("text", "until", "link")}
    _cols = [c for c in (_settings.get("collections") or []) if c.get("on") and c.get("nos")]
    if _cols:
        catalog["collections"] = [{"id": c["id"], "title": c["title"], "nos": c["nos"]} for c in _cols]
    if _settings.get("texts"):
        catalog["texts"] = _settings["texts"]
    _sub = (load_json(PROMOS, {}) if PROMOS.exists() else {}).get("sub") or {}
    if _sub.get("on"):
        catalog["gift"] = {"kind": _sub["kind"], "value": _sub["value"]}
    old_catalog = {"items": old_items, "reviews": old_reviews}
    _oc = load_json(CATALOG, {})
    for _k in ("bot", "gift", "maint", "notice", "collections", "texts"):
        if _oc.get(_k):
            old_catalog[_k] = _oc[_k]
    if json.dumps(catalog, sort_keys=True, ensure_ascii=False) != json.dumps(old_catalog, sort_keys=True, ensure_ascii=False):
        dump_json(CATALOG, {"updated": int(time.time()), **catalog})
        log("Каталог обновлён.")

    # 4) ответы на команды, уведомления, отчёт
    for fn in deferred:
        try:
            fn(items)
        except Exception as e:
            log(f"  ! ответ на команду: {e}")
    bot.notify_events(notifier, events, SITE_URL)
    bot.maybe_weekly_report(notifier, items, state, SITE_URL, sales_doc.get("sales", []))

    # 4б) напоминание: в канале давно не было постов
    try:
        last = max([int((m.get("timestamp") or 0) / 1000) for m in messages] + [0])
        if last:
            state["last_post_ts"] = last
        maybe_post_reminder(notifier, state)
    except Exception as e:
        log(f"  ! напоминание о постах: {e}")

    # 5) разовые посты от бота (outbox.json)
    ob = ROOT / "outbox.json"
    if ob.exists():
        try:
            od = load_json(ob, {})
            if outbox.run(od, token, chat_id, notifier, SITE_URL):
                dump_json(ob, od)
        except Exception as e:
            log(f"  ! outbox: {e}")

    # 6) промокоды по расписанию
    if PROMOS.exists():
        try:
            pd = load_json(PROMOS, {})
            if promo.run(pd, token, chat_id, notifier, SITE_URL):
                dump_json(PROMOS, pd)
        except Exception as e:
            log(f"  ! промокоды: {e}")

    dump_json(STATE, state)
    visible = [i for i in items if not i.get("hidden")]
    log(f"Готово. Товаров на сайте: {len(visible)}, отзывов: {len(reviews)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

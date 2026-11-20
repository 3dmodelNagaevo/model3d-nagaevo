"""
Бот для владельца канала: команды в личке, уведомления, еженедельный отчёт.

Команды слушаются только от владельца/админов канала (проверка по списку админов MAX).
Бот отвечает при следующем запуске синхронизации — обычно в течение 5–15 минут.
"""
import datetime as dt
import json
import os
import re
import time
import urllib.parse
import urllib.request

import maxapi
from maxapi import ApiError, log

MSK = dt.timezone(dt.timedelta(hours=3))
HELP = """🤖 Бот сайта. Кнопки меню — под этим сообщением, или пишите команды:

/меню — показать кнопки
/статус — сколько товаров на сайте, когда обновлялся
/топ — популярное за неделю (по просмотрам постов)
/стата — статистика за неделю
✅ Продано — затем просто номера: «38», «20 2» (2 шт.), можно несколько сразу
/отменить — отменить последнюю отметку о продажах
/продажи — продажи за неделю и месяц
/скрыть 12 — убрать товар №12 с сайта (пост в канале остаётся)
/показать 12 — вернуть товар на сайт
/цена 12 300 — поменять цену на сайте
/цена 12 сброс — вернуть цену из поста
/обложка 12 3 — сделать 3-е фото главным у товара №12
/шаблон — шаблон поста для канала
/отзыв текст (+ фото) — добавить отзыв на сайт, /отзывы — список
/пост — опубликовать товар в канал (см. ниже)

📸 Публикация: пришлите фото и подпись:
/пост Дракон-антистресс
Цена: 200
#антистресс
Можно добавить строки «Размер: …», «Цвет: …» и описание.

Номер товара (№) виден на сайте в карточке и в тексте заказа.
Бот отвечает обычно в течение 5–15 минут."""


# ---------------------------------------------------------------- меню

SITE_URL = (os.environ.get("SITE_URL") or "https://3dnagaevo.ru/").rstrip("/") + "/"

# Кнопки типа "message": нажатие отправляет боту текст кнопки, как будто его написали вручную.
MENU_BUTTONS = {
    "📊 Статус": "/статус", "🔥 Топ": "/топ", "📈 Статистика": "/стата", "🔄 Обновить": "/обновить",
    "🙈 Скрыть": "/скрыть", "👁 Вернуть": "/показать", "💰 Цена": "/цена", "🖼 Обложка": "/обложка",
    "📸 Новый пост": "/пост", "❓ Помощь": "/помощь", "📝 Шаблон": "/шаблон",
    "💬 Отзыв": "/отзыв", "🗂 Отзывы": "/отзывы", "👥 Доступ": "/доступ", "📜 История": "/история", "↩️ Отменить": "/отменить", "✅ Продано": "/продано", "🧾 Продажи": "/продажи",
    # старые подписи кнопок (из прошлых сообщений)
    "🔥 Топ недели": "/топ", "🙈 Скрыть товар": "/скрыть", "👁 Вернуть товар": "/показать",
    "💰 Изменить цену": "/цена", "🔄 Обновить сайт": "/обновить",
}
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

BOT_COMMANDS = [
    ("menu", "Меню"), ("status", "Статус сайта"), ("top", "Популярное за неделю"),
    ("stats", "Статистика за неделю"), ("help", "Все команды"),
]
EN_ALIASES = {"/menu": "/меню", "/status": "/статус", "/top": "/топ", "/stats": "/стата", "/help": "/помощь"}


def menu_keyboard():
    rows = [[{"type": "message", "text": t} for t in row] for row in MENU_ROWS]
    rows.append([{"type": "link", "text": "🌐 Открыть сайт", "url": SITE_URL}])
    return {"type": "inline_keyboard", "payload": {"buttons": rows}}


def ensure_commands(token, state):
    """Список команд бота (кнопка «/» в чате). Ставим один раз."""
    version = 1
    if state.get("commands_v") == version:
        return
    try:
        maxapi.patch("/me", {}, token, {"commands": [{"name": n, "description": d} for n, d in BOT_COMMANDS]})
        log("  список команд бота обновлён")
    except Exception as e:
        log(f"  ! не удалось задать список команд: {e}")
    state["commands_v"] = version


# ---------------------------------------------------------------- владельцы

def get_owners(token, chat_id, state):
    """Кто может управлять ботом: владелец канала всегда, другие админы — если владелец разрешил (access.json)."""
    env = [int(x) for x in re.findall(r"-?\d+", os.environ.get("OWNER_IDS", ""))]
    if env:
        return env
    import sync_max as sm
    acc = sm.load_json(sm.ROOT / "access.json", {})
    allowed = set(acc.get("allowed", []))
    full = set(acc.get("full", []))
    no_names = any("name" not in a for a in state.get("admins") or [])
    if time.time() - state.get("owners_ts", 0) >= 3600 or "admins" not in state or no_names:
        try:
            data = maxapi.get(f"/chats/{chat_id}/members/admins", {}, token)
            ms = [m for m in data.get("members") or [] if not m.get("is_bot")]
            if ms:
                state["admins"] = [{"id": m["user_id"], "owner": bool(m.get("is_owner")),
                                    "name": " ".join(x for x in (m.get("first_name"), m.get("last_name")) if x) or m.get("name") or m.get("username") or str(m["user_id"])}
                                   for m in ms]
                state["owners_ts"] = int(time.time())
        except ApiError as e:
            log(f"  ! список админов недоступен: {e}")
    admins = state.get("admins") or []
    chief = [a["id"] for a in admins if a.get("owner")]
    if admins and not chief:
        log("  ! MAX не сообщил владельца канала — доступ у всех админов")
        chief = [a["id"] for a in admins]
    ids = {a["id"] for a in admins}
    chief = sorted(set(chief) | (full & ids))
    owners = sorted(set(chief) | (allowed & ids)) if admins else state.get("owners", [])
    state["owners"], state["chief"] = owners, sorted(set(chief))
    return owners


# ---------------------------------------------------------------- отправка

class Notifier:
    def __init__(self, token):
        self.token, self.owners = token, []

    def send(self, text, user_id=None, menu=False):
        targets = [user_id] if user_id else self.owners
        body = {"text": text[:3900]}
        if menu:
            body["attachments"] = [menu_keyboard()]
        for uid in targets:
            try:
                maxapi.post("/messages", {"user_id": uid, "disable_link_preview": "true"}, self.token, body)
            except Exception as e:
                log(f"  ! не удалось написать {uid}: {e}")


# ---------------------------------------------------------------- команды

def _num(s):
    m = re.search(r"\d+", s or "")
    return int(m.group(0)) if m else None


def _find(items, no):
    return next((i for i in items if i.get("no") == no), None)


def _fmt_price(p):
    return "цена по запросу" if p is None else f"{p} ₽"


def build_post(text, att_images):
    """Подпись команды /пост → текст поста в стиле канала."""
    import sync_max as sm
    body = re.sub(r"^/(пост|post|новый)\s*", "", text.strip(), flags=re.I)
    tags, fields, rest = sm.split_post(body)
    price = sm.parse_price(fields.get("price"))
    if not rest:
        raise ValueError("нет названия: напишите его сразу после /пост")
    if price is None:
        raise ValueError("нет цены: добавьте строку «Цена: 200»")
    lines = [rest[0]]
    lines += rest[1:]
    lines += [
        f"- Размер: ({fields.get('size', 'по желанию').strip('()')})",
        f"- Цвет: {fields.get('color', 'согласовывать в ЛС')}",
        f"- Стоимость: {price} ₽",
    ]
    if tags:
        lines.append(" ".join("#" + t for t in tags))
    atts = []
    for a in att_images[:10]:
        p = a.get("payload") or {}
        if p.get("token"):
            atts.append({"type": "image", "payload": {"token": p["token"]}})
        elif p.get("url"):
            atts.append({"type": "image", "payload": {"url": p["url"]}})
    return "\n".join(lines), atts


def webhook_active(token):
    """Если бот подписан на webhook (Yandex Cloud), команды обрабатывает он, а не GitHub."""
    try:
        subs = maxapi.get("/subscriptions", {}, token).get("subscriptions") or []
        return bool(subs)
    except Exception as e:
        log(f"  ! не удалось проверить webhook: {e}")
        return False



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


def process_commands(token, chat_id, owners, state, notifier, ov, sales_doc=None):
    """Читает личку бота. Возвращает список отложенных ответов fn(items) — им нужен свежий каталог."""
    if webhook_active(token):
        log("  команды бота обрабатывает Yandex Cloud (webhook) — пропускаю")
        return []
    params = {"limit": 100, "timeout": 0}
    if state.get("marker"):
        params["marker"] = state["marker"]
    try:
        data = maxapi.get("/updates", params, token)
    except ApiError as e:
        log(f"  ! не удалось прочитать сообщения бота: {e}")
        return []
    if data.get("marker"):
        state["marker"] = data["marker"]

    deferred = []
    for u in data.get("updates") or []:
        if u.get("update_type") not in ("message_created", "bot_started"):
            continue
        msg = u.get("message") or {}
        rec = msg.get("recipient") or {}
        sender = (msg.get("sender") or u.get("user") or {}).get("user_id")
        if u.get("update_type") == "bot_started":
            if sender in owners:
                notifier.send(HELP, sender, menu=True)
            continue
        if rec.get("chat_type") != "dialog" or sender not in owners:
            continue                        # не владелец — игнорируем (покупателей пока не обслуживаем)
        body = msg.get("body") or {}
        text = (body.get("text") or "").strip()
        images = [a for a in body.get("attachments") or [] if a.get("type") == "image"]
        if text in MENU_BUTTONS:                       # нажата кнопка меню
            text = MENU_BUTTONS[text]
        if is_sales_list(text):                        # просто номера — это продажи
            text = "/продано " + text
        cmd = text.split()[0].lower() if text else ""
        cmd = EN_ALIASES.get(cmd, cmd)
        log(f"  команда от владельца: {cmd or '(без текста)'}")

        def reply(t, uid=sender):
            notifier.send(t, uid, menu=True)      # меню прикрепляем к каждому ответу

        if cmd in ("/start", "/help", "/помощь", "помощь"):
            reply(HELP)
        elif cmd in ("/меню", "меню", "/menu"):
            reply("Меню 👇")
        elif cmd in ("/скрыть", "/показать"):
            no = _num(text)
            if not no:
                reply(f"Напишите номер товара, например: {cmd} 12\n"
                      "Номер есть на сайте в карточке товара и в /статус.")
                continue
            hidden = set(ov.get("hidden", []))
            (hidden.add if cmd == "/скрыть" else hidden.discard)(no)
            ov["hidden"] = sorted(hidden)
            deferred.append(lambda items, no=no, cmd=cmd, reply=reply: reply(
                (f"🙈 Скрыл с сайта №{no}" if cmd == "/скрыть" else f"👁 Вернул на сайт №{no}")
                + (f": {_find(items, no)['title']}" if _find(items, no) else " (такого номера сейчас нет)")
                + "\nСайт обновится через 1–2 минуты."))
        elif cmd == "/цена":
            parts = text.split()
            no = _num(parts[1]) if len(parts) > 1 else None
            if not no or len(parts) < 3:
                reply("Напишите номер товара и новую цену, например:\n/цена 12 300\n\nВернуть цену из поста: /цена 12 сброс")
                continue
            prices = ov.setdefault("price", {})
            if parts[2].lower() in ("сброс", "reset", "-"):
                prices.pop(str(no), None)
                deferred.append(lambda items, no=no, reply=reply: reply(f"↩️ Цена №{no} снова берётся из поста."))
            else:
                p = _num(parts[2])
                prices[str(no)] = p
                deferred.append(lambda items, no=no, p=p, reply=reply: reply(
                    f"💰 Цена №{no}" + (f" ({_find(items, no)['title']})" if _find(items, no) else "") + f" → {p} ₽"))
        elif cmd in ("/пост", "/post", "/новый") and not images and text.strip().lower() in ("/пост", "/post", "/новый"):
            reply("📸 Пришлите фото товара и в подписи к нему:\n\n/пост Название\nЦена: 200\n#категория\n\n"
                  "Можно добавить строки «Размер: …», «Цвет: …» и описание.")
        elif cmd in ("/пост", "/post", "/новый"):
            try:
                post_text, atts = build_post(text, images)
                maxapi.post("/messages", {"chat_id": chat_id}, token,
                            {"text": post_text, **({"attachments": atts} if atts else {})})
                reply("✅ Опубликовал в канал. На сайте появится через 1–2 минуты." +
                      ("" if atts else "\n(Без фото — пришлите фото вместе с подписью /пост.)"))
            except ValueError as e:
                reply(f"❌ Не опубликовал: {e}\n\nПример:\n/пост Дракон-антистресс\nЦена: 200\n#антистресс")
            except ApiError as e:
                reply("❌ MAX не дал опубликовать. Проверьте, что у бота в канале есть право "
                      f"«Публиковать посты».\n\n{e}")
        elif cmd == "/продано":
            rest = text.split(None, 1)[1] if len(text.split(None, 1)) > 1 else ""
            pairs, bad = parse_sales(rest)
            if not pairs or sales_doc is None:
                reply(SALES_HELP)
                continue
            batch = int(time.time() * 1000)
            nxt = ensure_sale_ids(sales_doc)
            sales_doc.setdefault("sales", []).extend(
                {"id": nxt + k, "no": no, "qty": q, "ts": batch, "b": batch} for k, (no, q) in enumerate(pairs))
            deferred.append(lambda items, pairs=pairs, bad=bad, reply=reply: reply(
                "✅ Записал продажи:\n" + "\n".join(
                    f"• №{no} {(_find(items, no) or {}).get('title', '(нет на сайте — проверьте номер)')} × {q}" for no, q in pairs)
                + (("\n⚠️ Не понял: " + ", ".join(bad)) if bad else "") + "\n\nОшиблись — /отменить."))
        elif cmd == "/история":
            if sales_doc is not None:
                ensure_sale_ids(sales_doc)
            deferred.append(lambda items, reply=reply: reply(history_text(items, (sales_doc or {}).get("sales", []))))
        elif cmd in ("/удалить", "/удалить_продажу") and "продаж" in text.lower():
            sid = _num(text)
            ok = bool(sid) and sales_doc is not None and delete_sale(sales_doc, sid)
            reply(f"🗑 Отметка #{sid} удалена." if ok else "Напишите номер отметки из «📜 История»: /удалить продажу 7")
        elif cmd in ("/сбросить", "/сбросить_продажи"):
            no = _num(text)
            n = reset_item_sales(sales_doc, no) if (no and sales_doc is not None) else 0
            reply(f"🧹 Удалил отметки продаж у №{no} ({n} шт.)." if n else "Напишите номер товара: /сбросить продажи 38")
        elif cmd == "/отменить":
            sales = (sales_doc or {}).get("sales") or []
            if not sales:
                reply("Отметок о продажах пока нет.")
                continue
            last_b, removed = sales[-1].get("b"), []
            while sales and (sales[-1].get("b") == last_b if last_b else not removed):
                removed.append(sales.pop())
            reply("↩️ Отменил последнюю отметку: " + ", ".join(f"№{r['no']}×{r.get('qty', 1)}" for r in reversed(removed)))
        elif cmd == "/продажи":
            deferred.append(lambda items, reply=reply: reply(sales_text(items, (sales_doc or {}).get("sales", []))))
        elif cmd in ("/отзыв", "отзыв:") or "#отзыв" in text.lower():
            reply(save_review_local(text, images))
        elif cmd == "/отзывы":
            reply(list_reviews_local())
        elif cmd in ("/удалить", "/удалить_отзыв") and "отзыв" in text.lower():
            reply(delete_review_local(_num(text)))
        elif cmd == "/шаблон":
            notifier.send(TEMPLATE_HELP, sender)
            reply(POST_TEMPLATE)
        elif cmd == "/обновить":
            reply("🔄 Сайт обновляется при каждой проверке канала — уже в процессе.")
        elif cmd == "/обложка":
            parts = text.split()
            no = _num(parts[1]) if len(parts) > 1 else None
            k = _num(parts[2]) if len(parts) > 2 else None
            if not no or not k:
                reply("Выберите, какое фото будет главным:\n/обложка 12 3 — у товара №12 главным станет 3-е фото\n"
                      "/обложка 12 1 — вернуть как в посте\n\nФото считаются по порядку, как в карточке на сайте и в посте.")
                continue
            covers = ov.setdefault("cover", {})
            if k <= 1:
                covers.pop(str(no), None)
            else:
                covers[str(no)] = k
            deferred.append(lambda items, no=no, k=k, reply=reply: reply(
                f"🖼 Обложка №{no}: фото {k}. Сайт обновится через 1–2 минуты." if k > 1
                else f"🖼 Обложка №{no}: как в посте."))
        elif cmd == "/статус":
            deferred.append(lambda items, reply=reply: reply(status_text(items, ov)))
        elif cmd == "/топ":
            deferred.append(lambda items, reply=reply: reply(top_text(items, "views_week", "за неделю")))
        elif cmd in ("/стата", "/статистика"):
            deferred.append(lambda items, reply=reply: reply(stats_text(items, state)))
        elif images and not text:
            reply("Чтобы опубликовать товар, пришлите фото с подписью, начинающейся с /пост. /помощь — все команды.")
        else:
            reply("Не понял команду. /помощь — список команд.")
    return deferred


# ---------------------------------------------------------------- тексты

REVIEW_HELP = ("💬 Как добавить отзыв на сайт (в канал он не попадёт):\n\n"
               "Пришлите фото и в подписи: /отзыв Купили дракона сыну — играет каждый день! — Анна\n"
               "Можно без фото. Можно указать товар: №12. Удалить: /удалить отзыв 3")


def _rev_doc():
    import sync_max as sm
    return sm.REVIEWS, sm.load_json(sm.REVIEWS, {"reviews": []})


def save_review_local(text, images):
    import sync_max as sm
    body = re.sub(r"^/?(отзыв|review)\s*:?\s*", "", re.sub(r"#отзыв", "", text, flags=re.I).strip(), flags=re.I).strip()
    if not body and not images:
        return REVIEW_HELP
    path, doc = _rev_doc()
    rid = max([r.get("id", 0) for r in doc["reviews"]] + [0]) + 1
    raw = []
    for k, a in enumerate(images[:4]):
        url = (a.get("payload") or {}).get("url")
        if not url:
            continue
        try:
            dest = sm.ROOT / "reviews" / "raw" / f"{rid}_{k}.jpg"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(maxapi.download(url))
            raw.append(f"reviews/raw/{dest.name}")
        except Exception as e:
            log(f"  ! фото отзыва: {e}")
    m = re.search(r"№\s*(\d+)", body)
    doc["reviews"].append({"id": rid, "ts": int(time.time() * 1000), "text": body[:800], "raw": raw,
                           "no": int(m.group(1)) if m else None})
    sm.dump_json(path, doc)
    return f"💬 Отзыв №{rid} добавлен на сайт" + (f" с фото ({len(raw)})" if raw else "") + ". Убрать: /удалить отзыв " + str(rid)


def list_reviews_local():
    _, doc = _rev_doc()
    if not doc["reviews"]:
        return "Отзывов из бота пока нет.\n\n" + REVIEW_HELP
    return "🗂 Отзывы на сайте (из бота):\n" + "\n".join(
        f"№{r['id']}: {(r.get('text') or '(без текста)')[:60]}{' 📷' if r.get('raw') else ''}" for r in doc["reviews"][-15:])


def delete_review_local(rid):
    import sync_max as sm
    if not rid:
        return "Напишите номер отзыва: /удалить отзыв 3"
    path, doc = _rev_doc()
    before = len(doc["reviews"])
    doc["reviews"] = [r for r in doc["reviews"] if r.get("id") != rid]
    sm.dump_json(path, doc)
    return f"🗑 Отзыв №{rid} убран с сайта." if before != len(doc["reviews"]) else f"Отзыва №{rid} нет."


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


def times(n):
    return "раза" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else "раз"


def _sum(sales, since_ms):
    out = {}
    for r in sales:
        if r.get("ts", 0) >= since_ms:
            out[r["no"]] = out.get(r["no"], 0) + int(r.get("qty") or 1)
    return out


def sold_reply(items, total, no, qty):
    it = _find(items, no)
    name = f"№{no} {it['title']}" if it else f"№{no} (такого номера сейчас нет на сайте — проверьте номер)"
    return (f"✅ Записал продажу: {name} × {qty or 1}\nВсего продано: {total} шт.\n"
            "Ошиблись — /отменить. На сайте обновится «Популярное».")


def sales_text(items, sales, days_list=(7, 30)):
    if not sales:
        return "🧾 Продаж пока не отмечено. После продажи нажмите «✅ Продано» и напишите номер товара."
    now = time.time() * 1000
    parts = []
    for days in days_list:
        agg = _sum(sales, now - days * 86400 * 1000)
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
    hid = [i for i in items if i.get("hidden")]
    last = max((i.get("ts", 0) for i in items if i.get("source") == "max"), default=0)
    last_s = dt.datetime.fromtimestamp(last / 1000, MSK).strftime("%d.%m %H:%M") if last else "—"
    lines = [f"📊 На сайте: {len(vis)} товаров", f"🕐 Последний пост с товаром: {last_s}"]
    if hid:
        lines.append("🙈 Скрыты: " + ", ".join(f"№{i['no']}" for i in hid))
    if ov.get("price"):
        lines.append("💰 Цены изменены вручную: " + ", ".join(f"№{k}" for k in ov["price"]))
    return "\n".join(lines)


def top_text(items, key, label, n=5):
    if key == "views_week" and any(i.get("sold_week") for i in items):
        rows = sorted([i for i in items if not i.get("hidden") and (i.get("sold_week") or i.get("views_week"))],
                      key=lambda i: (-(i.get("sold_week") or 0), -(i.get("views_week") or 0)))[:n]
        return "🔥 Хиты недели (продажи, потом просмотры):\n" + "\n".join(
            f"{k}. №{i['no']} {i['title']} — продано {i.get('sold_week', 0)}, {i.get('views_week', 0)} просм."
            for k, i in enumerate(rows, 1))
    rows = sorted([i for i in items if i.get(key) and not i.get("hidden")], key=lambda i: -i[key])[:n]
    if not rows and key != "views":       # за неделю ещё пусто — показываем за всё время
        return top_text(items, "views", "за всё время", n)
    if not rows:
        return f"🔥 Популярное {label}: пока нет данных о просмотрах."
    return f"🔥 Популярное {label}:\n" + "\n".join(
        f"{k}. {i['title']} — {i[key]} просм." for k, i in enumerate(rows, 1))


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
        log(f"  ! Метрика: {e}")
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
        log(f"  ! Метрика (источники): {e}")
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
        log(f"  ! Метрика (цели): {e}")
    return "\n".join(lines)


def stats_text(items, state):
    week_ms = 7 * 86400 * 1000
    now_ms = time.time() * 1000
    new = [i for i in items if i.get("source") == "max" and now_ms - i.get("ts", 0) < week_ms]
    views = sum(i.get("views_week", 0) for i in items)
    parts = [
        f"🆕 Новых товаров за неделю: {len(new)}",
        f"👁 Просмотров постов-товаров за неделю: {views}",
        f"📦 Всего на сайте: {len([i for i in items if not i.get('hidden')])}",
    ]
    m = metrika_text()
    if m:
        parts.append(m)
    parts.append("")
    parts.append(top_text(items, "views_week", "за неделю"))
    return "\n".join(parts)


# ---------------------------------------------------------------- уведомления

def notify_events(notifier, events, site_url):
    for it in events["added"][:10]:
        notifier.send(f"✅ Добавлено на сайт: №{it['no']} {it['title']} — {_fmt_price(it.get('price'))}\n"
                      f"{site_url}m/{it['no']}.html")
    if len(events["added"]) > 10:
        notifier.send(f"…и ещё {len(events['added']) - 10} товаров.")
    for it in events["removed"]:
        notifier.send(f"🗑 Убрано с сайта (пост удалён или помечен #нетнасайте): №{it['no']} {it['title']}")
    for title in events["warn"]:
        notifier.send(f"⚠️ Пост «{title}» не попал на сайт: в нём нет строки «Стоимость: …».\n"
                      "Если это товар — допишите цену. Если нет — всё в порядке.")


def maybe_weekly_report(notifier, items, state, site_url, sales=None):
    """Отчёт по понедельникам после 10:00 МСК, раз в неделю."""
    now = dt.datetime.now(MSK)
    last = state.get("last_report", 0)
    if now.weekday() != 0 or now.hour < 10 or time.time() - last < 5 * 86400:
        return
    if not notifier.owners:
        return
    notifier.send("📅 Отчёт за неделю\n\n" + sales_text(items, sales or [], (7,)) + "\n\n"
                  + stats_text(items, state) + f"\n\n{site_url}", menu=True)
    state["last_report"] = int(time.time())
    state["snap_week"] = {"ts": int(time.time()),
                          "views": {i["mid"]: i.get("views", 0) for i in items if i.get("source") == "max"}}

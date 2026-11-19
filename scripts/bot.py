"""
Бот для владельца канала: команды в личке, уведомления, еженедельный отчёт.

Команды слушаются только от владельца/админов канала (проверка по списку админов MAX).
Бот отвечает при следующем запуске синхронизации — обычно в течение 15–30 минут.
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
HELP = """🤖 Команды бота сайта:

/статус — сколько товаров на сайте, когда обновлялся
/топ — популярное за неделю (по просмотрам постов)
/стата — статистика за неделю
/скрыть 12 — убрать товар №12 с сайта (пост в канале остаётся)
/показать 12 — вернуть товар на сайт
/цена 12 300 — поменять цену на сайте
/цена 12 сброс — вернуть цену из поста
/пост — опубликовать товар в канал (см. ниже)

📸 Публикация: пришлите фото и подпись:
/пост Дракон-антистресс
Цена: 200
#антистресс
Можно добавить строки «Размер: …», «Цвет: …» и описание.

Номер товара (№) виден на сайте в карточке и в тексте заказа.
Бот отвечает в течение 15–30 минут."""


# ---------------------------------------------------------------- владельцы

def get_owners(token, chat_id, state):
    env = [int(x) for x in re.findall(r"-?\d+", os.environ.get("OWNER_IDS", ""))]
    if env:
        return env
    if time.time() - state.get("owners_ts", 0) < 86400 and state.get("owners"):
        return state["owners"]
    try:
        data = maxapi.get(f"/chats/{chat_id}/members/admins", {}, token)
        owners = [m["user_id"] for m in data.get("members") or []
                  if not m.get("is_bot") and (m.get("is_owner") or m.get("is_admin"))]
        if owners:
            state["owners"], state["owners_ts"] = owners, int(time.time())
            return owners
    except ApiError as e:
        log(f"  ! список админов недоступен: {e}")
    return state.get("owners", [])


# ---------------------------------------------------------------- отправка

class Notifier:
    def __init__(self, token):
        self.token, self.owners = token, []

    def send(self, text, user_id=None):
        targets = [user_id] if user_id else self.owners
        for uid in targets:
            try:
                maxapi.post("/messages", {"user_id": uid, "disable_link_preview": "true"}, self.token,
                            {"text": text[:3900]})
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


def process_commands(token, chat_id, owners, state, notifier):
    """Читает личку бота. Возвращает список отложенных ответов fn(items) — им нужен свежий каталог."""
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
                notifier.send(HELP, sender)
            continue
        if rec.get("chat_type") != "dialog" or sender not in owners:
            continue                        # не владелец — игнорируем (покупателей пока не обслуживаем)
        body = msg.get("body") or {}
        text = (body.get("text") or "").strip()
        images = [a for a in body.get("attachments") or [] if a.get("type") == "image"]
        cmd = text.split()[0].lower() if text else ""
        log(f"  команда от владельца: {cmd or '(без текста)'}")

        def reply(t, uid=sender):
            notifier.send(t, uid)

        if cmd in ("/start", "/help", "/помощь", "помощь"):
            reply(HELP)
        elif cmd in ("/скрыть", "/показать"):
            no = _num(text)
            if not no:
                reply("Укажите номер товара: /скрыть 12")
                continue
            hidden = set(state.get("hidden", []))
            (hidden.add if cmd == "/скрыть" else hidden.discard)(no)
            state["hidden"] = sorted(hidden)
            deferred.append(lambda items, no=no, cmd=cmd, reply=reply: reply(
                (f"🙈 Скрыл с сайта №{no}" if cmd == "/скрыть" else f"👁 Вернул на сайт №{no}")
                + (f": {_find(items, no)['title']}" if _find(items, no) else " (такого номера сейчас нет)")
                + "\nСайт обновится через 1–2 минуты."))
        elif cmd == "/цена":
            parts = text.split()
            no = _num(parts[1]) if len(parts) > 1 else None
            if not no or len(parts) < 3:
                reply("Формат: /цена 12 300  или  /цена 12 сброс")
                continue
            prices = state.setdefault("price", {})
            if parts[2].lower() in ("сброс", "reset", "-"):
                prices.pop(str(no), None)
                deferred.append(lambda items, no=no, reply=reply: reply(f"↩️ Цена №{no} снова берётся из поста."))
            else:
                p = _num(parts[2])
                prices[str(no)] = p
                deferred.append(lambda items, no=no, p=p, reply=reply: reply(
                    f"💰 Цена №{no}" + (f" ({_find(items, no)['title']})" if _find(items, no) else "") + f" → {p} ₽"))
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
        elif cmd == "/статус":
            deferred.append(lambda items, reply=reply: reply(status_text(items, state)))
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

def status_text(items, state):
    vis = [i for i in items if not i.get("hidden")]
    hid = [i for i in items if i.get("hidden")]
    last = max((i.get("ts", 0) for i in items if i.get("source") == "max"), default=0)
    last_s = dt.datetime.fromtimestamp(last / 1000, MSK).strftime("%d.%m %H:%M") if last else "—"
    lines = [f"📊 На сайте: {len(vis)} товаров", f"🕐 Последний пост с товаром: {last_s}"]
    if hid:
        lines.append("🙈 Скрыты: " + ", ".join(f"№{i['no']}" for i in hid))
    if state.get("price"):
        lines.append("💰 Цены изменены вручную: " + ", ".join(f"№{k}" for k in state["price"]))
    return "\n".join(lines)


def top_text(items, key, label, n=5):
    rows = sorted([i for i in items if i.get(key) and not i.get("hidden")], key=lambda i: -i[key])[:n]
    if not rows:
        return f"🔥 Популярное {label}: пока нет данных о просмотрах."
    return f"🔥 Популярное {label}:\n" + "\n".join(
        f"{k}. {i['title']} — {i[key]} просм." for k, i in enumerate(rows, 1))


def metrika_text():
    mid, tok = os.environ.get("METRIKA_ID", "").strip(), os.environ.get("METRIKA_TOKEN", "").strip()
    if not (mid and tok):
        return ""
    q = urllib.parse.urlencode({"ids": mid, "metrics": "ym:s:visits,ym:s:users,ym:s:pageviews",
                                "date1": "7daysAgo", "date2": "today"})
    req = urllib.request.Request(f"https://api-metrika.yandex.net/stat/v1/data?{q}",
                                 headers={"Authorization": f"OAuth {tok}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read())
        v, u, p = (int(x) for x in d["totals"])
        return f"🌐 Сайт за 7 дней: {u} посетителей, {v} визитов, {p} просмотров страниц"
    except Exception as e:
        log(f"  ! Метрика: {e}")
        return ""


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


def maybe_weekly_report(notifier, items, state, site_url):
    """Отчёт по понедельникам после 10:00 МСК, раз в неделю."""
    now = dt.datetime.now(MSK)
    last = state.get("last_report", 0)
    if now.weekday() != 0 or now.hour < 10 or time.time() - last < 5 * 86400:
        return
    if not notifier.owners:
        return
    notifier.send("📅 Отчёт за неделю\n\n" + stats_text(items, state) + f"\n\n{site_url}")
    state["last_report"] = int(time.time())
    state["snap_week"] = {"ts": int(time.time()),
                          "views": {i["mid"]: i.get("views", 0) for i in items if i.get("source") == "max"}}

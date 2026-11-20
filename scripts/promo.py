"""Промокоды по расписанию: запускается при каждой синхронизации (раз в 5 минут, GitHub Actions).

Управление — в боте («🎟 Промокоды»), данные — promos.json.
Здесь только то, что должно происходить само: выкладка промокода недели и закрытие истёкших.
"""
import random
import re
import time

import maxapi

TZ = 5 * 3600                     # Уфа, UTC+5
_LAT2CYR = str.maketrans("ABCEHKMOPTXY", "АВСЕНКМОРТХУ")


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


def post_text(c, site_url):
    until = time.strftime("%d.%m", time.gmtime(c["until"] + TZ)) if c.get("until") else ""
    return (f"🎁 Промокод:\n\n{c['code']}\n\n"
            f"Скидка {fmt_disc(c)} на любой заказ.\n"
            + (f"⚡️ Количество ограничено: всего {plural_act(c['limit'])}"
               + (f", осталось {c['limit'] - c.get('used', 0)}" if c.get("used") else "")
               + ". Промокод активируется, когда заказ с ним выполнен — успейте заказать!\n" if c.get("limit") else "")
            + (f"Действует до {until} включительно." if until else "")
            + f"\n\n👉 Как получить скидку: выберите модель и напишите мастеру заказ, добавив в сообщение промокод. "
              f"Каталог: {site_url}\n#промокод")


MASTER_URL = "https://max.ru/u/f9LHodD0cOIASIo07UGTqsR7Fx0LPNcqKwAi9GFiTlXqkxZ281BEJNmiZK0"


def kb(code, site_url, copy=True):
    rows = []
    if copy:
        rows.append([{"type": "clipboard", "text": f"📋 Скопировать промокод {code}", "payload": code}])
    rows.append([{"type": "link", "text": "✍️ Заказать у мастера", "url": MASTER_URL},
                 {"type": "link", "text": "🛍 Каталог", "url": site_url + "?utm_source=promo"}])
    return {"type": "inline_keyboard", "payload": {"buttons": rows}}


def _send(method, params, token, text, code, site_url):
    for copy in (True, False):
        try:
            return maxapi._request(method, "/messages", params, token, {"text": text, "attachments": [kb(code, site_url, copy)]})
        except Exception as e:
            print(f"  ! кнопки промокода ({'с копированием' if copy else 'без'}): {e}")
    return maxapi._request(method, "/messages", params, token, {"text": text})


def _post(token, chat_id, c, site_url):
    r = _send("POST", {"chat_id": chat_id}, token, post_text(c, site_url), c["code"], site_url)
    return ((r.get("message") or {}).get("body") or {}).get("mid")


def _close_post(token, c, reason, site_url):
    if not c.get("mid") or c.get("post_closed"):
        return
    try:
        maxapi._request("PUT", "/messages", {"message_id": c["mid"]}, token,
                        {"text": post_text(c, site_url).replace("🎁 Промокод:", "⛔️ Промокод больше не действует:")
                         + f"\n\n({reason})", "attachments": []})
        c["post_closed"] = True
    except Exception as e:
        print(f"  ! промокод {c['code']}: не удалось закрыть пост: {e}")


def due_slot(sch, now):
    """Ключ недели, если время выкладки на этой неделе уже наступило (и ещё не прошло больше суток)."""
    local = now + TZ
    hh, mm = map(int, sch["hm"].split(":"))
    day = int(local // 86400)
    weekday = (day + 3) % 7                     # 1970-01-01 — четверг
    slot_day = day - ((weekday - sch["wd"]) % 7)
    slot = slot_day * 86400 + hh * 3600 + mm * 60 - TZ
    if slot <= now < slot + 86400:
        return str(slot_day)
    return None


def run(doc, token, chat_id, notifier, site_url, now=None):
    """Меняет doc на месте. Возвращает True, если что-то изменилось."""
    now = now or time.time()
    changed = False
    codes = doc.setdefault("codes", [])

    # 1) закрыть истёкшие
    for c in codes:
        if c.get("personal"):                    # личные коды за подписку: срок проверяет бот, здесь только закрываем
            if c.get("status") == "active" and c.get("until") and now > c["until"]:
                c["status"] = "ended"
                changed = True
            continue
        if c.get("status") == "active" and c.get("until") and now > c["until"]:
            c["status"], c["ended"] = "ended", int(now)
            _close_post(token, c, "срок действия закончился", site_url)
            notifier.send(f"⚪️ Промокод {c['code']} закончился по сроку: использовали {c.get('used', 0)} из {c.get('limit') or '∞'}.")
            changed = True

    # 1б) добавить кнопку «Скопировать» к уже опубликованным постам (один раз)
    for c in codes:
        if c.get("code") and c.get("mid") and c.get("status") == "active" and not c.get("kb"):
            try:
                _send("PUT", {"message_id": c["mid"]}, token, post_text(c, site_url), c["code"], site_url)
                c["kb"] = True
                changed = True
            except Exception as e:
                print(f"  ! кнопки к посту {c['code']}: {e}")

    # 2) промокод недели
    sch = doc.get("schedule") or {}
    if sch.get("on"):
        key = due_slot(sch, now)
        if key and sch.get("last") != key:
            queued = sorted((c for c in codes if c.get("status") == "queued"), key=lambda c: c.get("queued", 0))
            if queued:
                c = queued[0]
            else:
                have = {norm_code(x["code"]) for x in codes if x.get("code")}
                code = random.choice(CODE_WORDS) + str(random.randint(10, 99))
                while code in have:
                    code = random.choice(CODE_WORDS) + str(random.randint(10, 99))
                c = {"code": code, "kind": sch["kind"], "value": sch["value"], "limit": sch.get("limit", 3),
                     "days": sch.get("days", 7), "used": 0, "created": int(now), "auto": True}
                codes.append(c)
            c["status"], c["start"] = "active", int(now)
            c["until"] = int(now) + c["days"] * 86400 if c.get("days") else None
            sch["last"] = key
            changed = True
            try:
                c["mid"] = _post(token, chat_id, c, site_url)
                c["kb"] = True
                notifier.send(f"📣 Автовыкладка: опубликовал промокод {c['code']} — {fmt_disc(c)}, "
                              f"{plural_act(c['limit']) if c.get('limit') else 'без ограничения'}, до {time.strftime('%d.%m', time.gmtime(c['until'] + TZ)) if c.get('until') else '—'}.")
            except Exception as e:
                notifier.send(f"⚠️ Промокод {c['code']} включён, но пост в канал не вышел: {e}")
    return changed

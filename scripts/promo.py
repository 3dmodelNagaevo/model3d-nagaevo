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
    return (f"🎁 Промокод: {c['code']}\n\n"
            f"Скидка {fmt_disc(c)} на любой заказ.\n"
            + (f"⚡️ Количество ограничено: всего {plural_act(c['limit'])}"
               + (f", осталось {c['limit'] - c.get('used', 0)}" if c.get("used") else "")
               + " — скидку получат первые, кто назовёт промокод.\n" if c.get("limit") else "")
            + (f"Действует до {until} включительно." if until else "")
            + f"\n\nНазовите промокод, когда пишете заказ. Каталог: {site_url}\n#промокод")


def _post(token, chat_id, c, site_url):
    r = maxapi.post("/messages", {"chat_id": chat_id}, token, {"text": post_text(c, site_url)})
    return ((r.get("message") or {}).get("body") or {}).get("mid")


def _close_post(token, c, reason, site_url):
    if not c.get("mid") or c.get("post_closed"):
        return
    try:
        maxapi._request("PUT", "/messages", {"message_id": c["mid"]}, token,
                        {"text": post_text(c, site_url).replace("🎁 Промокод:", "⛔️ Промокод больше не действует:")
                         + f"\n\n({reason})"})
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
        if c.get("status") == "active" and c.get("until") and now > c["until"]:
            c["status"], c["ended"] = "ended", int(now)
            _close_post(token, c, "срок действия закончился", site_url)
            notifier.send(f"⚪️ Промокод {c['code']} закончился по сроку: использовали {c.get('used', 0)} из {c.get('limit') or '∞'}.")
            changed = True

    # 2) промокод недели
    sch = doc.get("schedule") or {}
    if sch.get("on"):
        key = due_slot(sch, now)
        if key and sch.get("last") != key:
            queued = sorted((c for c in codes if c.get("status") == "queued"), key=lambda c: c.get("queued", 0))
            if queued:
                c = queued[0]
            else:
                have = {norm_code(x["code"]) for x in codes}
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
                notifier.send(f"📣 Автовыкладка: опубликовал промокод {c['code']} — {fmt_disc(c)}, "
                              f"{plural_act(c['limit']) if c.get('limit') else 'без ограничения'}, до {time.strftime('%d.%m', time.gmtime(c['until'] + TZ)) if c.get('until') else '—'}.")
            except Exception as e:
                notifier.send(f"⚠️ Промокод {c['code']} включён, но пост в канал не вышел: {e}")
    return changed

#!/usr/bin/env python3
"""
Черновик поста из ссылки MakerWorld (запускается GitHub Actions по команде бота).

  python3 scripts/makerworld.py prepare <url> <uid>   — скачать данные модели, фото → drafts.json, drafts/
  python3 scripts/makerworld.py notify <uid>          — прислать владельцу предпросмотр в MAX

Почему через GitHub: makerworld.com работает через Cloudflare, а из России (Yandex Cloud)
такие сайты открываются с ограничениями.
"""
import html
import json
import os
import re
import sys
import time
import urllib.request

import maxapi
from maxapi import ROOT, log
import photos as photolib

DRAFTS = ROOT / "drafts.json"
DRAFT_DIR = ROOT / "drafts"
REPO = os.environ.get("REPO", "3dmodelNagaevo/model3d-nagaevo")
RAW = f"https://raw.githubusercontent.com/{REPO}/main/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36")
MAX_PICS = 6

# Лицензии, при которых продавать печать нельзя без разрешения автора
NONCOMMERCIAL = re.compile(r"\bNC\b|non.?commercial|некоммер|standard digital file license|personal use", re.I)
COMMERCIAL_OK = re.compile(r"^(CC0|CC[ -]?BY|BY|BY[ -]SA|BY[ -]ND|CC[ -]BY[ -]SA|CC[ -]BY[ -]ND|Public Domain)$", re.I)


def load_drafts():
    try:
        return json.loads(DRAFTS.read_text("utf-8"))
    except Exception:
        return {}


def save_drafts(d):
    DRAFTS.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", "utf-8")


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru,en;q=0.8",
                                               "Accept": "text/html,application/xhtml+xml,*/*"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read().decode("utf-8", "replace")


def meta(page, prop):
    m = re.search(rf'<meta[^>]+(?:property|name)="{re.escape(prop)}"[^>]+content="([^"]*)"', page) or \
        re.search(rf'<meta[^>]+content="([^"]*)"[^>]+(?:property|name)="{re.escape(prop)}"', page)
    return html.unescape(m.group(1)).strip() if m else ""


def text_of(h):
    h = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", h or "", flags=re.I)
    h = re.sub(r"<[^>]+>", "", h)
    h = html.unescape(h)
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", h)).strip()


def parse_model(page, url):
    """Название, описание, фото, лицензия и автор со страницы модели."""
    title = meta(page, "og:title") or (re.search(r"<title>([^<]+)</title>", page) or [None, ""])[1]
    title = re.sub(r"\s*[-|]\s*MakerWorld.*$", "", html.unescape(title)).strip()
    desc = meta(page, "og:description") or meta(page, "description")

    data = None
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if m:
        try:
            data = json.loads(m.group(1))
        except Exception:
            data = None
    design = {}
    if data:
        def walk(o):
            if isinstance(o, dict):
                if "title" in o and ("license" in o or "designExtension" in o or "coverUrl" in o):
                    return o
                for v in o.values():
                    r = walk(v)
                    if r:
                        return r
            elif isinstance(o, list):
                for v in o:
                    r = walk(v)
                    if r:
                        return r
            return None
        design = walk(data) or {}
    if design.get("title"):
        title = design["title"]
    if design.get("summary"):
        desc = text_of(design["summary"])

    lic = design.get("license") or ""
    if not lic:
        lm = re.search(r'"license"\s*:\s*"([^"]{1,80})"', page)
        lic = lm.group(1) if lm else ""
    author = ""
    for key in ("designCreator", "creator", "user"):
        c = design.get(key)
        if isinstance(c, dict) and (c.get("name") or c.get("nickname")):
            author = c.get("name") or c.get("nickname")
            break
    if not author:
        am = re.search(r'"designCreator"\s*:\s*\{[^{}]*?"name"\s*:\s*"([^"]+)"', page)
        author = am.group(1) if am else ""

    # фото самой модели (папка /design/), без миниатюр профилей печати и аватаров
    pics, seen = [], set()
    for u in re.findall(r'https://[a-z0-9.-]*bblmw\.com/makerworld/model/[^"\'\s)\\]+?/design/[^"\'\s)\\]+?\.(?:jpe?g|png|webp)', page, re.I):
        key = u.split("?")[0]
        if key not in seen:
            seen.add(key)
            pics.append(key)
    cover = design.get("coverUrl") or meta(page, "og:image")
    if cover:
        ck = cover.split("?")[0]
        if ck in seen:
            pics.remove(ck)
        pics.insert(0, ck)

    # лицензия: разрешена ли продажа печати
    page_low = (desc or "") + " " + text_of(design.get("summary") or "")
    commercial_note = bool(re.search(r"commercial (licen[cs]e|membership|use is allowed only|tier)|коммерческ", page_low, re.I))
    if NONCOMMERCIAL.search(lic):
        lic_status = "forbidden"
    elif COMMERCIAL_OK.match(lic.strip()):
        lic_status = "warn" if commercial_note else "ok"
    else:
        lic_status = "unknown"

    return {"title": title.strip()[:120], "desc_src": (desc or "").strip()[:1500], "pics": pics[:MAX_PICS],
            "license": lic or "не указана", "license_status": lic_status, "author": author, "url": url}


def prepare(url, uid):
    m = re.search(r"makerworld\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?models/(\d+)", url)
    if not m:
        raise SystemExit("это не ссылка на модель MakerWorld")
    mid = m.group(1)
    page = fetch(url)
    info = parse_model(page, url)
    DRAFT_DIR.mkdir(exist_ok=True)
    for old in DRAFT_DIR.glob(f"{uid}_*"):
        old.unlink()
    saved = []
    for n, pic in enumerate(info.pop("pics")):
        try:
            data = urllib.request.urlopen(urllib.request.Request(pic, headers={"User-Agent": UA}), timeout=40).read()
            dest = DRAFT_DIR / f"{uid}_{mid}_{n}.jpg"
            photolib.save_photo(data, dest)
            photolib.thumb_path(dest).unlink(missing_ok=True)
            saved.append(f"drafts/{dest.name}")
        except Exception as e:
            log(f"  ! фото {pic}: {e}")
    drafts = load_drafts()
    drafts[str(uid)] = {
        **info, "model_id": mid, "photos": saved, "use": list(range(1, len(saved) + 1)),
        "name": info["title"], "desc": "", "price": None, "size": "по желанию",
        "color": "согласовывать в ЛС", "tags": [], "created": int(time.time()),
    }
    save_drafts(drafts)
    log(f"Черновик: {info['title']} — фото {len(saved)}, лицензия {info['license']} ({info['license_status']})")


# ---------------------------------------------------------------- текст поста и предпросмотр

LICENSE_TEXT = {
    "ok": "✅ Лицензия разрешает коммерческое использование.",
    "warn": "⚠️ Лицензия открытая, но автор в описании упоминает коммерческую лицензию — проверьте условия на странице модели.",
    "unknown": "⚠️ Не смог определить, разрешена ли продажа печати. Проверьте лицензию на странице модели.",
    "forbidden": "⛔ Лицензия запрещает коммерческое использование (продажу печати) без разрешения автора. "
                 "Опубликовать такой товар бот не может. Можно купить коммерческую лицензию у автора и выложить пост вручную.",
}


def post_text(d):
    lines = [d["name"]]
    if d.get("desc"):
        lines.append(d["desc"])
    lines += [f"- Размер: ({d.get('size') or 'по желанию'})", f"- Цвет: {d.get('color') or 'согласовывать в ЛС'}",
              f"- Стоимость: {d['price']} ₽" if d.get("price") else "- Стоимость: ❗укажите цену"]
    if d.get("author"):
        lines.append(f"Автор модели: {d['author']} (MakerWorld)")
    if d.get("tags"):
        lines.append(" ".join("#" + t for t in d["tags"]))
    return "\n".join(lines)


def preview_text(d):
    return ("📝 Черновик поста:\n\n" + post_text(d) +
            f"\n\n📷 Фото: {', '.join(map(str, d['use'])) or 'нет'} из {len(d['photos'])}"
            f"\n📄 Лицензия: {d['license']}\n{LICENSE_TEXT[d['license_status']]}"
            + (f"\n\nОписание с сайта (для справки):\n{d['desc_src'][:500]}" if d.get("desc_src") else "")
            + "\n\nЧтобы поправить, пришлите строки:\nНазвание: …\nЦена: 500\nОписание: …\nРазмер: …\nФото: 1 3 4\n#категория")


def notify(uid):
    token = os.environ["MAX_TOKEN"]
    d = load_drafts().get(str(uid))
    if not d:
        return
    for n, ph in enumerate(d["photos"], 1):
        try:
            maxapi.post("/messages", {"user_id": uid}, token,
                        {"text": f"Фото {n}", "attachments": [{"type": "image", "payload": {"url": RAW + ph}}]})
        except Exception as e:
            log(f"  ! фото {n}: {e}")
    buttons = [[{"type": "message", "text": "✅ Опубликовать"}, {"type": "message", "text": "❌ Отменить черновик"}],
               [{"type": "link", "text": "🔗 Страница модели", "url": d["url"]}]]
    maxapi.post("/messages", {"user_id": uid, "disable_link_preview": "true"}, token,
                {"text": preview_text(d)[:3900], "attachments": [{"type": "inline_keyboard", "payload": {"buttons": buttons}}]})


def fail(uid, reason):
    token = os.environ.get("MAX_TOKEN", "")
    try:
        maxapi.post("/messages", {"user_id": uid}, token,
                    {"text": f"❌ Не получилось сделать черновик: {reason}\n\nМожно опубликовать вручную: пришлите фото с подписью /пост."})
    except Exception as e:
        log(f"  ! {e}")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "prepare":
        try:
            prepare(sys.argv[2], sys.argv[3])
        except Exception as e:
            log(f"ОШИБКА: {e}")
            fail(sys.argv[3], f"сайт MakerWorld не отдал страницу ({e})")
            sys.exit(1)
    elif cmd == "notify":
        notify(sys.argv[2])

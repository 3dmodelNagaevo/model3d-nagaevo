"""Разовые посты в канал «от бота»: outbox.json → канал. Запускается при синхронизации.

Пост выходит, когда его картинки уже доступны на сайте (после выкладки), и отмечается done.
"""
import urllib.request

import maxapi


def _url_ok(url):
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "model3d-sync"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200
    except Exception:
        return False


def run(doc, token, chat_id, notifier, site_url):
    changed = False
    for p in doc.get("posts", []):
        if p.get("done"):
            continue
        imgs = [site_url + i for i in p.get("images", [])]
        if not all(_url_ok(u) for u in imgs):
            print(f"  пост «{p['id']}»: жду, пока картинки появятся на сайте")
            continue
        atts = [{"type": "image", "payload": {"url": u}} for u in imgs]
        if p.get("buttons"):
            atts.append({"type": "inline_keyboard", "payload": {"buttons": [
                [{"type": "link", "text": t, "url": u} for t, u in row] for row in p["buttons"]]}})
        try:
            r = maxapi.post("/messages", {"chat_id": chat_id}, token, {"text": p["text"], "attachments": atts})
            p["done"] = True
            p["mid"] = ((r.get("message") or {}).get("body") or {}).get("mid")
            notifier.send(f"📣 Опубликовал в канале пост «{p.get('title') or p['id']}».")
        except Exception as e:
            p["error"] = str(e)[:300]
            p["done"] = True                      # не повторяем бесконечно; ошибку видно в файле
            notifier.send(f"⚠️ Пост «{p.get('title') or p['id']}» не вышел: {e}")
        changed = True
    return changed

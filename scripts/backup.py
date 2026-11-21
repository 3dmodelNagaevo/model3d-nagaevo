"""Еженедельная резервная копия: архив сайта, данных, кода и истории → владельцам в личку бота.

Ничего не хранится ни в облаке, ни на GitHub: архив собирается во временной машине GitHub Actions,
отправляется в MAX и удаляется вместе с машиной.
"""
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import time
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import maxapi  # noqa: E402
from maxapi import ROOT, log  # noqa: E402

TZ = dt.timezone(dt.timedelta(hours=5))


def build(out):
    date = dt.datetime.now(TZ).strftime("%d.%m.%Y")
    tmp = pathlib.Path("/tmp/backup")
    tmp.mkdir(exist_ok=True)
    bundle = tmp / "история_изменений.bundle"
    subprocess.run(["git", "bundle", "create", str(bundle), "--all"], cwd=ROOT, check=True, capture_output=True)
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.split("\n")
    readme = (ROOT / "scripts" / "backup_readme.txt").read_text(encoding="utf-8").replace("{DATE}", date)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr("ПРОЧИТАЙ_МЕНЯ.txt", readme)
        for f in files:
            if f and (ROOT / f).is_file():
                z.write(ROOT / f, "сайт_и_бот/" + f)
        z.write(bundle, bundle.name)
    return date


def main():
    token = os.environ.get("MAX_TOKEN", "").strip()
    state = json.loads((ROOT / "state.json").read_text(encoding="utf-8"))
    to = state.get("chief") or state.get("owners") or []
    if os.environ.get("BACKUP_TO", "").strip().isdigit():          # запросили из бота — только ему
        to = [int(os.environ["BACKUP_TO"])]
    else:                                                          # еженедельная — тем, кто не отключил
        try:
            st = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))
            to = [u for u in to if (st.get("notify") or {}).get(str(u), {}).get("backup", True) is not False]
        except Exception:
            pass
    date = dt.datetime.now(TZ).strftime("%Y-%m-%d")
    out = pathlib.Path(f"/tmp/backup_3dnagaevo_{date}.zip")
    nice = build(out)
    size = out.stat().st_size / 1024 / 1024
    log(f"Архив: {out.name}, {size:.1f} МБ, получателей: {len(to)}")
    payload = maxapi.upload_file(out, token)
    text = (f"🗄 Резервная копия сайта и бота на {nice} ({size:.0f} МБ).\n"
            "Внутри: сайт, фото, каталог, продажи, отзывы, промокоды, ссылки, код бота и вся история изменений. "
            "Инструкция — в файле «ПРОЧИТАЙ_МЕНЯ.txt». Секретных ключей в архиве нет.\n\n"
            "Сохраните файл себе (Скачать) — если что-то пропадёт, по нему всё восстанавливается.")
    for uid in to:
        for attempt in range(6):
            try:
                maxapi.post("/messages", {"user_id": uid}, token,
                            {"text": text, "attachments": [{"type": "file", "payload": payload}]})
                log(f"  отправлено {uid}")
                break
            except maxapi.ApiError as e:
                if "not.ready" in str(e) and attempt < 5:
                    time.sleep(5)
                    continue
                log(f"  ! {uid}: {e}")
                break


if __name__ == "__main__":
    main()

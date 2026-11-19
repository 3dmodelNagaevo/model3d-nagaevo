"""Отдельные страницы товаров (m/N.html) с Open Graph — для красивого превью ссылок
в MAX, Telegram, ВК и для поиска Яндекса. Человек со страницы сразу попадает
в каталог с открытой карточкой товара."""
import html
import pathlib

from maxapi import ROOT, log

OUT = ROOT / "m"
QR_DIR = ROOT / "qr"
SITE_NAME = "3D модели Нагаево на заказ"
DEFAULT_DESC = "Печать на 3D-принтере на заказ в Нагаево. Размер и цвет — по желанию. Заказ в MAX, Telegram или по телефону."

TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — {site}</title>
<meta name="description" content="{desc}">
<link rel="canonical" href="{url}">
<meta property="og:type" content="product">
<meta property="og:site_name" content="{site}">
<meta property="og:title" content="{og_title}">
<meta property="og:description" content="{desc}">
<meta property="og:url" content="{url}">
{og_image}
<meta name="twitter:card" content="summary_large_image">
<style>
body{{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#f7f7f4;color:#16181d}}
main{{max-width:640px;margin:0 auto;padding:24px 20px}}
img{{width:100%;border-radius:16px;display:block}}
h1{{font-size:26px;margin:18px 0 6px}}
.p{{font-size:22px;font-weight:800}}
a.b{{display:inline-block;margin-top:16px;background:#5b3df5;color:#fff;padding:12px 18px;border-radius:12px;text-decoration:none;font-weight:700}}
</style>
</head>
<body>
<main>
{img}
<h1>{title}</h1>
<div class="p">{price}</div>
<p>{desc}</p>
<a class="b" href="../#m{no}">Открыть в каталоге и заказать</a>
</main>
<script>location.replace("../#m{no}");</script>
</body>
</html>
"""


def _price(p):
    return "Цена по запросу" if p is None else f"от {p:,} ₽".replace(",", " ")


def generate(items, site_url):
    OUT.mkdir(exist_ok=True)
    keep = set()
    for it in items:
        if it.get("hidden") or not it.get("no"):
            continue
        no = it["no"]
        e = html.escape
        photo = (it.get("photos") or [None])[0]
        desc = it.get("desc") or DEFAULT_DESC
        price = _price(it.get("price"))
        page = TEMPLATE.format(
            site=e(SITE_NAME), title=e(it["title"]), no=no,
            og_title=e(f"{it['title']} — {price}"),
            desc=e(desc if not it.get("desc") else f"{desc} Размер: {it.get('size', 'по желанию').lower()}. Цвет — на выбор.")[:300],
            url=e(f"{site_url}m/{no}.html"),
            og_image=(f'<meta property="og:image" content="{e(site_url + photo)}">' if photo else ""),
            img=(f'<img src="../{e(photo)}" alt="{e(it["title"])}">' if photo else ""),
            price=e(price),
        )
        path = OUT / f"{no}.html"
        keep.add(path.name)
        if not path.exists() or path.read_text("utf-8") != page:
            path.write_text(page, "utf-8")
    for f in OUT.glob("*.html"):
        if f.name not in keep:
            f.unlink()


def make_qr(site_url):
    """QR-коды: для листовки (с меткой, чтобы видеть переходы в Метрике) и обычный."""
    try:
        import qrcode
    except ImportError:
        return
    QR_DIR.mkdir(exist_ok=True)
    codes = {
        "flyer.png": site_url + "?utm_source=flyer&utm_medium=qr",
        "channel.png": site_url + "?utm_source=max_channel&utm_medium=post",
        "site.png": site_url,
    }
    for name, url in codes.items():
        path = QR_DIR / name
        marker = QR_DIR / (name + ".url")
        if path.exists() and marker.exists() and marker.read_text() == url:
            continue
        img = qrcode.make(url, box_size=12, border=2)
        img.save(path)
        marker.write_text(url)
        log(f"  QR: {name} → {url}")

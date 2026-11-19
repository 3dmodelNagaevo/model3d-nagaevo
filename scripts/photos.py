"""Сжатие фото: полноразмерное (до 1200 px) и миниатюра для карточек (до 600 px)."""
import io
import pathlib

FULL_SIDE = 1200
THUMB_SIDE = 600

try:
    from PIL import Image, ImageOps
except ImportError:  # без Pillow просто сохраняем как есть
    Image = None


def thumb_path(full: pathlib.Path) -> pathlib.Path:
    return full.parent / "t" / full.name


def save_photo(data: bytes, dest: pathlib.Path):
    """Сохраняет сжатую копию в dest и миниатюру в dest.parent/t/."""
    t = thumb_path(dest)
    t.parent.mkdir(parents=True, exist_ok=True)
    if Image is None:
        dest.write_bytes(data)
        t.write_bytes(data)
        return
    im = Image.open(io.BytesIO(data))
    im = ImageOps.exif_transpose(im).convert("RGB")
    for side, path, q in ((FULL_SIDE, dest, 82), (THUMB_SIDE, t, 78)):
        c = im.copy()
        c.thumbnail((side, side), Image.LANCZOS)
        c.save(path, "JPEG", quality=q, optimize=True, progressive=True)


def recompress_existing(folder: pathlib.Path):
    """Пережать уже скачанные фото и сделать недостающие миниатюры."""
    if Image is None:
        return 0
    n = 0
    for f in sorted(folder.glob("*.jpg")):
        need_thumb = not thumb_path(f).exists()
        with Image.open(f) as im:
            big = max(im.size) > FULL_SIDE
        if big or need_thumb:
            save_photo(f.read_bytes(), f)
            n += 1
    return n

"""Договор возмездного оказания услуг (физлицо — физлицо) в PDF и Word.

Только стандартная библиотека Python: бот работает в облачной функции без дополнительных пакетов.
PDF: шрифт Liberation Sans (урезанный до латиницы и кириллицы, fonts/contract-*.ttf) встраивается как TrueType/Identity-H.
Word: .docx собирается из XML вручную.
"""
import io
import struct
import zipfile
import zlib
from xml.sax.saxutils import escape as _x

# ---------------------------------------------------------------- сумма прописью

_U = ["", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]
_UF = ["", "одна", "две"] + _U[3:]
_T = ["десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать", "шестнадцать",
      "семнадцать", "восемнадцать", "девятнадцать"]
_D = ["", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят", "восемьдесят", "девяносто"]
_H = ["", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот", "восемьсот", "девятьсот"]


def _plural(n, forms):
    n = abs(n) % 100
    if 10 < n < 20:
        return forms[2]
    n %= 10
    return forms[0] if n == 1 else forms[1] if 2 <= n <= 4 else forms[2]


def _triple(n, fem):
    w = [_H[n // 100]]
    r = n % 100
    if 10 <= r < 20:
        w.append(_T[r - 10])
    else:
        w.append(_D[r // 10])
        w.append((_UF if fem else _U)[r % 10])
    return [x for x in w if x]


def rub_words(amount):
    n = int(round(amount))
    if n == 0:
        return "ноль рублей"
    parts = []
    for div, forms, fem in ((10 ** 6, ("миллион", "миллиона", "миллионов"), False),
                            (1000, ("тысяча", "тысячи", "тысяч"), True)):
        k = n // div
        if k:
            parts += _triple(k % 1000, fem) + [_plural(k, forms)]
        n %= div
    parts += _triple(n, False)
    total = int(round(amount))
    return " ".join(parts) + " " + _plural(total, ("рубль", "рубля", "рублей"))


def money(v):
    v = round(float(v), 2)
    s = f"{v:,.2f}".replace(",", " ").replace(".", ",")
    return s[:-3] if s.endswith(",00") else s


# ---------------------------------------------------------------- содержание договора

def build(d):
    """d: number, date, city, executor{name, phone}, customer{name, phone}, items[{name, qty, price}],
    days, prepay (%), delivery, warranty_days. Возвращает список блоков."""
    ex, cu = d.get("executor") or {}, d.get("customer") or {}
    items = d.get("items") or []
    total = sum(float(i["qty"]) * float(i["price"]) for i in items)
    pre = int(d.get("prepay") or 0)
    pre_sum = round(total * pre / 100)
    cust = cu.get("name") or "_" * 34
    blank = "_" * 22
    B = []
    B.append(("title", f"ДОГОВОР № {d['number']}"))
    B.append(("subtitle", "возмездного оказания услуг"))
    B.append(("cityline", (f"{d.get('city') or 'с. Нагаево'}", d["date"])))
    B.append(("p", f"{ex.get('name') or blank}, именуемый(ая) в дальнейшем «Исполнитель», с одной стороны, и {cust}, "
                   f"именуемый(ая) в дальнейшем «Заказчик», с другой стороны, вместе именуемые «Стороны», "
                   f"заключили настоящий договор о нижеследующем."))
    B.append(("h", "1. Предмет договора"))
    B.append(("p", "1.1. Исполнитель обязуется по заданию Заказчика оказать услуги по изготовлению изделий методом "
                   "3D-печати (а при необходимости — по созданию 3D-модели), а Заказчик обязуется принять и оплатить услуги."))
    B.append(("p", "1.2. Перечень, количество и стоимость:"))
    rows = [[str(k), i["name"], money(i["qty"]), money(i["price"]), money(float(i["qty"]) * float(i["price"]))]
            for k, i in enumerate(items, 1)]
    B.append(("table", {"head": ["№", "Наименование", "Кол-во", "Цена, руб.", "Сумма, руб."], "rows": rows,
                        "total": money(total)}))
    B.append(("p", "1.3. Цвет, размеры, материал и иные параметры изделий согласуются Сторонами в переписке "
                   "(MAX, Telegram или по телефону) и являются частью настоящего договора."))
    B.append(("h", "2. Стоимость и порядок оплаты"))
    B.append(("p", f"2.1. Общая стоимость услуг составляет {money(total)} ({rub_words(total)})."))
    if pre:
        B.append(("p", f"2.2. Заказчик вносит предоплату {pre}% — {money(pre_sum)} руб. при оформлении заказа. "
                       f"Оставшаяся сумма {money(total - pre_sum)} руб. оплачивается при получении изделий."))
    else:
        B.append(("p", "2.2. Оплата производится при получении изделий."))
    B.append(("p", "2.3. Оплата — наличными или банковским переводом по согласованию Сторон."))
    B.append(("h", "3. Сроки и порядок сдачи"))
    days = d.get("days")
    B.append(("p", f"3.1. Срок оказания услуг — {days} с момента согласования заказа"
                   + (" и внесения предоплаты." if pre else ".") if days else
              "3.1. Срок оказания услуг согласуется Сторонами в переписке."))
    B.append(("p", f"3.2. Способ получения: {d.get('delivery') or 'самовывоз'}."))
    B.append(("p", "3.3. Услуги считаются оказанными с момента передачи изделий Заказчику. "
                   "Заказчик осматривает изделия при получении."))
    B.append(("h", "4. Качество изделий"))
    B.append(("p", "4.1. Изделия, изготовленные методом 3D-печати, могут иметь видимые слои печати, следы поддержек, "
                   "незначительные отклонения размеров (до ±0,5 мм) и оттенка цвета от изображений. "
                   "Это особенность технологии и не является недостатком."))
    wd = int(d.get("warranty_days") or 7)
    B.append(("p", f"4.2. Если изделие имеет дефект по вине Исполнителя (брак печати, поломка не по вине Заказчика), "
                   f"Исполнитель бесплатно изготавливает его заново при обращении Заказчика в течение {wd} дней с момента получения."))
    B.append(("h", "5. Прочие условия"))
    B.append(("p", "5.1. Заказчик вправе отказаться от заказа, оплатив Исполнителю фактически понесённые расходы "
                   "(ст. 782 ГК РФ). Предоплата за вычетом этих расходов возвращается Заказчику."))
    B.append(("p", "5.2. Модели и файлы, предоставленные Заказчиком, используются только для выполнения заказа. "
                   "Заказчик подтверждает, что вправе их использовать."))
    B.append(("p", "5.3. Споры решаются путём переговоров, а при недостижении согласия — в порядке, установленном "
                   "законодательством Российской Федерации."))
    B.append(("p", "5.4. Договор направляется Заказчику в мессенджере (MAX, Telegram) и считается заключённым, "
                   "когда Заказчик подтвердит согласие с его условиями ответным сообщением (например, «Ознакомлен, согласен») "
                   "или внесёт оплату (ст. 434, 438 ГК РФ). Подписи Сторон не требуются. "
                   "Договор действует до полного исполнения обязательств."))
    B.append(("h", "6. Стороны"))
    B.append(("sign", [
        ("Исполнитель", ex.get("name") or blank, ex.get("phone") or blank),
        ("Заказчик", cu.get("name") or blank, cu.get("phone") or blank)]))
    return B


# ---------------------------------------------------------------- Word (.docx)

def _r(text, bold=False, size=21):
    rpr = f'<w:rPr>{"<w:b/>" if bold else ""}<w:sz w:val="{size}"/><w:szCs w:val="{size}"/></w:rPr>'
    return f'<w:r>{rpr}<w:t xml:space="preserve">{_x(text)}</w:t></w:r>'


def _p(runs, align="both", before=0, after=80, keep=False):
    ppr = (f'<w:pPr>{"<w:keepNext/>" if keep else ""}<w:spacing w:before="{before}" w:after="{after}" w:line="276" w:lineRule="auto"/>'
           f'<w:jc w:val="{align}"/></w:pPr>')
    return f"<w:p>{ppr}{runs}</w:p>"


def _cell(text, w, bold=False, align="left", borders=True):
    b = ('<w:tcBorders><w:top w:val="single" w:sz="4" w:color="808080"/><w:left w:val="single" w:sz="4" w:color="808080"/>'
         '<w:bottom w:val="single" w:sz="4" w:color="808080"/><w:right w:val="single" w:sz="4" w:color="808080"/></w:tcBorders>'
         if borders else '<w:tcBorders><w:top w:val="nil"/><w:left w:val="nil"/><w:bottom w:val="nil"/><w:right w:val="nil"/></w:tcBorders>')
    shade = '<w:shd w:val="clear" w:color="auto" w:fill="EEF0F4"/>' if bold and borders else ""
    paras = "".join(_p(_r(t, bold), align, 0, 0) for t in (text.split("\n") if isinstance(text, str) else text))
    return f'<w:tc><w:tcPr><w:tcW w:w="{w}" w:type="dxa"/>{b}{shade}</w:tcPr>{paras}</w:tc>'


def _table(widths, rows):
    grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)
    return (f'<w:tbl><w:tblPr><w:tblW w:w="{sum(widths)}" w:type="dxa"/><w:tblLayout w:type="fixed"/>'
            f'<w:tblCellMar><w:left w:w="80" w:type="dxa"/><w:right w:w="80" w:type="dxa"/></w:tblCellMar></w:tblPr>'
            f'<w:tblGrid>{grid}</w:tblGrid>{"".join(rows)}</w:tbl>')


def to_docx(blocks):
    W = 9638                      # ширина текста A4 при полях 2 см (в двадцатых долях пункта)
    body = []
    for kind, v in blocks:
        if kind == "title":
            body.append(_p(_r(v, True, 28), "center", 0, 0))
        elif kind == "subtitle":
            body.append(_p(_r(v, True, 24), "center", 0, 200))
        elif kind == "cityline":
            body.append(_table([W // 2, W - W // 2], ['<w:tr>' + _cell(v[0], W // 2, borders=False)
                                                       + _cell(v[1], W - W // 2, align="right", borders=False) + '</w:tr>']))
            body.append(_p("", after=120))
        elif kind == "h":
            body.append(_p(_r(v, True, 22), "left", 160, 80, keep=True))
        elif kind == "p":
            body.append(_p(_r(v)))
        elif kind == "table":
            ws = [520, W - 520 - 1100 - 1500 - 1600, 1100, 1500, 1600]
            rows = ['<w:tr><w:trPr><w:tblHeader/></w:trPr>' + "".join(
                _cell(h, ws[k], True, "center") for k, h in enumerate(v["head"])) + '</w:tr>']
            for r in v["rows"]:
                rows.append('<w:tr><w:trPr><w:cantSplit/></w:trPr>' + "".join(
                    _cell(c, ws[k], align="left" if k == 1 else "center" if k == 0 else "right") for k, c in enumerate(r)) + '</w:tr>')
            rows.append('<w:tr>' + _cell("", ws[0]) + _cell("Итого", ws[1], True) + _cell("", ws[2]) + _cell("", ws[3])
                        + _cell(v["total"], ws[4], True, "right") + '</w:tr>')
            body.append(_table(ws, rows))
            body.append(_p("", after=80))
        elif kind == "sign":
            cells = []
            for role, name, phone in v:
                cells.append(_cell([role, name, f"Тел.: {phone}"], W // 2, borders=False))
            body.append(_table([W // 2, W - W // 2], ['<w:tr>' + cells[0] + cells[1] + '</w:tr>']))
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
           + "".join(body) +
           '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" '
           'w:header="567" w:footer="567" w:gutter="0"/></w:sectPr></w:body></w:document>')
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
              '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="Arial" w:cs="Arial"/>'
              '<w:sz w:val="21"/><w:szCs w:val="21"/><w:lang w:val="ru-RU"/></w:rPr></w:rPrDefault>'
              '<w:pPrDefault><w:pPr><w:spacing w:after="80"/></w:pPr></w:pPrDefault></w:docDefaults>'
              '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style></w:styles>')
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
          '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
          '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>')
    drels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
             '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
             '</Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/_rels/document.xml.rels", drels)
        z.writestr("word/document.xml", doc)
        z.writestr("word/styles.xml", styles)
    return buf.getvalue()


# ---------------------------------------------------------------- PDF

class TTF:
    """Минимальный разбор TrueType: соответствие символ → глиф, ширины, метрики."""

    def __init__(self, data):
        self.data = data
        n = struct.unpack(">H", data[4:6])[0]
        self.tab = {}
        for k in range(n):
            tag, _, off, ln = struct.unpack(">4sIII", data[12 + 16 * k: 28 + 16 * k])
            self.tab[tag.decode()] = (off, ln)
        h = self.tab["head"][0]
        self.upm = struct.unpack(">H", data[h + 18:h + 20])[0]
        self.bbox = struct.unpack(">hhhh", data[h + 36:h + 44])
        hh = self.tab["hhea"][0]
        self.asc, self.desc = struct.unpack(">hh", data[hh + 4:hh + 8])
        nhm = struct.unpack(">H", data[hh + 34:hh + 36])[0]
        hm = self.tab["hmtx"][0]
        self.adv = [struct.unpack(">H", data[hm + 4 * k:hm + 4 * k + 2])[0] for k in range(nhm)]
        self.cmap = self._cmap()

    def _cmap(self):
        d, base = self.data, self.tab["cmap"][0]
        n = struct.unpack(">H", d[base + 2:base + 4])[0]
        sub = None
        for k in range(n):
            pid, eid, off = struct.unpack(">HHI", d[base + 4 + 8 * k: base + 12 + 8 * k])
            if pid == 3 and eid == 1:
                sub = base + off
        m = {}
        if sub is None or struct.unpack(">H", d[sub:sub + 2])[0] != 4:
            return m
        seg = struct.unpack(">H", d[sub + 6:sub + 8])[0] // 2
        ends = struct.unpack(f">{seg}H", d[sub + 14:sub + 14 + 2 * seg])
        p = sub + 16 + 2 * seg
        starts = struct.unpack(f">{seg}H", d[p:p + 2 * seg])
        deltas = struct.unpack(f">{seg}h", d[p + 2 * seg:p + 4 * seg])
        ro_pos = p + 4 * seg
        ros = struct.unpack(f">{seg}H", d[ro_pos:ro_pos + 2 * seg])
        for i in range(seg):
            for c in range(starts[i], ends[i] + 1):
                if c == 0xFFFF:
                    continue
                if ros[i] == 0:
                    g = (c + deltas[i]) & 0xFFFF
                else:
                    a = ro_pos + 2 * i + ros[i] + 2 * (c - starts[i])
                    g = struct.unpack(">H", d[a:a + 2])[0]
                    if g:
                        g = (g + deltas[i]) & 0xFFFF
                if g:
                    m[c] = g
        return m

    def gid(self, ch):
        return self.cmap.get(ord(ch)) or self.cmap.get(ord("?"), 0)

    def width(self, g):
        return self.adv[g] if g < len(self.adv) else self.adv[-1]

    def text_w(self, s, size):
        return sum(self.width(self.gid(c)) for c in s) * size / self.upm


class PDF:
    W, H, M = 595.28, 841.89, 56.7          # A4, поля 2 см

    def __init__(self, reg, bold):
        self.f = {"R": TTF(reg), "B": TTF(bold)}
        self.used = {"R": {}, "B": {}}
        self.pages, self.cur, self.y = [], None, 0
        self.new_page()

    def new_page(self):
        self.cur = []
        self.pages.append(self.cur)
        self.y = self.H - self.M

    def need(self, h):
        if self.y - h < self.M:
            self.new_page()

    def _hex(self, font, s):
        f, used = self.f[font], self.used[font]
        out = []
        for c in s:
            g = f.gid(c)
            used[g] = c
            out.append(f"{g:04X}")
        return "<" + "".join(out) + ">"

    def text(self, x, y, s, size=10.5, font="R"):
        self.cur.append(f"BT /F{font} {size} Tf {x:.2f} {y:.2f} Td {self._hex(font, s)} Tj ET")

    def line(self, x1, y1, x2, y2, w=0.6, gray=0.5):
        self.cur.append(f"{gray} G {w} w {x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S")

    def rect(self, x, y, w, h, gray=0.94):
        self.cur.append(f"{gray} g {x:.2f} {y:.2f} {w:.2f} {h:.2f} re f 0 g")

    def wrap(self, s, width, size, font="R"):
        f = self.f[font]
        lines = []
        for para in s.split("\n"):
            words, line = para.split(" "), ""
            for w in words:
                t = (line + " " + w) if line else w
                if f.text_w(t, size) <= width or not line:
                    line = t
                else:
                    lines.append(line)
                    line = w
            lines.append(line)
        return lines

    def para(self, s, size=10.5, font="R", align="justify", before=0, after=4, keep=0):
        lh = size * 1.38
        lines = self.wrap(s, self.W - 2 * self.M, size, font)
        self.y -= before
        self.need(lh * min(len(lines), 2) + keep)
        f = self.f[font]
        for k, ln in enumerate(lines):
            self.need(lh)
            self.y -= lh
            x = self.M
            tw = f.text_w(ln, size)
            if align == "center":
                x = (self.W - tw) / 2
            elif align == "right":
                x = self.W - self.M - tw
            if align == "justify" and k < len(lines) - 1 and ln.count(" "):
                gap = (self.W - 2 * self.M - tw) / ln.count(" ")
                xx = x
                for w in ln.split(" "):
                    self.text(xx, self.y, w, size, font)
                    xx += f.text_w(w + " ", size) + gap
            else:
                self.text(x, self.y, ln, size, font)
        self.y -= after

    def table(self, head, rows, total):
        size, pad = 9.5, 4
        full = self.W - 2 * self.M
        ws = [24, 0, 50, 72, 80]
        ws[1] = full - sum(ws)
        aligns = ["center", "left", "right", "right", "right"]

        def row(cells, font="R", shade=False):
            wrapped = [self.wrap(c, ws[k] - 2 * pad, size, font) for k, c in enumerate(cells)]
            h = max(len(w) for w in wrapped) * size * 1.3 + 2 * pad
            self.need(h)
            top = self.y
            if shade:
                self.rect(self.M, top - h, full, h)
            x = self.M
            for k, lines in enumerate(wrapped):
                yy = top - pad
                for ln in lines:
                    yy -= size * 1.3
                    tw = self.f[font].text_w(ln, size)
                    tx = x + pad if aligns[k] == "left" else (x + ws[k] - pad - tw if aligns[k] == "right" else x + (ws[k] - tw) / 2)
                    self.text(tx, yy + 2.5, ln, size, font)
                x += ws[k]
            x = self.M
            for k in range(len(ws) + 1):
                self.line(x, top, x, top - h)
                if k < len(ws):
                    x += ws[k]
            self.line(self.M, top, self.M + full, top)
            self.line(self.M, top - h, self.M + full, top - h)
            self.y = top - h

        row(head, "B", True)
        for r in rows:
            row(r)
        row(["", "Итого", "", "", total], "B")
        self.y -= 6

    def sign(self, parts):
        size, lh = 10.5, 15
        self.need(lh * 4)
        top = self.y
        col = (self.W - 2 * self.M) / 2
        for k, (role, name, phone) in enumerate(parts):
            x = self.M + k * col
            y = top
            for txt, font in ((role, "B"), (name, "R"), (f"Тел.: {phone}", "R")):
                y -= lh
                for ln in self.wrap(txt, col - 14, size, font):
                    self.text(x, y, ln, size, font)
        self.y = top - lh * 4

    def _font_objs(self, key, add):
        f = self.f[key]
        sc = 1000 / f.upm
        used = sorted(self.used[key])
        w = " ".join(f"{g} [{round(f.width(g) * sc)}]" for g in used)
        cmap = ("/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CIDSystemInfo << /Registry (Adobe) "
                "/Ordering (UCS) /Supplement 0 >> def /CMapName /Adobe-Identity-UCS def /CMapType 2 def "
                "1 begincodespacerange <0000> <FFFF> endcodespacerange\n")
        chunks = [used[i:i + 100] for i in range(0, len(used), 100)]
        for ch in chunks:
            cmap += f"{len(ch)} beginbfchar\n" + "".join(
                f"<{g:04X}> <{ord(self.used[key][g]):04X}>\n" for g in ch) + "endbfchar\n"
        cmap += "endcmap CMapName currentdict /CMapResource defineresource pop end end"
        raw = f.data
        ff = add(f"<< /Length {{len}} /Length1 {len(raw)} /Filter /FlateDecode >>", zlib.compress(raw, 9))
        name = "LiberationSans" + ("-Bold" if key == "B" else "")
        bb = " ".join(str(round(v * sc)) for v in f.bbox)
        fd = add(f"<< /Type /FontDescriptor /FontName /{name} /Flags 32 /FontBBox [{bb}] /ItalicAngle 0 "
                 f"/Ascent {round(f.asc * sc)} /Descent {round(f.desc * sc)} /CapHeight {round(f.asc * sc * 0.7)} "
                 f"/StemV {120 if key == 'B' else 80} /FontFile2 {ff} 0 R >>")
        cid = add(f"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /{name} /CIDSystemInfo << /Registry (Adobe) "
                  f"/Ordering (Identity) /Supplement 0 >> /FontDescriptor {fd} 0 R /DW 556 /W [{w}] /CIDToGIDMap /Identity >>")
        tu = add("<< /Length {len} /Filter /FlateDecode >>", zlib.compress(cmap.encode(), 9))
        return add(f"<< /Type /Font /Subtype /Type0 /BaseFont /{name} /Encoding /Identity-H "
                   f"/DescendantFonts [{cid} 0 R] /ToUnicode {tu} 0 R >>")

    def output(self, footer=""):
        objs = []

        def add(dct, stream=None):
            objs.append((dct, stream))
            return len(objs)
        add("CATALOG")
        add("PAGES")
        n = len(self.pages)
        if footer:
            for k, pg in enumerate(self.pages, 1):
                s = f"{footer} · страница {k} из {n}"
                tw = self.f["R"].text_w(s, 8)
                pg.append(f"0.45 g BT /FR 8 Tf {(self.W - tw) / 2:.2f} 28 Td {self._hex('R', s)} Tj ET 0 g")
        fr = self._font_objs("R", add)
        fb = self._font_objs("B", add)
        kids = []
        for pg in self.pages:
            data = zlib.compress("\n".join(pg).encode(), 9)
            c = add("<< /Length {len} /Filter /FlateDecode >>", data)
            kids.append(add(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {self.W} {self.H}] "
                            f"/Resources << /Font << /FR {fr} 0 R /FB {fb} 0 R >> >> /Contents {c} 0 R >>"))
        objs[0] = ("<< /Type /Catalog /Pages 2 0 R >>", None)
        objs[1] = (f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>", None)
        out = io.BytesIO()
        out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offs = []
        for i, (dct, stream) in enumerate(objs, 1):
            offs.append(out.tell())
            if stream is not None:
                dct = dct.replace("{len}", str(len(stream)))
                out.write(f"{i} 0 obj\n{dct}\nstream\n".encode() + stream + b"\nendstream\nendobj\n")
            else:
                out.write(f"{i} 0 obj\n{dct}\nendobj\n".encode())
        x = out.tell()
        out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
        for o in offs:
            out.write(f"{o:010d} 00000 n \n".encode())
        out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n".encode())
        return out.getvalue()


def to_pdf(blocks, reg, bold, footer=""):
    p = PDF(reg, bold)
    for kind, v in blocks:
        if kind == "title":
            p.para(v, 14, "B", "center", 0, 0)
        elif kind == "subtitle":
            p.para(v, 11.5, "B", "center", 0, 12)
        elif kind == "cityline":
            p.need(20)
            p.y -= 14
            p.text(p.M, p.y, v[0])
            p.text(p.W - p.M - p.f["R"].text_w(v[1], 10.5), p.y, v[1])
            p.y -= 12
        elif kind == "h":
            p.para(v, 11, "B", "left", 8, 3, keep=30)
        elif kind == "p":
            p.para(v)
        elif kind == "table":
            p.y -= 2
            p.table(v["head"], v["rows"], v["total"])
        elif kind == "sign":
            p.sign(v)
    return p.output(footer)

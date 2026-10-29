"""Genera los wireframes de la demo (mockups del cliente) con Pillow.

Son bocetos en gris, no capturas de la solución: el juez visual compara estructura, no píxeles.
Uso: python -m backend.fixtures.landing.make_mockups
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent
INK, LINE, FILL, ACCENT = (60, 60, 60), (150, 150, 150), (228, 228, 228), (190, 190, 190)


def font(size):
    for name in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def box(d, xy, label=None, size=18, fill=FILL):
    d.rectangle(xy, outline=LINE, fill=fill, width=2)
    if label:
        d.text((xy[0] + 12, xy[1] + 10), label, fill=INK, font=font(size))


def lines(d, x, y, width, count, gap=18):
    for i in range(count):
        d.line((x, y + i * gap, x + width * (0.95 if i % 2 else 0.8), y + i * gap), fill=LINE, width=6)


def desktop():
    w, h = 1440, 1500
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    box(d, (0, 0, w, 72), None, fill=ACCENT)
    d.text((170, 22), "LOGO / Marca", fill=INK, font=font(24))
    d.text((1030, 24), "Servicios      Contacto", fill=INK, font=font(20))
    box(d, (0, 72, w, 420), None)
    d.text((170, 150), "Título principal grande", fill=INK, font=font(48))
    lines(d, 170, 240, 600, 2, 24)
    box(d, (170, 310, 380, 362), "Botón CTA", 20, fill=ACCENT)
    d.text((170, 470), "Servicios", fill=INK, font=font(32))
    for i in range(3):
        x = 170 + i * 375
        box(d, (x, 530, x + 345, 720), f"Tarjeta {i + 1}", 20)
        lines(d, x + 16, 600, 300, 3)
    d.text((170, 790), "Contacto", fill=INK, font=font(32))
    y = 850
    for label, height in (("Nombre", 46), ("Email", 46), ("Mensaje", 120)):
        d.text((170, y), label, fill=INK, font=font(18))
        box(d, (170, y + 26, 690, y + 26 + height))
        y += 26 + height + 22
    box(d, (170, y, 690, y + 50), "Botón Enviar", 20, fill=ACCENT)
    d.text((170, y + 110), "Pie de página", fill=LINE, font=font(16))
    return img


def mobile():
    w, h = 390, 1900
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    box(d, (0, 0, w, 96), None, fill=ACCENT)
    d.text((130, 14), "LOGO / Marca", fill=INK, font=font(20))
    d.text((105, 56), "Servicios   Contacto", fill=INK, font=font(16))
    box(d, (0, 96, w, 420), None)
    d.text((24, 130), "Título principal", fill=INK, font=font(30))
    d.text((24, 170), "en dos líneas", fill=INK, font=font(30))
    lines(d, 24, 240, 330, 3, 22)
    box(d, (24, 330, 190, 378), "Botón CTA", 16, fill=ACCENT)
    d.text((24, 450), "Servicios", fill=INK, font=font(26))
    y = 500
    for i in range(3):
        box(d, (24, y, 366, y + 150), f"Tarjeta {i + 1}", 18)
        lines(d, 40, y + 60, 300, 3)
        y += 170
    d.text((24, y + 20), "Contacto", fill=INK, font=font(26))
    y += 70
    for label, height in (("Nombre", 44), ("Email", 44), ("Mensaje", 110)):
        d.text((24, y), label, fill=INK, font=font(16))
        box(d, (24, y + 24, 366, y + 24 + height))
        y += 24 + height + 18
    box(d, (24, y, 366, y + 48), "Botón Enviar", 18, fill=ACCENT)
    return img


if __name__ == "__main__":
    desktop().save(HERE / "mockup_desktop.png", optimize=True)
    mobile().save(HERE / "mockup_mobile.png", optimize=True)
    print("ok")

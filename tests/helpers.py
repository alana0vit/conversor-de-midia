import io

from PIL import Image


def png_bytes(mode="RGBA", size=(40, 30), color=(255, 0, 0, 0)) -> bytes:
    img = Image.new(mode, size, color if mode == "RGBA" else color[:3])
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()

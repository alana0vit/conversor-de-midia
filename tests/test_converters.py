from PIL import Image

import pytest

from converter_worker.converters import PermanentError, get_converter, jpg_to_png, png_to_jpg
from helpers import png_bytes


def convert(tmp_path, data: bytes):
    src, dst = tmp_path / "in.png", tmp_path / "out.jpg"
    src.write_bytes(data)
    png_to_jpg(str(src), str(dst))
    return Image.open(dst)


def test_transparent_png_becomes_white_jpg(tmp_path):
    out = convert(tmp_path, png_bytes("RGBA", color=(255, 0, 0, 0)))  # vermelho 100% transparente
    assert out.format == "JPEG"
    assert out.mode == "RGB"
    r, g, b = out.getpixel((5, 5))
    assert min(r, g, b) > 245                      # branco, não preto


def test_opaque_png_keeps_color(tmp_path):
    out = convert(tmp_path, png_bytes("RGB", color=(0, 0, 255, 255)))
    r, g, b = out.getpixel((5, 5))
    assert b > 200 and r < 40


def test_palette_png_with_transparency(tmp_path):
    img = Image.new("P", (10, 10))
    img.info["transparency"] = 0
    path = tmp_path / "p.png"
    img.save(path, "PNG", transparency=0)
    out = convert(tmp_path, path.read_bytes())
    assert out.mode == "RGB"


def test_grayscale_16bit_png(tmp_path):
    img = Image.new("I;16", (10, 10), 30000)
    path = tmp_path / "g.png"
    img.save(path, "PNG")
    assert convert(tmp_path, path.read_bytes()).mode == "RGB"


def test_corrupted_file_is_permanent_error(tmp_path):
    with pytest.raises(PermanentError):
        convert(tmp_path, b"\x89PNG\r\n\x1a\n" + b"lixo" * 50)


def test_unknown_conversion_is_permanent_error():
    with pytest.raises(PermanentError):
        get_converter("png", "webp")


def test_jpg_becomes_png(tmp_path):
    src, dst = tmp_path / "in.jpg", tmp_path / "out.png"
    Image.new("RGB", (40, 30), (10, 200, 10)).save(src, "JPEG")
    jpg_to_png(str(src), str(dst))
    out = Image.open(dst)
    assert out.format == "PNG" and out.size == (40, 30)


def test_jpg_exif_rotation_is_applied(tmp_path):
    src, dst = tmp_path / "in.jpg", tmp_path / "out.png"
    img = Image.new("RGB", (40, 20))
    exif = img.getexif()
    exif[0x0112] = 6                                    # girar 90 graus
    img.save(src, "JPEG", exif=exif)
    jpg_to_png(str(src), str(dst))
    assert Image.open(dst).size == (20, 40)


def test_cmyk_jpg_becomes_rgb_png(tmp_path):
    src, dst = tmp_path / "in.jpg", tmp_path / "out.png"
    Image.new("CMYK", (10, 10)).save(src, "JPEG")
    jpg_to_png(str(src), str(dst))
    assert Image.open(dst).mode == "RGB"


def test_png_renamed_to_jpg_is_permanent_error(tmp_path):
    src = tmp_path / "in.jpg"
    src.write_bytes(png_bytes())
    with pytest.raises(PermanentError):
        jpg_to_png(str(src), str(tmp_path / "out.png"))


def test_sample_corrupted_file_goes_to_dlq(tmp_path):
    """O arquivo samples/corrompido.png deve falhar de forma permanente (-> DLQ)."""
    from pathlib import Path
    sample = Path(__file__).resolve().parents[1] / "samples" / "corrompido.png"
    with pytest.raises(PermanentError):
        png_to_jpg(str(sample), str(tmp_path / "out.jpg"))

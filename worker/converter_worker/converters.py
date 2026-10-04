"""Conversores de mídia, indexados por (formato_origem, formato_destino).

Para adicionar WAV -> MP3: escrever uma função que chama o FFmpeg e registrá-la
em CONVERTERS (e incluir a conversão no contrato). Nada mais no worker muda.
"""
from __future__ import annotations

from typing import Callable

from PIL import Image, ImageOps, UnidentifiedImageError

# Proteção contra "decompression bomb": imagens absurdamente grandes viram erro.
Image.MAX_IMAGE_PIXELS = 100_000_000


class PermanentError(Exception):
    """Erro que não se resolve tentando de novo (arquivo corrompido, formato inválido)."""


def _open(src_path: str, expected: str) -> Image.Image:
    """Abre e decodifica a imagem inteira. Falhas aqui = arquivo ruim = erro permanente."""
    try:
        img = Image.open(src_path)
        img.load()
    except (UnidentifiedImageError, Image.DecompressionBombError, SyntaxError, OSError) as exc:
        raise PermanentError(f"imagem inválida ou corrompida: {exc}") from exc
    if img.format != expected:
        img.close()
        raise PermanentError(f"arquivo não é {expected} (detectado: {img.format})")
    return img


def png_to_jpg(src_path: str, dst_path: str) -> None:
    with _open(src_path, "PNG") as img:
        # JPG não tem transparência. Sem este tratamento, as áreas
        # transparentes ficariam pretas. Achatamos sobre fundo branco.
        has_alpha = img.mode in ("RGBA", "LA") or (
            img.mode == "P" and "transparency" in img.info
        )
        if has_alpha:
            rgba = img.convert("RGBA")
            out = Image.new("RGB", rgba.size, (255, 255, 255))
            out.paste(rgba, mask=rgba.getchannel("A"))
        else:
            out = img.convert("RGB")   # cobre L, P, I;16, RGB etc.

    # Escrita: um erro aqui (ex.: disco cheio) é temporário -> vai para retry.
    out.save(dst_path, "JPEG", quality=90, optimize=True, progressive=True)


def jpg_to_png(src_path: str, dst_path: str) -> None:
    with _open(src_path, "JPEG") as img:
        # Fotos de celular guardam a rotação no EXIF; aplicamos para a imagem
        # não sair "deitada", já que o PNG não carrega essa informação.
        out = ImageOps.exif_transpose(img)
        if out.mode not in ("RGB", "L"):      # ex.: CMYK, que o PNG não suporta
            out = out.convert("RGB")

    # PNG é sem perdas: a imagem fica igual ao JPG, mas o arquivo costuma crescer.
    out.save(dst_path, "PNG", optimize=True)


CONVERTERS: dict[tuple[str, str], Callable[[str, str], None]] = {
    ("png", "jpg"): png_to_jpg,
    ("jpg", "png"): jpg_to_png,
}


def get_converter(source_format: str, target_format: str) -> Callable[[str, str], None]:
    try:
        return CONVERTERS[(source_format, target_format)]
    except KeyError as exc:
        raise PermanentError(f"sem conversor para {source_format} -> {target_format}") from exc

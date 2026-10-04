"""Conversores de mídia, indexados por (formato_origem, formato_destino).

Para adicionar WAV -> MP3: escrever uma função que chama o FFmpeg e registrá-la
em CONVERTERS. Nada mais no worker precisa mudar.
"""
from __future__ import annotations

from typing import Callable

from PIL import Image, UnidentifiedImageError

# Proteção contra "decompression bomb": imagens absurdamente grandes viram erro.
Image.MAX_IMAGE_PIXELS = 100_000_000


class PermanentError(Exception):
    """Erro que não se resolve tentando de novo (arquivo corrompido, formato inválido)."""


def png_to_jpg(src_path: str, dst_path: str) -> None:
    # Leitura: falhas aqui significam arquivo ruim -> erro permanente.
    try:
        img = Image.open(src_path)
        img.load()
    except (UnidentifiedImageError, Image.DecompressionBombError, SyntaxError, OSError) as exc:
        raise PermanentError(f"imagem inválida ou corrompida: {exc}") from exc

    with img:
        if img.format != "PNG":
            raise PermanentError(f"arquivo não é PNG (detectado: {img.format})")

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


CONVERTERS: dict[tuple[str, str], Callable[[str, str], None]] = {
    ("png", "jpg"): png_to_jpg,
}


def get_converter(source_format: str, target_format: str) -> Callable[[str, str], None]:
    try:
        return CONVERTERS[(source_format, target_format)]
    except KeyError as exc:
        raise PermanentError(f"sem conversor para {source_format} -> {target_format}") from exc

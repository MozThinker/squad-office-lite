"""Gera os ícones PNG do Office a partir do pixel art do favicon (static/index.html), só com a stdlib.

Uso: python3 tools/gerar_icones.py   (escreve em static/; os PNG ficam commitados)
- icon-192.png, icon-512.png: «any», a arte 10×10 ocupa a área toda;
- icon-maskable-{192,512}.png e apple-touch-icon.png (180): arte a 70 % centrada (zona segura) sobre o fundo.
Determinístico: os mesmos bytes a cada corrida.
"""
import struct
import zlib
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"
FUNDO = (0x26, 0x32, 0x38)
# = favicon do index.html (viewBox 10×10): cabeça e corpo
RECTS = [(2, 1, 6, 3, (0xFF, 0xCC, 0x80)), (1, 5, 8, 4, (0x42, 0xA5, 0xF5))]


def cor(u: int, v: int) -> tuple:
    for x, y, w, h, c in RECTS:
        if x <= u < x + w and y <= v < y + h:
            return c
    return FUNDO


def png(lado: int, escala: float) -> bytes:
    arte = round(lado * escala)
    o = (lado - arte) // 2
    linhas = []
    for py in range(lado):
        linha = bytearray(b"\0")
        for px in range(lado):
            dx, dy = px - o, py - o
            linha += bytes(cor(dx * 10 // arte, dy * 10 // arte) if 0 <= dx < arte and 0 <= dy < arte else FUNDO)
        linhas.append(bytes(linha))

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", lado, lado, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(linhas), 9)) + chunk(b"IEND", b""))


ICONES = {"icon-192.png": (192, 1), "icon-512.png": (512, 1), "icon-maskable-192.png": (192, 0.7),
          "icon-maskable-512.png": (512, 0.7), "apple-touch-icon.png": (180, 0.7)}

if __name__ == "__main__":
    for nome, (lado, escala) in ICONES.items():
        (STATIC / nome).write_bytes(png(lado, escala))
        print(nome, lado)

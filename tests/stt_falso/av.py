"""PyAV falso para os testes da voz (ADR-023): «descodifica» o ficheiro em quadros de 1 s de silêncio até à
`duracao` do JSON em CONVERSA_STT_FALSO (gerador preguiçoso: uma duração enorme só custa os quadros que se lerem).
Com `"erro": "descodificar"` o open rebenta. Regista os quadros lidos em `registo` ({"quadros": n})."""
import builtins
import json
import math
import os
from types import SimpleNamespace

import numpy

TAXA = 16000


def _cfg() -> dict:
    with builtins.open(os.environ["CONVERSA_STT_FALSO"], encoding="utf-8") as f:
        return json.load(f)


class _Quadro:
    def __init__(self, n):
        self.n = n

    def to_ndarray(self):
        return numpy.ndarray(self.n)


class _Contentor:
    def __init__(self, ficheiro):
        with builtins.open(ficheiro, "rb") as f:   # a cópia privada tem de existir enquanto se descodifica
            f.read(1)
        self.c, self.lidos = _cfg(), 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        if self.c.get("registo"):
            with builtins.open(self.c["registo"], "a", encoding="utf-8") as f:
                f.write(json.dumps({"quadros": self.lidos, "pid": os.getpid()}) + "\n")

    def decode(self, audio=None):
        assert audio == 0
        falta = round(float(self.c.get("duracao", 3.0)) * TAXA)
        for _ in range(math.ceil(falta / TAXA)):
            self.lidos += 1
            yield _Quadro(min(TAXA, falta))
            falta -= TAXA


def open(ficheiro, **kw):   # a assinatura do av
    if _cfg().get("erro") == "descodificar":
        raise ValueError("áudio partido")
    return _Contentor(ficheiro)


class AudioResampler:
    def __init__(self, format, layout, rate):
        assert (format, layout, rate) == ("flt", "mono", TAXA)

    def resample(self, quadro):
        return [] if quadro is None else [quadro]


audio = SimpleNamespace(resampler=SimpleNamespace(AudioResampler=AudioResampler))

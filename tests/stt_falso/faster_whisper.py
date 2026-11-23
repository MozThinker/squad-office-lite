"""faster-whisper falso para os testes da voz (ADR-023), com o av.py e o numpy.py falsos ao lado: entra por um `.pth`
no venv falso dos testes (o trabalhador corre com -E -s), nunca carrega modelos nem vai à rede.

Lê, em cada carga e em cada transcrição, o JSON do ficheiro em CONVERSA_STT_FALSO:

  {"texto": "olá",          # o texto dos segmentos (partido no 1.º espaço, para testar a junção)
   "duracao": 3.0,          # a duração do áudio que o av falso «descodifica» (info.duration = amostras / 16 000)
   "demora": 0,             # segundos a dormir dentro do transcribe
   "erro": null,            # "carga" (o WhisperModel rebenta), "descodificar" (o av.open rebenta) ou "transcrever"
   "sair": false,           # o processo morre a meio do transcribe (os._exit(3))
   "ruido": false,          # escreve lixo no stdout ao carregar (como uma biblioteca faladora)
   "registo": "<caminho>"}  # acrescenta uma linha JSON por carga e por transcrição, com os argumentos
"""
import json
import os
import resource
import time
from types import SimpleNamespace


def _cfg() -> dict:
    with open(os.environ["CONVERSA_STT_FALSO"], encoding="utf-8") as f:
        return json.load(f)


def _registar(c: dict, linha: dict) -> None:
    if c.get("registo"):
        with open(c["registo"], "a", encoding="utf-8") as f:
            f.write(json.dumps(linha) + "\n")


class WhisperModel:
    def __init__(self, nome, **kw):
        c = _cfg()
        _registar(c, {"carga": nome, **kw, "pid": os.getpid(), "offline": os.environ.get("HF_HUB_OFFLINE"),
                      "pythonpath": os.environ.get("PYTHONPATH"),
                      "rlimit_as": resource.getrlimit(resource.RLIMIT_AS)[0]})
        if c.get("ruido"):
            print("ruído do modelo", flush=True)
            os.write(1, b"ruido no fd 1\n")
        if c.get("erro") == "carga":
            raise RuntimeError("modelo partido")

    def transcribe(self, audio, **kw):
        c = _cfg()
        assert not isinstance(audio, str), "o trabalhador passa o array descodificado, nunca o caminho"
        _registar(c, {"transcrever": audio.size, **kw, "pid": os.getpid()})
        if c.get("demora"):
            time.sleep(c["demora"])
        if c.get("sair"):
            os._exit(3)
        if c.get("erro") == "transcrever":
            raise RuntimeError("áudio partido")
        segs = [SimpleNamespace(text=f" {t} ") for t in c.get("texto", "").split(" ", 1) if t]
        return iter(segs), SimpleNamespace(duration=audio.size / 16000, language="pt")

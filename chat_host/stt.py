#!/usr/bin/env python3
"""Voz da Conversa (ADR-023): o trabalhador que transcreve os áudios da fila, lançado pela ponte (chat_host/ponte.py).

Dois modos, o mesmo protocolo de linhas JSON pelos pipes (a ponte nunca bloqueia):
- `local` (`stt.py --servir`): faster-whisper `small` (CPU int8, VAD ligado), com o Python do venv do dono
  (`~/.local/share/office-conversa/stt-venv`, lançado com -E -s e HF_HUB_OFFLINE=1: só o modelo já descarregado).
  O áudio descodifica-se com o `av` quadro a quadro para mono 16 kHz e pára (`longo`) logo que passa dos LONGO_S.
- `externa` (`stt.py --servir --externa`): POST multipart a `<CONVERSA_STT_URL>/v1/audio/transcriptions` (protocolo
  compatível com o da OpenAI) com `Authorization: Bearer <CONVERSA_STT_CHAVE>`, só stdlib (`urllib`), com o
  `python3 -I` do sistema. A chave só existe no ambiente deste processo: nunca se escreve em lado nenhum.

Pedido: `{"id": "<32 hex>", "ficheiro": "<caminho>", "idioma": "pt"}`. Resposta: `{"id", "ok": true, "texto",
"duracao_s"}` ou `{"id", "ok": false, "erro": "indisponivel|longo|falhou|chave|limite|tempo"}`. Sai com EOF no stdin
ou depois de CONVERSA_STT_OCIOSO s (600) sem pedidos. O fd 1 passa a ser o stderr (nada das bibliotecas se mistura com
as respostas); o texto e a chave nunca vão para o stderr.
`stt.py <ficheiro>`: imprime o JSON de uma transcrição local (testar no host).
"""
from __future__ import annotations

import itertools
import json
import os
import re
import secrets
import select
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ID_MAX = 64
LINHA_MAX = 4096
LONGO_S = 130.0          # o cliente pára aos 120 s; mais do que isto é recusado sem transcrever
TAXA = 16000             # Hz, o que o Whisper espera
OCIOSO_S = 600.0
TEMPO_S = 60.0           # o mesmo CONVERSA_STT_TEMPO da ponte; o pedido HTTP desiste antes (TEMPO_FRACCAO)
TEMPO_FRACCAO = 0.8
RESPOSTA_MAX = 1 << 20
MODELO_EXTERNA = "whisper-1"
IDIOMA = re.compile(r"[a-z]{2}")
MIME = {"webm": "audio/webm", "ogg": "audio/ogg", "m4a": "audio/mp4", "mp3": "audio/mpeg"}
LOCAIS = ("127.0.0.1", "localhost", "::1")   # http:// só para um servidor no próprio host

_modelo = None


def _float_env(nome: str, omissao: float) -> float:
    try:
        return float(os.environ.get(nome) or omissao)
    except ValueError:
        return omissao


def modelo():
    # importa o faster_whisper só aqui (ImportError: voz indisponível) e carrega o modelo uma vez
    global _modelo
    if _modelo is None:
        from faster_whisper import WhisperModel
        raiz = os.environ.get("CONVERSA_STT_MODELOS") or str(Path.home() / ".local/share/office-conversa/stt-modelos")
        _modelo = WhisperModel("small", device="cpu", compute_type="int8", download_root=raiz)
    return _modelo


DEMUXER = {"webm": "matroska", "ogg": "ogg", "m4a": "mp4", "mp3": "mp3"}


def descodificar(ficheiro: str):
    # mono 16 kHz float32 (numpy) ou None se passar dos LONGO_S; pára nesse quadro, antes de juntar o resto
    import av
    import numpy as np
    partes, n, maximo = [], 0, int(LONGO_S * TAXA)
    rs = av.audio.resampler.AudioResampler(format="flt", layout="mono", rate=TAXA)
    # demuxer pela extensão que o Office validou, nunca adivinhado pelo conteúdo (p.ex. HLS escondido depois de um ID3)
    fmt = DEMUXER[Path(ficheiro).suffix.lstrip(".")]
    with av.open(ficheiro, format=fmt, metadata_errors="ignore") as c:
        for quadro in itertools.chain(c.decode(audio=0), [None]):   # None esvazia o resampler
            for r in rs.resample(quadro):
                a = r.to_ndarray()
                n += a.size
                if n > maximo:
                    return None
                partes.append(a)
    return np.concatenate(partes, axis=None)


def transcrever(ficheiro: str, idioma: str = "pt") -> dict:
    try:
        m = modelo()
        import av  # noqa: F401 - vêm com o faster-whisper; sem eles a voz está indisponível
        import numpy  # noqa: F401
    except ImportError:
        return {"ok": False, "erro": "indisponivel"}
    except Exception as e:   # noqa: BLE001 - modelo que não carrega
        print(f"stt: o modelo não carregou ({type(e).__name__})", file=sys.stderr, flush=True)
        return {"ok": False, "erro": "falhou"}
    try:
        audio = descodificar(ficheiro)
        if audio is None:
            return {"ok": False, "erro": "longo", "duracao_s": LONGO_S}
        segmentos, info = m.transcribe(audio, language=idioma, vad_filter=True)
        duracao = round(float(info.duration), 1)
        texto = " ".join(t for s in segmentos if (t := str(s.text).strip()))
    except Exception as e:   # noqa: BLE001 - áudio ilegível, erro do modelo
        print(f"stt: a transcrição falhou ({type(e).__name__})", file=sys.stderr, flush=True)
        return {"ok": False, "erro": "falhou"}
    return {"ok": True, "texto": texto, "duracao_s": duracao}


# --- externa ----------------------------------------------------------------------------------------------------------
def endpoint(base: str) -> str | None:
    """`<base>/v1/audio/transcriptions` (ou `<base>/audio/transcriptions` se a base já acabar em /v1); None se a base
    não for https:// (ou http:// para o próprio host), tiver credenciais, pesquisa ou fragmento."""
    try:
        u = urllib.parse.urlsplit(base.strip())
        host = u.hostname
    except ValueError:
        return None
    if not host or u.username or u.password or u.query or u.fragment or \
            not (u.scheme == "https" or (u.scheme == "http" and host in LOCAIS)):
        return None
    caminho = u.path.rstrip("/")
    caminho += "/audio/transcriptions" if caminho.endswith("/v1") else "/v1/audio/transcriptions"
    return urllib.parse.urlunsplit((u.scheme, u.netloc, caminho, "", ""))


def multipart(campos: dict, ficheiro: tuple[str, str, bytes]) -> tuple[bytes, str]:
    fronteira = "----office-conversa-" + secrets.token_hex(16)
    partes = []
    for k, v in campos.items():
        partes.append(f'--{fronteira}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    nome, mime, dados = ficheiro
    partes.append(f'--{fronteira}\r\nContent-Disposition: form-data; name="file"; filename="{nome}"\r\n'
                  f"Content-Type: {mime}\r\n\r\n".encode() + dados + b"\r\n")
    partes.append(f"--{fronteira}--\r\n".encode())
    return b"".join(partes), f"multipart/form-data; boundary={fronteira}"


class _SemRedirect(urllib.request.HTTPRedirectHandler):
    # um redirect conta como falha: a chave nunca segue para outro endereço
    def redirect_request(self, *_a, **_k):
        return None


_ABRIR = urllib.request.build_opener(_SemRedirect).open


def transcrever_externa(ficheiro: str, idioma: str = "pt", env=os.environ) -> dict:
    url, chave = endpoint(env.get("CONVERSA_STT_URL") or ""), env.get("CONVERSA_STT_CHAVE") or ""
    ext = Path(ficheiro).suffix.lstrip(".")
    if url is None or not chave or ext not in MIME:
        return {"ok": False, "erro": "indisponivel"}
    try:
        with open(ficheiro, "rb") as f:
            dados = f.read()
    except OSError:
        return {"ok": False, "erro": "falhou"}
    corpo, ct = multipart({"model": env.get("CONVERSA_STT_MODELO") or MODELO_EXTERNA, "language": idioma,
                           "response_format": "json"}, (f"audio.{ext}", MIME[ext], dados))
    req = urllib.request.Request(url, data=corpo, method="POST",
                                 headers={"Authorization": f"Bearer {chave}", "Content-Type": ct,
                                          "Accept": "application/json", "User-Agent": "office-conversa"})
    tempo = _float_env("CONVERSA_STT_TEMPO", TEMPO_S) * TEMPO_FRACCAO
    try:
        with _ABRIR(req, timeout=tempo) as r:   # noqa: S310 - esquema conferido em endpoint()
            raw = r.read(RESPOSTA_MAX + 1)
    except urllib.error.HTTPError as e:
        print(f"stt: o fornecedor respondeu {e.code}", file=sys.stderr, flush=True)
        return {"ok": False, "erro": {401: "chave", 403: "chave", 429: "limite"}.get(e.code, "falhou")}
    except (TimeoutError, socket.timeout):
        return {"ok": False, "erro": "tempo"}
    except urllib.error.URLError as e:
        if isinstance(e.reason, (TimeoutError, socket.timeout)):
            return {"ok": False, "erro": "tempo"}
        print(f"stt: o fornecedor não respondeu ({type(e.reason).__name__})", file=sys.stderr, flush=True)
        return {"ok": False, "erro": "falhou"}
    except OSError as e:
        print(f"stt: o fornecedor não respondeu ({type(e).__name__})", file=sys.stderr, flush=True)
        return {"ok": False, "erro": "falhou"}
    try:
        d = json.loads(raw) if len(raw) <= RESPOSTA_MAX else None
    except ValueError:
        d = None
    if not isinstance(d, dict) or not isinstance(d.get("text"), str):
        print("stt: resposta do fornecedor sem texto", file=sys.stderr, flush=True)
        return {"ok": False, "erro": "falhou"}
    dur = d.get("duration")
    dur = round(float(dur), 1) if isinstance(dur, (int, float)) and not isinstance(dur, bool) else None
    return {"ok": True, "texto": d["text"].strip(), "duracao_s": dur}


# --- protocolo --------------------------------------------------------------------------------------------------------
def _pedido(ln: bytes) -> tuple[str | None, str | None, str]:
    try:
        d = json.loads(ln)
    except ValueError:
        return None, None, "pt"
    if not isinstance(d, dict) or not isinstance(d.get("id"), str) or len(d["id"]) > ID_MAX:
        return None, None, "pt"
    idioma = d.get("idioma") if isinstance(d.get("idioma"), str) and IDIOMA.fullmatch(d["idioma"]) else "pt"
    return d["id"], d.get("ficheiro") if isinstance(d.get("ficheiro"), str) else None, idioma


def servir(externa: bool = False) -> int:
    proto = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)                 # o que o modelo ou as bibliotecas escreverem vai para o stderr
    sys.stdout = sys.stderr
    buf, ocioso = b"", _float_env("CONVERSA_STT_OCIOSO", OCIOSO_S)
    fazer = transcrever_externa if externa else transcrever
    while True:
        while b"\n" not in buf:
            if len(buf) > LINHA_MAX:
                buf = b""
            pronto, _, _ = select.select([0], [], [], ocioso)
            if not pronto:
                return 0          # parado há tempo demais: sai e liberta a memória do modelo
            bloco = os.read(0, 65536)
            if not bloco:
                return 0          # a ponte fechou o stdin (ou morreu)
            buf += bloco
        ln, buf = buf.split(b"\n", 1)
        id_, ficheiro, idioma = _pedido(ln)
        if id_ is None:
            continue
        res = fazer(ficheiro, idioma) if ficheiro else {"ok": False, "erro": "falhou"}
        proto.write(json.dumps({"id": id_, **res}, ensure_ascii=False) + "\n")
        proto.flush()


def main(argv: list[str]) -> int:
    if argv in (["--servir"], ["--servir", "--externa"]):
        return servir(externa=len(argv) == 2)
    if len(argv) != 1 or argv[0].startswith("-"):
        print("uso: stt.py --servir [--externa] | stt.py <ficheiro>", file=sys.stderr)
        return 2
    res = transcrever(argv[0])
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

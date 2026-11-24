"""Aprovações: pedidos de decisão (.md com frontmatter) lidos de pastas só de leitura e decisões escritas numa fila.

O Office nunca edita os .md nem aplica nada: cada decisão é um JSON v1 em DECISOES_FILA_DIR (único sítio onde se
escreve), consumido por um aplicador externo que confirma o sha256 do pedido. Só stdlib; o fuso e o «agora» vêm
por parâmetro (sem importar o server).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import tempfile
import threading
from datetime import date, datetime
from pathlib import Path

import reader

FONTE = re.compile(r"[\w-]+")
FICHEIRO = re.compile(r"[\w.-]+\.md")
DEC_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,19}")
SHA = re.compile(r"[0-9a-f]{64}")
# gramática igual à do aplicador do runner (runner/decisoes.py: frontmatter, linhas, feitas, pendente): o Office
# só mostra em aberto o que o aplicador aceita aplicar
FRONT = re.compile(r"---\n(.*?)\n---", re.S)
DEC_LINE = re.compile(r"- \[([A-Za-z0-9][A-Za-z0-9._-]{0,19})\] (.*)")
DONE = re.compile(r" → (aprovado|recusado) \((\d{4}-\d{2}-\d{2})\)$")
_SP = "[ \u00a0\u2009\u202f]"   # espaço, sem quebra, fino e fino sem quebra
_NUM = rf"(?:\d{{1,3}}(?:[.{_SP[1:-1]}]\d{{3}})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"   # 1 200 · 1.200,50 · 20.5
# não começa a meio de um número/palavra (nem em «1 200» a partir do 200); unidade não seguida de letra
VALOR = re.compile(rf"(?<![\w.,])(?<!\d{_SP}){_NUM}{_SP}?(?:€|MT|USD)(?![^\W\d_])|\$ ?{_NUM}(?![\d.,]|{_SP}\d)")
VALOR_MARCA = re.compile(rf"(?i:\b(?:tecto|teto|máx(?:imo)?\.?|total|orçamento))\s*:?\s*((?:{VALOR.pattern}))")
ACCOES = ("aprovar", "recusar", "comentar")
FECHA = ("aprovar", "recusar")   # só estas fecham uma decisão («recebido»); comentar não
RESUMO_MAX, DATAS_MAX, CORPO_MAX = 400, 1500, 200 * 1024
NOTA_MAX, UA_MAX, IP_MAX = 2000, 120, 64
FILA_N = 500                 # JSON da fila lidos por passagem (os mais recentes pelo nome)
PEDIDO_MAX = 1024 * 1024     # .md maiores são ignorados (não aparecem)
FILA_SIZE = 16 * 1024        # JSON maiores são ignorados (o POST é ≤ 8 KB; ip/ua/criado acrescentam pouco)
# Marca «[lança: <slug>]» (ADR-024): só apresentação; a linha e a fila v1 não mudam. Gramática do lancar-aprovados do
# kit (slug do `lancar`), e só no fim da linha: o kit só lança «… [lança: <slug>] → aprovado (data)». Duas marcas ou
# um slug fora da gramática = sem marca (o texto fica cru, como o kit, que não lança nenhuma delas).
MARCA = re.compile(r" ?\[lança: ([a-z0-9][a-z0-9-]{0,47})\]$")
SINAL = ".lancar-aprovados.json"   # escrito pelo lancar-aprovados do kit na pasta das decisões (ADR-024)
SINAL_MAX = 64 * 1024
SINAL_FRESCO_S = 10 * 60     # o timer do kit corre de minuto a minuto (≤ 4 min por volta); mais velho = desligado
SINAL_LANCADO = ("a arrancar", "lançado")   # estados do registo do kit em que o lancar já arrancou

_fila_lock = threading.Lock()


def nome_valido(nome) -> bool:
    return isinstance(nome, str) and bool(FICHEIRO.fullmatch(nome)) and ".." not in nome


def _clip(s: str, n: int) -> str:
    m = reader.mask(s)
    return m if len(m) <= n else reader.clip(s, n - 1)   # clip acrescenta «…»: n - 1 + 1 = n


def _secao(lines: list[str], ok) -> list[str] | None:
    # linhas entre o título «## …» que satisfaz ok() e o próximo «## »
    for i, line in enumerate(lines):
        if line.startswith("## ") and ok(line.rstrip()):
            out = []
            for nxt in lines[i + 1:]:
                if nxt.startswith("## "):
                    break
                out.append(nxt)
            return out
    return None


def _titulo_ok(base: str):
    return lambda t: t == base or t.startswith(base + " (")


def _paragrafo(lines: list[str]) -> str:
    out = []
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            if out:
                break
            continue
        out.append(line.rstrip())
    return "\n".join(out)


_PEDIDO_ACCOES = re.compile(r"accoes-[a-z][a-z0-9-]{1,19}-\d{8}-\d{1,2}\.md")   # = accoes.PEDIDO (ADR-021)


def _mascara(root, nome: str):
    """Pedidos de acções (ADR-021) vão sem máscara: o dono tem de ver exactamente o texto que o serviço `accoes` envia
    se aprovar (um segredo tapado na vista sairia inteiro). Só conta como pedido de acções o nome do runner com o
    registo `.accoes/<pedido>.json` (ficheiro regular, escrito só pelo runner); tudo o resto mantém a máscara."""
    if _PEDIDO_ACCOES.fullmatch(nome):
        try:
            if stat.S_ISREG(os.lstat(Path(root) / ".accoes" / (nome[:-3] + ".json")).st_mode):
                return lambda s: s
        except OSError:
            pass
    return reader.mask


def marca(texto: str) -> tuple[str, str | None]:
    """(texto sem a marca, slug) se a linha acaba numa marca válida e única; senão (texto, None)."""
    m = MARCA.search(texto)
    if not m or texto.count("[lança:") != 1:
        return texto, None
    return texto[:m.start()], m.group(1)


def sinal(root: Path, now: float) -> dict | None:
    """O sinal do lançar depois de aprovar (ADR-024), ou None (= desligado: a omissão do kit). Ligado só com
    `{"v": 1, "ligado": true, "atualizado": <epoch>}` com menos de SINAL_FRESCO_S (um timer parado deixa de valer);
    `decisoes` = {ficheiro: estado do slug no registo do kit} das decisões que o próprio kit criou (só essas lançam)."""
    try:
        fd = os.open(Path(root) / SINAL, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        with open(fd, "rb") as f:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return None
            raw = f.read(SINAL_MAX + 1)
        d = json.loads(raw) if len(raw) <= SINAL_MAX else None
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or d.get("v") != 1 or d.get("ligado") is not True:
        return None
    t, dec = d.get("atualizado"), d.get("decisoes")
    if isinstance(t, bool) or not isinstance(t, (int, float)) or not (now - SINAL_FRESCO_S <= t <= now + 60):
        return None
    if not isinstance(dec, dict):
        return None
    return {"decisoes": {k: v for k, v in dec.items() if isinstance(k, str) and isinstance(v, str)}}


def lancamento(slug: str | None, sin: dict | None, nome: str, feito: str | None) -> str | None:
    """Estado do lançamento de uma linha para a UI: None (sem marca), «desligado» (aprovar só regista), «ligado»
    (aprovar lança), «lancada» (o kit já arrancou o lancar) ou «espera» (aprovada, o kit ainda não pegou nela)."""
    if slug is None:
        return None
    est = sin["decisoes"].get(nome) if sin else None
    if est is None:
        return "desligado"   # sem sinal, ou uma decisão que o kit não criou: o kit não a lança
    if feito != "aprovado":
        return "ligado"
    return "lancada" if est in SINAL_LANCADO else "espera"


def pendente(estado: str) -> bool:
    return estado.startswith(("pendente", "parcial"))   # com maiúsculas não conta (como no aplicador)


def valor(texto: str) -> str | None:
    # o valor depois de «tecto/teto/máx(imo)/total/orçamento»; senão o único valor; vários sem marcador → None
    m = VALOR_MARCA.search(texto)
    if m:
        return m.group(1)
    vs = VALOR.findall(texto)
    return vs[0] if len(vs) == 1 else None


def parse_pedido(text: str, nome: str) -> dict | None:
    # texto bruto → campos sem máscara (None = sem frontmatter). Decisões: id, texto, valor, feito, data
    # como o aplicador: «---\n» logo no início (sem BOM), chaves com maiúsculas, a última repetida ganha
    fm = FRONT.match(text)
    if not fm:
        return None
    front = {}
    for line in fm.group(1).splitlines():
        k, _, v = line.partition(":")
        front[k.strip()] = v.split("#")[0].strip()   # comentário «# …» fora
    lines = text.split("\n")
    body = text[fm.end():].split("\n")[1:]   # depois da linha que fecha o frontmatter
    titulo = next((ln[2:].strip() for ln in body if ln.startswith("# ") and ln[2:].strip()), None)
    titulo = titulo or nome.removesuffix(".md")
    oque = _secao(body, _titulo_ok("## O quê"))
    resumo = _paragrafo(oque) if oque is not None else ""
    resumo = resumo or _paragrafo(body)
    datas = _secao(body, _titulo_ok("## Datas e orçamento"))
    datas = "\n".join(datas).strip() if datas is not None else ""
    decisoes, vistos, dentro = [], set(), False
    for line in lines:   # todas as secções «## Decisões» do ficheiro, linha crua (sem strip), 1.º id repetido ganha
        if line.startswith("## "):
            dentro = line.strip() == "## Decisões"
            continue
        m = DEC_LINE.fullmatch(line) if dentro else None
        if not m or m.group(1) in vistos:
            continue
        vistos.add(m.group(1))
        texto, done = m.group(2), DONE.search(m.group(2))
        if done:
            texto = texto[:done.start()]
        texto, slug = marca(texto)
        decisoes.append({"id": m.group(1), "texto": texto, "valor": valor(texto), "lanca": slug,
                         "feito": done.group(1) if done else None, "data": done.group(2) if done else None})
    if not decisoes:
        decisoes = [{"id": "*", "texto": titulo, "valor": None, "lanca": None, "feito": None, "data": None}]
    return {"titulo": titulo, "criado": front.get("criado", ""), "estado": front.get("estado", ""),
            "resumo": resumo, "datas": datas or None, "decisoes": decisoes,
            "corpo": "\n".join(body)}


def _ler(root: Path, nome: str) -> tuple[bytes, os.stat_result] | None:
    # só ficheiros regulares directamente na pasta (sem symlinks, nem FIFOs), ≤ 1 MiB, nunca README.md;
    # tudo pelo mesmo fd (sem corrida entre o stat e a leitura)
    if not nome_valido(nome) or nome == "README.md":
        return None
    try:
        fd = os.open(root / nome, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        with open(fd, "rb") as f:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_size > PEDIDO_MAX:
                return None
            raw = f.read(PEDIDO_MAX + 1)
    except OSError:
        return None
    return (raw, st) if len(raw) <= PEDIDO_MAX else None   # cresceu depois do fstat


def _carregar(root: Path, nome: str) -> tuple[dict, str, os.stat_result] | None:
    lido = _ler(root, nome)
    if lido is None:
        return None
    raw, st = lido
    try:
        p = parse_pedido(raw.decode("utf-8"), nome)
    except UnicodeDecodeError:
        return None
    return (p, hashlib.sha256(raw).hexdigest(), st) if p else None


def recebidos(fila: Path) -> set:
    # (fonte, ficheiro, id, sha256) dos JSON aprovar/recusar na raiz da fila; subpastas e lixo ignorados
    out = set()
    try:
        entries = [e for e in os.scandir(fila)
                   if e.name.endswith(".json") and not e.name.startswith(".") and e.is_file(follow_symlinks=False)]
    except OSError:
        return out
    entries.sort(key=lambda e: e.name, reverse=True)
    for e in entries[:FILA_N]:
        try:
            if e.stat(follow_symlinks=False).st_size > FILA_SIZE:
                continue
            d = json.loads(Path(e.path).read_bytes())
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and d.get("accao") in FECHA:
            key = (d.get("fonte"), d.get("ficheiro"), d.get("id"), d.get("sha256"))
            if all(isinstance(k, str) for k in key):
                out.add(key)
    return out


def _recebido(rec: set, fonte: str, nome: str, did: str, sha: str) -> bool:
    return (fonte, nome, did, sha) in rec or (fonte, nome, "*", sha) in rec


def listar(dirs: dict[str, Path], fila: Path, tz, now: float) -> list[dict]:
    rec = recebidos(fila) if dirs else set()
    hoje = datetime.fromtimestamp(now, tz).date()
    out = []
    for fonte, root in dirs.items():
        sin = sinal(root, now)
        try:
            nomes = sorted(e.name for e in os.scandir(root) if e.name.endswith(".md"))
        except OSError:   # pasta inexistente: fonte ignorada
            continue
        for nome in nomes:
            got = _carregar(root, nome)
            if got is None or not pendente(got[0]["estado"]):
                continue
            p, sha, st = got
            try:
                criado = date.fromisoformat(p["criado"])
            except ValueError:
                criado = datetime.fromtimestamp(st.st_mtime, tz).date()
            decisoes = []
            msk = _mascara(root, nome)
            for d in p["decisoes"]:
                rcb = d["feito"] is None and _recebido(rec, fonte, nome, d["id"], sha)
                decisoes.append({"id": d["id"], "texto": msk(d["texto"]),
                                 "valor": msk(d["valor"]) if d["valor"] else None,
                                 "lanca": d["lanca"], "lancamento": lancamento(d["lanca"], sin, nome, d["feito"]),
                                 "feito": d["feito"], "data": d["data"], "recebido": rcb,
                                 "aberta": d["feito"] is None and not rcb})
            abertas = sum(d["aberta"] for d in decisoes)
            if not abertas:
                continue
            out.append({"fonte": fonte, "ficheiro": nome, "titulo": reader.mask(p["titulo"]),
                        "criado": criado.isoformat(), "idade_dias": max(0, (hoje - criado).days),
                        "estado": reader.mask(p["estado"]), "resumo": _clip(p["resumo"], RESUMO_MAX),
                        "datas": _clip(p["datas"], DATAS_MAX) if p["datas"] else None,
                        "sha256": sha, "abertas": abertas, "decisoes": decisoes})
    out.sort(key=lambda a: (a["criado"], a["fonte"], a["ficheiro"]))
    return out


def corpo(dirs: dict[str, Path], fonte, ficheiro) -> tuple[int, dict]:
    # GET /api/aprovacao: só pedidos pendentes; o corpo sem frontmatter, mascarado, cortado a 200 KB
    if not nome_valido(ficheiro):
        return 400, {"error": "pedido inválido"}
    root = dirs.get(fonte) if isinstance(fonte, str) else None
    got = _carregar(root, ficheiro) if root is not None else None
    if got is None or not pendente(got[0]["estado"]):
        return 404, {"error": "não encontrado"}
    p, sha, _ = got
    texto = _mascara(root, ficheiro)(p["corpo"])
    return 200, {"fonte": fonte, "ficheiro": ficheiro, "titulo": reader.mask(p["titulo"]), "sha256": sha,
                 "corpo": texto[:CORPO_MAX], "cortado": len(texto) > CORPO_MAX}


def validar(dirs: dict[str, Path], d: dict) -> str | None:
    # verificações de forma do POST (400); None = ok
    for v in d.values():   # surrogate solto («\ud800») passa no json.loads mas rebenta no json.dump da fila
        if isinstance(v, str):
            try:
                v.encode("utf-8")
            except UnicodeEncodeError:
                return "JSON inválido"
    if not isinstance(d.get("fonte"), str) or d["fonte"] not in dirs:
        return "fonte desconhecida"
    if not nome_valido(d.get("ficheiro")):
        return "ficheiro inválido"
    did = d.get("id")
    if not isinstance(did, str) or not (did == "*" or DEC_ID.fullmatch(did)):
        return "id inválido"
    if d.get("accao") not in ACCOES:
        return "accao inválida"
    if not isinstance(d.get("sha256"), str) or not SHA.fullmatch(d["sha256"]):
        return "sha256 inválido"
    nota = d.get("nota", "")
    if nota is None:
        nota = ""
    if not isinstance(nota, str):
        return "JSON inválido"
    if len(nota) > NOTA_MAX:
        return "nota com mais de 2000 caracteres"
    if d["accao"] != "aprovar" and not nota.strip():
        return "escreve o motivo"
    return None


def escrever(dirs: dict[str, Path], fila: Path, d: dict, ip: str, ua: str, tz, now: float) -> tuple[int, dict]:
    # d já passou por validar(); 409 se o pedido/decisão não está em aberto, 503 se a fila não aceita escrita
    fonte, nome, did, sha = d["fonte"], d["ficheiro"], d["id"], d["sha256"]
    with _fila_lock:
        got = _carregar(dirs[fonte], nome)
        if got is None:
            return 409, {"error": "pedido não encontrado"}
        p, actual, _ = got
        if not pendente(p["estado"]):
            return 409, {"error": "pedido já não está pendente"}
        dec = next((x for x in p["decisoes"] if x["id"] == did), None)
        if dec is None:
            return 409, {"error": "decisão inexistente"}
        if dec["feito"]:
            return 409, {"error": "decisão já tomada"}
        if _recebido(recebidos(fila), fonte, nome, did, actual):
            return 409, {"error": "decisão já recebida", "code": "recebida"}   # code: para o browser
        if sha != actual:
            return 409, {"error": "o pedido mudou, recarrega"}
        dt = datetime.fromtimestamp(now, tz)
        doc = {"v": 1, "criado": dt.isoformat(timespec="seconds"), "fonte": fonte, "ficheiro": nome, "id": did,
               "accao": d["accao"], "nota": d.get("nota") or "", "sha256": sha, "ip": ip[:IP_MAX], "ua": ua[:UA_MAX]}
        destino = fila / f"{dt.strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(4)}.json"
        tmp = None
        try:
            if not fila.is_dir():
                raise OSError("fila inexistente")
            fd, tmp = tempfile.mkstemp(dir=fila, prefix=".", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False)
            os.replace(tmp, destino)
            tmp = None
        except OSError:
            return 503, {"error": "fila indisponível"}
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
    return 202, {"ok": True}

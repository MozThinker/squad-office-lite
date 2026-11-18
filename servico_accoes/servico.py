#!/usr/bin/env python3
"""Serviço `accoes` (ADR-021): o único processo com as credenciais das acções aprovadas. Comando `accoes` na imagem
do runner (modo Docker) ou `python3 -I /opt/office-accoes/servico_accoes/servico.py` (modo VPS, timer de sistema):

    accoes [ciclo] [--intervalo S] [--passagens N]   colector + executor a cada S segundos (60), sem fim ou N vezes
    accoes passagem                                  uma passagem (colector + executor)
    accoes verificar                                 permissões, configuração, credenciais e ligação a cada conector
    accoes anular <sha>                              uma email_responder em espera não sai
    accoes desfazer <sha>                            apaga o rascunho criado ou a linha acrescentada à folha
    accoes escrever-config                           lê o accoes.json do stdin, valida-o e grava-o (0644)
    accoes escrever-segredos                         lê o accoes.env do stdin (sem eco), valida as chaves e grava-o
                                                     (0600); nunca imprime valores

Ambiente: ACCOES_PERFIL (só `lite`, a omissão), ACCOES_DIR, ACCOES_SEGREDOS, ACCOES_ENTRADA, ACCOES_DECISOES,
ACCOES_AGENTES, ACCOES_SAUDE. No arranque: `arranque.preparar` (com root: chown das raízes e largar privilégios; sem
root, nada) e recusa (exit 1) com `accoes.env` ou `accoes.json` com permissões largas, de outro uid ou symlink.

Saúde (ADR-022): cada passagem completa grava `<ACCOES_SAUDE>/saude.json` = {"v": 1, "em": ISO UTC, "avisos": […]}
(0644, atómico; o Office lê-o) e o stderr só leva `aviso: <texto>` dos avisos novos e `resolvido: <texto>` dos que
saíram, comparando com o `saude.json` anterior (no modo VPS cada passagem é um processo).
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # corre com python3 -I (sem a pasta do script)

import accoes
from servico_accoes import colector, executor

CODIGO = Path(__file__).resolve().parent.parent
SAUDE, SAUDE_MAX, AVISOS_MAX, AVISO_MAX = "saude.json", 8192, 10, 200
COMANDOS = ("ciclo", "passagem", "verificar", "anular", "desfazer", "escrever-config", "escrever-segredos")
_parar = False


def _erro(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _agora() -> datetime:
    return datetime.now(timezone.utc)   # noqa: UP017 — datetime.UTC só desde o 3.11 (VPS: 3.10)


def preparar(r: accoes.Raizes) -> None:
    from servico_accoes import arranque
    arranque.preparar([r.segredos, r.dir, r.entrada, r.saude])


def erro_arranque(r: accoes.Raizes) -> str | None:
    e = accoes.erro_arranque(r.config, r.env, CODIGO)
    if e is None:
        try:
            accoes.ler_config(r.config)
            accoes.ler_segredos(r.env)
        except accoes.ConfigErro as x:
            e = str(x)
    return e


def normalizar(avisos: list[str]) -> list[str]:
    """Não imprimíveis → espaço, cortados a AVISO_MAX, sem repetidos (1.ª ocorrência), no máximo AVISOS_MAX."""
    out = []
    for a in avisos:
        a = "".join(c if c.isprintable() else " " for c in a)[:AVISO_MAX]
        if a not in out:
            out.append(a)
    return out[:AVISOS_MAX]


def avisos_anteriores(r: accoes.Raizes) -> list[str]:
    """Os avisos do `saude.json` anterior; [] se faltar ou for inválido."""
    d = accoes.ler_json(r.saude / SAUDE, SAUDE_MAX)
    if isinstance(d, dict) and d.get("v") == 1 and isinstance(d.get("avisos"), list) \
            and all(isinstance(a, str) for a in d["avisos"]):
        return d["avisos"]
    return []


def gravar_saude(r: accoes.Raizes, em: datetime, avisos: list[str]) -> None:
    """Grava o `saude.json` (0644, atómico, um symlink no lugar é substituído); levanta OSError."""
    em = em.astimezone(timezone.utc)   # noqa: UP017 — datetime.UTC só desde o 3.11 (VPS: 3.10)
    dados = json.dumps({"v": 1, "em": em.isoformat(timespec="seconds"), "avisos": avisos}, ensure_ascii=False)
    accoes.escrever_atomico(r.saude / SAUDE, dados.encode("utf-8"), 0o644)


def passagem(r: accoes.Raizes, perfil: str) -> None:
    avisos, saltou = [], False
    for nome, f in (("colector", colector.passagem), ("executor", executor.passagem)):
        try:
            res = f(r, _agora(), perfil)
        except Exception as e:   # noqa: BLE001 — uma passagem falhada não pára o serviço
            res = [f"erro no {nome}: {type(e).__name__}"]
        if res is None:   # lock ocupado: outra passagem está a correr
            saltou = True
        else:
            avisos += res
    if saltou:   # passagem incompleta: nem aviso: nem resolvido: nem saude.json (a próxima completa compara e
        return   # escreve; senão o mesmo aviso saía aqui e outra vez nela)
    avisos, antes = normalizar(avisos), avisos_anteriores(r)
    for a in avisos:
        if a not in antes:
            _erro(f"aviso: {a}")
    for a in antes:
        if a not in avisos:
            _erro(f"resolvido: {a}")
    try:
        gravar_saude(r, _agora(), avisos)
    except (OSError, ValueError) as e:
        _erro(f"aviso: saude.json não gravado ({type(e).__name__})")


def ciclo(r: accoes.Raizes, perfil: str, intervalo: float, passagens: int | None) -> int:
    def fim(*_):
        global _parar
        _parar = True
    antigos = {s: signal.signal(s, fim) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        n = 0
        while not _parar:
            passagem(r, perfil)
            n += 1
            if passagens is not None and n >= passagens:
                break
            alvo = time.monotonic() + intervalo
            while not _parar and time.monotonic() < alvo:
                time.sleep(min(1.0, max(0.0, alvo - time.monotonic())))
    finally:
        for s, h in antigos.items():
            signal.signal(s, h)
    return 0


def _ler_stdin(maximo: int) -> bytes:
    """O stdin até ao fim; num terminal, sem eco (termina com Ctrl-D)."""
    antes = None
    if sys.stdin.isatty():
        import termios
        fd = sys.stdin.fileno()
        antes = termios.tcgetattr(fd)
        novo = termios.tcgetattr(fd)
        novo[3] &= ~termios.ECHO
        termios.tcsetattr(fd, termios.TCSANOW, novo)
        _erro("cola o conteúdo e termina com Ctrl-D (não aparece no ecrã)")
    try:
        dados = sys.stdin.buffer.read(maximo + 1)
    finally:
        if antes is not None:
            termios.tcsetattr(fd, termios.TCSANOW, antes)
    return dados


def escrever_config(r: accoes.Raizes) -> int:
    dados = _ler_stdin(accoes.CONFIG_MAX)
    try:
        accoes.config_de_texto(dados, r.dir)
    except accoes.ConfigErro as e:
        _erro(f"accoes.json não gravado: {e}")
        return 1
    accoes.escrever_atomico(r.config, dados, 0o644)
    print("ok: accoes.json gravado")
    return 0


def escrever_segredos(r: accoes.Raizes) -> int:
    dados = _ler_stdin(64 * 1024)
    try:
        if len(dados) > 64 * 1024:
            raise accoes.ConfigErro("credenciais: grandes demais")
        try:
            texto = dados.decode("utf-8")
        except UnicodeDecodeError:
            raise accoes.ConfigErro("credenciais: não é UTF-8") from None
        seg = accoes.segredos_de_texto(texto)
    except accoes.ConfigErro as e:
        _erro(f"accoes.env não gravado: {e}")
        return 1
    r.segredos.mkdir(mode=0o700, parents=True, exist_ok=True)
    accoes.escrever_atomico(r.env, dados, 0o600)
    print(f"ok: accoes.env gravado ({len(seg)} {'chave' if len(seg) == 1 else 'chaves'}: {', '.join(sorted(seg))})")
    return 0


def verificar(r: accoes.Raizes) -> int:
    """Uma linha por verificação (✓/✗), sem valores; sai 1 se alguma falhar."""
    cfg, seg = accoes.ler_config(r.config), accoes.ler_segredos(r.env)
    linhas = ["✓ permissões, configuração e credenciais"]
    for sid, sq in sorted(cfg["squads"].items()):
        if "email" not in sq:
            continue
        k = accoes.chave_sq(sid)
        if f"{k}_EMAIL_UTILIZADOR" not in seg or f"{k}_EMAIL_SENHA" not in seg:
            linhas.append(f"✗ {sid}: faltam {k}_EMAIL_UTILIZADOR/{k}_EMAIL_SENHA no accoes.env")
            continue
        for nome, f in (("IMAP", _verificar_imap), ("SMTP", _verificar_smtp)):
            try:
                f(sid, sq["email"], seg)
                linhas.append(f"✓ {sid}: {nome}")
            except Exception as e:   # noqa: BLE001 — o motivo vai para a linha, sem valores
                linhas.append(f"✗ {sid}: {nome}: {executor.motivo_erro(e)}")
    if "telegram" in cfg:
        if not seg.get("TELEGRAM_TOKEN"):
            linhas.append("✗ telegram: falta TELEGRAM_TOKEN no accoes.env")
        else:
            try:
                executor.telegram(cfg["telegram"], seg["TELEGRAM_TOKEN"], "getMe")
                linhas.append("✓ telegram")
            except Exception as e:   # noqa: BLE001
                linhas.append(f"✗ telegram: {executor.motivo_erro(e)}")
    for ln in linhas:
        print(ln)
    return 1 if any(ln.startswith("✗") for ln in linhas) else 0


def _verificar_imap(sid: str, conta: dict, seg: dict) -> None:
    c = executor.ligar_imap(conta)
    try:
        c.login(*executor.login(sid, seg))
        typ, _ = c.select('"' + conta["pasta"] + '"', readonly=True)
        if typ != "OK":
            raise executor.ErroServico("pasta de entrada inexistente")
    finally:
        c.logout()


def _verificar_smtp(sid: str, conta: dict, seg: dict) -> None:
    with executor.smtp_ligar(conta) as c:
        c.login(*executor.login(sid, seg))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="accoes", description="Serviço das acções aprovadas (ADR-021).")
    ap.add_argument("cmd", nargs="?", default="ciclo", choices=COMANDOS)
    ap.add_argument("sha", nargs="?")
    ap.add_argument("--intervalo", type=float, default=60.0)
    ap.add_argument("--passagens", type=int)
    a = ap.parse_args(argv)
    perfil = os.environ.get("ACCOES_PERFIL") or accoes.PERFIL
    if perfil not in accoes.PERFIS:
        _erro(f"accoes não arranca: ACCOES_PERFIL desconhecido (só {', '.join(accoes.PERFIS)})")
        return 2
    r = accoes.Raizes.do_ambiente()
    preparar(r)
    os.umask(0o077)
    try:   # as folhas CSV só podem viver em <dir>/folhas/ (ADR-021); criada aqui, com o dono do serviço
        (r.dir / "folhas").mkdir(mode=0o700, exist_ok=True)
    except OSError:
        pass
    if a.cmd == "escrever-config":
        return escrever_config(r)
    if a.cmd == "escrever-segredos":
        return escrever_segredos(r)
    erro = erro_arranque(r)
    if erro:
        _erro(f"accoes não arranca: {erro}")
        return 1
    if a.cmd == "verificar":
        return verificar(r)
    if a.cmd in ("anular", "desfazer"):
        rc, msg = executor.anular(r, a.sha) if a.cmd == "anular" else executor.desfazer(r, a.sha, _agora())
        print(msg)
        return rc
    if a.cmd == "passagem":
        passagem(r, perfil)
        return 0
    return ciclo(r, perfil, a.intervalo, a.passagens)


if __name__ == "__main__":
    sys.exit(main())

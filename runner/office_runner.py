#!/usr/bin/env python3
"""Runner do Squad Office Lite, no host (stdlib, Python >= 3.10). Corrido pelo timer systemd de utilizador.

    office_runner.py --uma-vez [--config FICH]            uma passagem: decisões → retomas → fila do Quadro → rotação
    office_runner.py correr TASK_ID [--retomar] [--config FICH]   (interno) corre o claude numa tarefa já preparada
    office_runner.py parar TASK_ID [--config FICH]        pára uma tarefa «a correr» (fecha em erro; não é retomada)
    office_runner.py doctor [--config FICH]               diagnóstico: uma linha ✓/✗ por verificação (exit 0/1)

Config: --config, senão $OFFICE_CONFIG, senão ~/office-lite/office.yaml. Lida em modo estrito: inválida → erro em
stderr, exit 1 e nada lançado (o `parar` usa então só `raizes.dados`; o `doctor` di-lo e segue). Lock não
bloqueante em <dados>/runner/.lock: nunca há duas passagens ao mesmo tempo.
Imprime uma linha por coisa tratada (vai para o journal); sem nada para fazer não imprime nada.
"""
from __future__ import annotations

import argparse
import ctypes
import fcntl
import hashlib
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(1, str(Path(__file__).resolve().parent.parent))
import decisoes
import diagnostico
import lancador
import seguro

import officecfg

SUFIXOS_ROTACAO = (".json", ".feito.json", ".movido.json")   # esta ordem (README, ADR-005)
PR_SET_DUMPABLE = 4


def nao_despejavel() -> None:
    """Linux: prctl(PR_SET_DUMPABLE, 0). A memória e o /proc/<pid>/environ do runner deixam de se ler por outros
    processos do utilizador, o agente incluído. O execve dos filhos (claude, git, gh) repõe-nos dumpable: por isso os
    segredos do runner.env saem do ambiente logo a seguir (lancador.guardar_segredos) e os filhos só recebem a lista
    branca. Noutros sistemas, ou se falhar, não faz nada."""
    if not sys.platform.startswith("linux"):
        return
    try:
        ctypes.CDLL(None, use_errno=True).prctl(PR_SET_DUMPABLE, 0, 0, 0, 0)
    except (OSError, AttributeError):
        pass


def cfg_parar(cfg_path: str) -> dict:
    """Config mínima do `parar` (só raizes.dados; fuso UTC) quando o office.yaml não valida. ConfigErro se nem isso."""
    return {"raizes": {"dados": officecfg.raiz_dados(cfg_path)}, "fuso": "UTC"}


def limpar_settings(cfg, limite: float) -> None:
    """Apaga as <dados>/runner/settings/<tid>.json de tarefas fechadas antes de `limite` (o correr já as apaga ao
    fechar; isto apanha as que ficaram, ex.: tarefa fechada pelo retomas()). Sem meta, conta a data do ficheiro."""
    pasta = officecfg.caminho(cfg, "runner") / "settings"
    tasks = officecfg.caminho(cfg, "tasks")
    try:
        nomes = [e.name for e in os.scandir(pasta) if e.name.endswith(".json")]
    except OSError:
        return
    for nome in nomes:
        tid = nome[: -len(".json")]
        if not lancador.TID.fullmatch(tid):
            continue
        meta = lancador.ler_meta(tasks / tid / "meta.json")
        try:
            if meta is None:
                velho = (pasta / nome).lstat().st_mtime < limite
            else:
                fim = meta.get("finished_at")
                velho = (meta.get("status") != "a correr" and isinstance(fim, str)
                         and datetime.fromisoformat(fim).timestamp() < limite)
            if velho:
                (pasta / nome).unlink()
        except (OSError, ValueError, TypeError):
            continue


def rodar(cfg, now: float | None = None) -> list[str]:
    """Move para feito/ os trios cujo .movido.json tem mais de rotacao_dias, pela ordem do ADR-005, e apaga as
    settings de tarefas fechadas há mais de rotacao_dias.

    A fila é escrita pelo contentor: tudo relativo a fds da fila e de `feito/`, sem seguir symlinks (seguro.py)."""
    fila = officecfg.caminho(cfg, "quadro_lancar")
    limite = (time.time() if now is None else now) - cfg["runner"]["rotacao_dias"] * 86400
    linhas = []
    limpar_settings(cfg, limite)
    try:
        dfd = seguro.abrir_pasta(fila)
    except OSError:
        return linhas
    ffd = None
    try:
        for nome in sorted(n for n in os.listdir(dfd) if n.endswith(".movido.json")):
            stem = nome[: -len(".movido.json")]
            if not lancador.NOME.fullmatch(stem + ".json"):
                continue
            try:
                if os.lstat(nome, dir_fd=dfd).st_mtime > limite:
                    continue
                if ffd is None:
                    ffd = seguro.subpasta(dfd, "feito")
                for suf in SUFIXOS_ROTACAO:
                    try:
                        seguro.mover(dfd, f"{stem}{suf}", ffd)
                    except FileNotFoundError:
                        pass
            except OSError:
                linhas.append(f"pedido {stem} não arquivado: erro a mover para feito/")
                continue
            linhas.append(f"pedido {stem} arquivado em feito/")
    finally:
        if ffd is not None:
            os.close(ffd)
        os.close(dfd)
    return linhas


def vigiar_config(cfg, cfg_path: str) -> list[str]:
    """Aviso (journal e Telegram) quando o office.yaml muda entre passagens, uma vez por mudança (C4). O sha256 fica
    em <dados>/runner/office.yaml.sha256; na primeira passagem só se grava. Erros de I/O não derrubam a passagem."""
    marca = officecfg.caminho(cfg, "runner") / "office.yaml.sha256"
    try:
        novo = hashlib.sha256(Path(cfg_path).read_bytes()).hexdigest()
        try:
            antigo = marca.read_text(encoding="ascii").strip()
        except FileNotFoundError:
            antigo = None
        if antigo == novo:
            return []
        tmp = marca.with_name(f".{marca.name}.tmp")
        tmp.write_text(novo + "\n", encoding="ascii")
        os.replace(tmp, marca)
    except (OSError, UnicodeDecodeError):
        return []
    if antigo is None:
        return []
    lancador.avisar("Squad Office: o office.yaml mudou. Se não foste tu, revê-o já.")
    return [f"office.yaml mudou (era {antigo[:8]}, é {novo[:8]}); se não foste tu, revê-o já"]


def uma_vez(cfg, cfg_path: str, now: float | None = None) -> list[str]:
    runner = officecfg.caminho(cfg, "runner")
    runner.mkdir(parents=True, exist_ok=True)
    with open(runner / ".lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return []
        try:   # o sinal do runner para o Office; um erro de I/O nunca derruba a passagem
            diagnostico.gravar_estado(cfg, cfg_path, now)
        except (OSError, ValueError):
            pass
        linhas = vigiar_config(cfg, cfg_path)
        linhas += decisoes.processar(officecfg.caminho(cfg, "decisoes_fila"), officecfg.caminho(cfg, "decisoes"),
                                     runner / ".historico.json", cfg["utilizador"]["nome"], lancador.agora(cfg, now))
        linhas += lancador.retomas(cfg, cfg_path, now)
        linhas += lancador.processar_fila(cfg, cfg_path, now)
        linhas += rodar(cfg, now)
    return linhas


def main(argv: list[str]) -> int:
    nao_despejavel()
    lancador.guardar_segredos()   # fora do os.environ antes de qualquer filho (git, gh, claude, correr)
    ap = argparse.ArgumentParser(prog="office_runner.py", description="Runner do Squad Office Lite.")
    ap.add_argument("--config", help="office.yaml (omissão: $OFFICE_CONFIG ou ~/office-lite/office.yaml)")
    ap.add_argument("--uma-vez", action="store_true", help="uma passagem pela fila e sai")
    sub = ap.add_subparsers(dest="accao")
    c = sub.add_parser("correr", help="(interno) corre o claude numa tarefa preparada")
    c.add_argument("task_id")
    c.add_argument("--retomar", action="store_true")
    c.add_argument("--config", dest="config_sub")
    p = sub.add_parser("parar", help="pára uma tarefa «a correr» (fecha em erro; não é retomada)")
    p.add_argument("task_id")
    p.add_argument("--config", dest="config_sub")
    d = sub.add_parser("doctor", help="diagnóstico da instalação: ✓/✗ e o comando que corrige")
    d.add_argument("--config", dest="config_sub")
    a = ap.parse_args(argv)
    if not a.uma_vez and a.accao not in ("correr", "parar", "doctor"):
        ap.print_usage(sys.stderr)
        return 2
    if a.accao == "correr":   # destacado: os segredos chegam por um pipe no stdin (à mão, sem pipe: só o ambiente)
        lancador.ler_segredos_stdin()
    cfg_path = (getattr(a, "config_sub", None) or a.config or os.environ.get("OFFICE_CONFIG")
                or os.path.expanduser("~/office-lite/office.yaml"))
    if a.accao == "doctor":   # antes de validar a config: o doctor diz se é inválida e segue
        codigo, linhas = diagnostico.doctor(cfg_path)
        print("\n".join(linhas), flush=True)
        return codigo
    try:
        cfg, _ = officecfg.carregar(cfg_path, estrito=True)
    except officecfg.ConfigErro as e:
        if a.accao != "parar":
            print(f"erro: config inválida: {e}", file=sys.stderr)
            return 1
        try:   # o parar é o que o README manda usar quando a config está estragada: basta-lhe raizes.dados
            cfg = cfg_parar(cfg_path)
        except officecfg.ConfigErro as e2:
            print(f"erro: config inválida: {e}; o parar precisa de ler raizes.dados e não conseguiu: {e2}",
                  file=sys.stderr)
            return 1
        print(f"aviso: config inválida ({e}); o parar usa só raizes.dados", file=sys.stderr)
    if a.accao == "parar":
        codigo, msg = lancador.parar(cfg, a.task_id)
        print(msg, file=sys.stdout if codigo == 0 else sys.stderr)
        return codigo
    if a.accao == "correr":
        try:
            meta = lancador.correr(cfg, a.task_id, retomar=a.retomar, cfg_path=os.path.abspath(cfg_path))
        except lancador.Falha as e:
            print(f"erro: {e}", file=sys.stderr)
            return 1
        print(f"tarefa {a.task_id}: {meta['status']}")
        return 0
    for linha in uma_vez(cfg, os.path.abspath(cfg_path)):
        print(linha, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

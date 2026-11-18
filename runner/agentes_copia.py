"""Cópia dos agentes da squad para o worktree da tarefa (ADR-017, CA-14). Stdlib só, Python >= 3.10.

`<dados>/agentes/<squad>/agents/*.md`, `skills/<skill>/…` e `CLAUDE.md` → `<worktree>/.claude/agents/…`,
`.claude/skills/…` e `.claude/CLAUDE.md`, para o `claude` os ler como agentes do projecto sem mudar o argv nem o HOME.
Leituras e escritas por fds `O_NOFOLLOW` (a pasta dos agentes e o worktree podem ter sido mexidos por um agente):
um symlink no caminho = nada copiado nesse ramo + aviso. Um caminho só se escreve se não existir no worktree ou se foi o
runner a copiá-lo antes (`ja_copiados`, na retoma); senão o do repo ganha e fica um aviso. Os caminhos copiados vão,
ancorados, para o `info/exclude` do repo: nunca entram em commits (se um `!` do `.gitignore` os desfizer, a cópia
sai e fica um aviso).
"""
from __future__ import annotations

import json
import os
import re
import stat
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(1, str(Path(__file__).resolve().parent))
import seguro

import accoes
import agentes
import officecfg

_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FICH = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
PROFUNDIDADE = 4               # pastas dentro de skills/<skill>/
TOTAL_MAX = 2 * 1024 * 1024    # por squad
FICHEIRO_MAX = agentes.MD_MAX
ERRO = "agentes da squad não copiados (erro de escrita no worktree)"
ACCOES_ENTRADA = "accoes-entrada"   # <dados>/accoes-entrada/<squad>/mensagens/ (só o serviço accoes escreve; ADR-021)
MENSAGEM = re.compile(r"m[0-9a-f]{16}\.txt")
MENSAGENS_MAX = 20             # por tarefa (CA-4)
MENSAGEM_MAX = 32 * 1024       # o serviço corta o texto a 20 KB; folga para o cabeçalho
ENTREGUES = "accoes-entregues"  # <dados>/runner/accoes-entregues/<squad>.json = {id: data da entrega}
ENTREGUES_DIAS = 90


def _ler(dfd: int, nome: str, maximo: int) -> bytes | None:
    try:
        fd = os.open(nome, _FICH, dir_fd=dfd)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        raw = b""
        while len(raw) <= maximo:
            bloco = os.read(fd, maximo + 1 - len(raw))
            if not bloco:
                break
            raw += bloco
    except OSError:
        return None
    finally:
        os.close(fd)
    return raw if len(raw) <= maximo else None


def _abrir(dfd: int, nome: str) -> int | None:
    try:
        return os.open(nome, _DIR, dir_fd=dfd)
    except OSError:
        return None


def _skill(dfd: int, prefixo: str, nivel: int, out: list) -> None:
    for nome in sorted(os.listdir(dfd)):
        if nome.startswith("."):
            continue
        try:
            st = os.lstat(nome, dir_fd=dfd)
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode):
            raw = _ler(dfd, nome, FICHEIRO_MAX)
            if raw is not None:
                out.append((f"{prefixo}/{nome}", raw))
        elif stat.S_ISDIR(st.st_mode) and nivel < PROFUNDIDADE:
            sub = _abrir(dfd, nome)
            if sub is not None:
                try:
                    _skill(sub, f"{prefixo}/{nome}", nivel + 1, out)
                finally:
                    os.close(sub)


def fontes(base_squad: Path) -> list[tuple[str, bytes]]:
    """(caminho relativo a `.claude/`, bytes) do que copiar, por ordem: agents/*.md, skills/<s>/…, CLAUDE.md.
    Só ficheiros regulares, sem symlinks em lado nenhum; pasta da squad ausente ou symlink → []."""
    out: list[tuple[str, bytes]] = []
    try:
        bfd = os.open(base_squad, _DIR)
    except OSError:
        return out
    try:
        afd = _abrir(bfd, "agents")
        if afd is not None:
            try:
                for nome in sorted(os.listdir(afd)):
                    if nome.endswith(".md") and agentes.ID_AGENTE.fullmatch(nome[:-3]):
                        raw = _ler(afd, nome, agentes.MD_MAX)
                        if raw is not None:
                            out.append((f"agents/{nome}", raw))
            finally:
                os.close(afd)
        sfd = _abrir(bfd, "skills")
        if sfd is not None:
            try:
                for nome in sorted(os.listdir(sfd)):
                    if not agentes.ID_AGENTE.fullmatch(nome):
                        continue
                    kfd = _abrir(sfd, nome)
                    if kfd is None:
                        continue
                    try:
                        if _ler(kfd, "SKILL.md", FICHEIRO_MAX) is not None:
                            _skill(kfd, f"skills/{nome}", 0, out)
                    finally:
                        os.close(kfd)
            finally:
                os.close(sfd)
        raw = _ler(bfd, "CLAUDE.md", agentes.MD_MAX)
        if raw is not None:
            out.append(("CLAUDE.md", raw))
    finally:
        os.close(bfd)
    return out


def _escrever(cfd: int, rel: str, raw: bytes, nosso: bool) -> str:
    """Escreve `.claude/<rel>` relativo a cfd. Devolve "ok", "repo" (já existe e não é nosso) ou "symlink"."""
    *pastas, nome = rel.split("/")
    fd = os.dup(cfd)
    try:
        for p in pastas:
            try:
                os.mkdir(p, 0o750, dir_fd=fd)
            except FileExistsError:
                pass
            novo = _abrir(fd, p)
            if novo is None:
                return "symlink"
            os.close(fd)
            fd = novo
        try:
            os.lstat(nome, dir_fd=fd)
            existe = True
        except FileNotFoundError:
            existe = False
        if existe and not nosso:
            return "repo"
        seguro.escrever(fd, nome, raw)
        return "ok"
    finally:
        os.close(fd)


def _excluir(wt: Path, caminhos: list[str]) -> None:
    """Acrescenta `/<caminho>` (ancorado) ao info/exclude do repo, uma vez cada."""
    import lancador   # aqui: o lancador importa este módulo
    r = lancador.git(["rev-parse", "--git-path", "info/exclude"], wt)
    if r.returncode != 0 or not r.stdout.strip():
        return
    excl = wt / r.stdout.strip()
    try:
        if excl.is_symlink():
            return
        actual = excl.read_text(encoding="utf-8") if excl.exists() else ""
        novos = [f"/{c}" for c in caminhos if f"/{c}" not in actual.split("\n")]
        if novos:
            excl.parent.mkdir(parents=True, exist_ok=True)
            sep = "" if actual.endswith("\n") or not actual else "\n"
            excl.write_text(actual + sep + "\n".join(novos) + "\n", encoding="utf-8")
    except OSError:
        pass


def copiar(cfg, squad, wt, ja_copiados) -> tuple[list[str], list[str]]:
    """Copia os agentes da squad para `<wt>/.claude/`. Devolve (copiados, avisos): `copiados` são os caminhos
    relativos ao worktree (`.claude/agents/x.md`) escritos agora ou antes pelo runner; nunca levanta."""
    if not isinstance(squad, str) or not agentes.ID_SQUAD.fullmatch(squad):
        return [], []
    antes = [c for c in (ja_copiados or []) if isinstance(c, str)]
    lista = fontes(officecfg.caminho(cfg, "agentes") / squad)
    if not lista:
        return antes, []
    avisos, escritos, total = [], [], 0
    wt = Path(wt)
    try:
        wfd = os.open(wt, _DIR)
    except OSError:
        return antes, [ERRO]
    try:
        try:
            os.mkdir(".claude", 0o750, dir_fd=wfd)
        except FileExistsError:
            pass
        cfd = _abrir(wfd, ".claude")
        if cfd is None:
            return antes, ["o repo tem .claude como symlink ou ficheiro: agentes da squad não copiados"]
        try:
            for rel, raw in lista:
                total += len(raw)
                if total > TOTAL_MAX:
                    avisos.append("agentes da squad com mais de 2 MB: o resto não foi copiado")
                    break
                caminho = f".claude/{rel}"
                r = _escrever(cfd, rel, raw, caminho in antes)
                if r == "ok":
                    escritos.append(caminho)
                elif r == "repo":
                    avisos.append(f"o repo já tem {caminho}: ficou o do repo")
                else:
                    avisos.append(f"symlink no caminho de {caminho}: não copiado")
        finally:
            os.close(cfd)
    except OSError:
        avisos.append(ERRO)
    finally:
        os.close(wfd)
    _excluir(wt, escritos)
    for caminho in _nao_ignorados(wt, escritos):   # um `!` no .gitignore ganha ao info/exclude: a cópia sai
        try:
            os.unlink(wt / caminho)
        except OSError:
            pass
        escritos.remove(caminho)
        antes = [x for x in antes if x != caminho]
        avisos.append(f"o .gitignore do repo não deixa ignorar {caminho}: cópia apagada")
    return sorted(set(antes) | set(escritos)), avisos


def _nao_ignorados(wt: Path, caminhos: list[str]) -> list[str]:
    """Os caminhos que o git não ignora (`check-ignore` sai com 1); fora de um repo (128) não há commits a temer."""
    import lancador
    return [x for x in caminhos if lancador.git(["check-ignore", "-q", "--", x], wt).returncode == 1]


# ---------------------------------------------------------------- mensagens das squads de negócio (ADR-021, D6)

def _mensagens(cfg, squad: str) -> list[tuple[float, str, bytes]]:
    """(mtime, nome, bytes) das `m<16 hex>.txt` regulares de `<dados>/accoes-entrada/<squad>/mensagens/` (de outro
    utilizador: abre-se sem seguir symlinks, sem exigir o dono). Pasta ausente ou symlink → []."""
    out = []
    try:
        rfd = os.open(officecfg.raiz(cfg, "dados") / ACCOES_ENTRADA, _DIR)
    except OSError:
        return out
    try:
        sfd = _abrir(rfd, squad)
        mfd = _abrir(sfd, "mensagens") if sfd is not None else None
        if sfd is not None:
            os.close(sfd)
        if mfd is None:
            return out
        try:
            for nome in sorted(os.listdir(mfd)):
                if not MENSAGEM.fullmatch(nome):
                    continue
                try:
                    st = os.lstat(nome, dir_fd=mfd)
                except OSError:
                    continue
                raw = _ler(mfd, nome, MENSAGEM_MAX) if stat.S_ISREG(st.st_mode) else None
                if raw is not None:
                    out.append((st.st_mtime, nome, raw))
        finally:
            os.close(mfd)
    finally:
        os.close(rfd)
    return out


def _entregues(cfg, squad: str, now: float) -> dict:
    """{id: data} das mensagens já entregues a esta squad, sem as com mais de ENTREGUES_DIAS."""
    raw = agentes.ler_bytes(officecfg.caminho(cfg, "runner") / ENTREGUES / f"{squad}.json", 1024 * 1024)
    try:
        d = json.loads(raw.decode("utf-8")) if raw is not None else {}
    except ValueError:
        d = {}
    out = {}
    for k, v in (d.items() if isinstance(d, dict) else []):
        try:
            if isinstance(k, str) and now - datetime.fromisoformat(v).timestamp() <= ENTREGUES_DIAS * 86400:
                out[k] = v
        except (TypeError, ValueError):
            continue
    return out


def _gravar_entregues(cfg, squad: str, doc: dict) -> None:
    runner = officecfg.caminho(cfg, "runner")
    runner.mkdir(parents=True, exist_ok=True)
    rfd = seguro.abrir_pasta(runner)
    try:
        efd = seguro.subpasta(rfd, ENTREGUES)
        try:
            seguro.substituir(efd, f"{squad}.json", json.dumps(doc, sort_keys=True).encode("utf-8"))
        finally:
            os.close(efd)
    finally:
        os.close(rfd)


def copiar_mensagens(cfg, squad, wt, ja, now: float | None = None) -> tuple[list[str], list[str]]:
    """CA-4: copia para `<wt>/entrada/mensagens/` as mensagens que o serviço `accoes` recolheu para a squad.

    Primeira vez (`ja` vazio): as MENSAGENS_MAX mais antigas (mtime, depois nome) ainda não entregues a esta squad,
    que passam a constar do registo `<dados>/runner/accoes-entregues/<squad>.json`. Na retoma (`ja` = as copiadas
    antes) recopia só essas. Os caminhos vão para o `info/exclude` (como os agentes) e o do repo ganha. Devolve
    (copiados, avisos), copiados relativos ao worktree; nunca levanta."""
    if not isinstance(squad, str) or not agentes.ID_SQUAD.fullmatch(squad):
        return [], []
    t = time.time() if now is None else now
    antes = [c for c in (ja or []) if isinstance(c, str)]
    todas = _mensagens(cfg, squad)
    if antes:
        nomes = {c.rsplit("/", 1)[-1] for c in antes}
        escolhidas = [(n, raw) for _, n, raw in todas if n in nomes]
        entregues = None
    else:
        entregues = _entregues(cfg, squad, t)
        novas = sorted((m, n, raw) for m, n, raw in todas if n[:-4] not in entregues)
        escolhidas = [(n, raw) for _, n, raw in novas[:MENSAGENS_MAX]]
    if not escolhidas:
        return antes, []
    avisos, escritos = [], []
    try:
        wfd = os.open(Path(wt), _DIR)
    except OSError:
        return antes, ["mensagens não copiadas (erro de escrita no worktree)"]
    try:
        for nome, raw in escolhidas:
            caminho = f"entrada/mensagens/{nome}"
            r = _escrever(wfd, caminho, raw, caminho in antes)
            if r == "ok":
                escritos.append(caminho)
            elif r == "repo":
                avisos.append(f"o repo já tem {caminho}: ficou o do repo")
            else:
                avisos.append(f"symlink no caminho de {caminho}: não copiado")
    except OSError:
        avisos.append("mensagens não copiadas (erro de escrita no worktree)")
    finally:
        os.close(wfd)
    _excluir(Path(wt), escritos)
    for caminho in _nao_ignorados(Path(wt), escritos):
        try:
            os.unlink(Path(wt) / caminho)
        except OSError:
            pass
        escritos.remove(caminho)
        avisos.append(f"o .gitignore do repo não deixa ignorar {caminho}: cópia apagada")
    if entregues is not None and escritos:
        dia = datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds")
        try:
            _gravar_entregues(cfg, squad, {**entregues, **{c.rsplit("/", 1)[-1][:-4]: dia for c in escritos}})
        except OSError:
            avisos.append("registo das mensagens entregues não gravado")
    return sorted(set(antes) | set(escritos)), avisos


def texto_accoes(vista: dict) -> str:
    """`entrada/accoes.md` (C2): acções ligadas, domínios e folhas com colunas da vista; nunca remetentes nem caixa."""
    folhas = [f"- {n}: {', '.join(c)}" for n, c in sorted(vista["folhas"].items())] or ["- nenhuma"]
    return ("# Acções desta squad\n\nGerado pelo runner a partir da configuração do serviço de acções; só leitura.\n\n"
            f"Acções ligadas: {', '.join(vista['ligadas']) or 'nenhuma'}.\n"
            f"Domínios permitidos nos links: {', '.join(vista['dominios']) or 'nenhum (não ponhas links)'}.\n"
            "Folhas e colunas (folha_linha só com estas):\n" + "\n".join(folhas) + "\n")


def copiar_accoes(cfg, squad, wt) -> tuple[list[str], list[str]]:
    """C2: escreve `<wt>/entrada/accoes.md` a partir de `<dados>/accoes-entrada/<squad>/vista.json` (sem vista: a
    omissão, como o accoes_propor) e põe-no no `info/exclude`. O do repo ganha. Devolve (escritos, avisos); nunca
    levanta."""
    if not isinstance(squad, str) or not agentes.ID_SQUAD.fullmatch(squad):
        return [], []
    p = officecfg.raiz(cfg, "dados") / ACCOES_ENTRADA / squad / "vista.json"
    vista = accoes.validar_vista(accoes.ler_json(p, accoes.VISTA_MAX)) or accoes.contexto_omissao()
    caminho = "entrada/accoes.md"
    try:
        wfd = os.open(Path(wt), _DIR)
    except OSError:
        return [], [f"{caminho} não escrito (erro de escrita no worktree)"]
    try:
        r = _escrever(wfd, caminho, texto_accoes(vista).encode("utf-8"), False)
    except OSError:
        return [], [f"{caminho} não escrito (erro de escrita no worktree)"]
    finally:
        os.close(wfd)
    if r == "repo":
        return [], [f"o repo já tem {caminho}: ficou o do repo"]
    if r != "ok":
        return [], [f"symlink no caminho de {caminho}: não escrito"]
    _excluir(Path(wt), [caminho])
    if _nao_ignorados(Path(wt), [caminho]):
        try:
            os.unlink(Path(wt) / caminho)
        except OSError:
            pass
        return [], [f"o .gitignore do repo não deixa ignorar {caminho}: cópia apagada"]
    return [caminho], []

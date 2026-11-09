#!/usr/bin/env python3
"""Gera o docker-compose.yml do Squad Office Lite a partir do office.yaml (stdlib, Python >= 3.10).

    python3 runner/gerar_compose.py --config FICH [--proxy caddy|traefik] [--rede R] [--certresolver C]
                                    [--dominio D] [--repo DIR] > docker-compose.yml

Saída determinística (mesma entrada -> mesmos bytes). Os mounts derivam só das raízes da config (officecfg.MOUNTS);
o compose lê OFFICE_UID, OFFICE_GID, OFFICE_SITE, OFFICE_DOMINIO, OFFICE_AUTH_USER, OFFICE_AUTH_HASH e
OFFICE_PROXY_SEGREDO do .env ao lado (sem o segredo o compose recusa arrancar: o Office nunca fica aberto na rede).
O docker-compose.example.yml do repo é esta saída para o office.example.yaml com HOME=/home/utilizador
(ver tests/test_compose.py).

    python3 runner/gerar_compose.py --modo docker [--imagem-office REF] [--imagem-runner REF] > compose.yaml

Modo Docker (ADR-008): compose do PC com office, caddy, runner e configurar, independente do office.yaml (dados em
volumes nomeados, projectos num bind). Todas as variáveis têm omissão (o ficheiro funciona sem .env). O
compose.example.yaml do repo é esta saída com as refs por omissão (ver tests/test_compose_docker.py).
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import officecfg  # noqa: E402

# digest do index multi-arquitectura (amd64 e arm64); actualizar à mão (o Dependabot não lê este ficheiro)
CADDY_IMAGEM = "caddy:2.11.9-alpine@sha256:0bbb055bda6793c999bde78b1b5ce3e89f829fb031af2cf50056ea1246559fee"
_NOME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
# o proxy põe-no no cabeçalho X-Office-Segredo depois do basic auth e o Office exige-o (ADR-006)
SEGREDO = "${OFFICE_PROXY_SEGREDO:?falta no .env, corre o install.sh}"
_DOMINIO = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def _q(s):
    """Escalar YAML entre aspas, com os `$` escapados para o compose não os interpolar."""
    return json.dumps(str(s).replace("$", "$$"), ensure_ascii=False)


def _bind(origem, destino, so_leitura):
    return ["      - type: bind", f"        source: {_q(origem)}", f"        target: {destino}",
            f"        read_only: {'true' if so_leitura else 'false'}"]


def gerar(cfg, config, proxy="caddy", rede=None, certresolver="letsencrypt", dominio=None, repo=REPO):
    contacto = cfg["utilizador"]["contacto"]
    push = f"https://{dominio}" if dominio else (f"mailto:{contacto}" if contacto else None)
    out = ["# Gerado por runner/gerar_compose.py a partir do office.yaml; não editar à mão (corre de novo o install.sh).",
           "name: office-lite", "services:", "  office:", f"    build: {_q(repo)}", "    restart: unless-stopped",
           "    read_only: true",
           "    # Pillow descodifica uma miniatura de cada vez (até ~330 MB) + tmpfs das miniaturas + servidor",
           "    mem_limit: 768m", '    user: "${OFFICE_UID}:${OFFICE_GID}"', "    environment:",
           "      OFFICE_CONFIG: /config/office.yaml", f"      OFFICE_PROXY_SEGREDO: {SEGREDO}"]
    out += [f"      {var}: {destino}" for _, destino, _, var in officecfg.MOUNTS]
    if push:
        out.append(f"      PUSH_CONTACTO: {_q(push)}")
    out.append("    volumes:")
    cred = officecfg.credenciais_claude(config)
    for origem, destino, modo, _ in officecfg.MOUNTS:
        p = officecfg.raiz(cfg, origem) if origem in officecfg.RAIZES else officecfg.caminho(cfg, origem)
        if p == cred or p in cred.parents:   # as credenciais do claude nunca entram no contentor
            raise officecfg.ConfigErro(f"raizes: a pasta montada em {destino} contém o {officecfg.CLAUDE_ENV}")
        out += _bind(p, destino, modo == "ro")
    out += _bind(Path(os.path.abspath(config)), "/config/office.yaml", True)
    out += ["    tmpfs:", "      - /tmp/miniaturas:size=128m,uid=${OFFICE_UID},gid=${OFFICE_GID},mode=0700"]
    if proxy == "traefik":
        r = "office-lite"
        out += ["    networks:", f"      - {rede}", "    labels:",
                '      - "traefik.enable=true"', f'      - "traefik.docker.network={rede}"',
                f'      - "traefik.http.routers.{r}.rule=Host(`${{OFFICE_DOMINIO}}`)"',
                f'      - "traefik.http.routers.{r}.entrypoints=websecure"',
                f'      - "traefik.http.routers.{r}.tls.certresolver={certresolver}"',
                # pela ordem: os cabeçalhos de resposta (primeiro, para o 401 do basic auth também os levar), o basic
                # auth e o cabeçalho com o segredo (substitui o que vier do cliente). Sem contentsecuritypolicy: o
                # Traefik substituiria a CSP do Office (a da Biblioteca leva sandbox); o framedeny chega
                f'      - "traefik.http.routers.{r}.middlewares={r}-cab,{r}-auth,{r}-segredo"',
                f'      - "traefik.http.middlewares.{r}-auth.basicauth.users=${{OFFICE_AUTH_USER}}:${{OFFICE_AUTH_HASH}}"',
                f'      - "traefik.http.middlewares.{r}-segredo.headers.customrequestheaders.X-Office-Segredo={SEGREDO}"',
                f'      - "traefik.http.middlewares.{r}-cab.headers.framedeny=true"',
                f'      - "traefik.http.middlewares.{r}-cab.headers.referrerpolicy=same-origin"',
                f'      - "traefik.http.middlewares.{r}-cab.headers.stsseconds=31536000"',
                f'      - "traefik.http.services.{r}.loadbalancer.server.port=8080"',
                "networks:", f"  {rede}:", "    external: true"]
    else:
        out += ["  caddy:", f"    image: {CADDY_IMAGEM}", "    restart: unless-stopped", "    depends_on:",
                "      - office", "    mem_limit: 256m", "    # único serviço com portas publicadas",
                "    ports:", '      - "80:80"']
        if dominio:
            out.append('      - "443:443"')
        out += ["    environment:", "      OFFICE_SITE: ${OFFICE_SITE}", "      OFFICE_AUTH_USER: ${OFFICE_AUTH_USER}",
                "      OFFICE_AUTH_HASH: ${OFFICE_AUTH_HASH}", f"      OFFICE_PROXY_SEGREDO: {SEGREDO}", "    volumes:"]
        out += _bind(Path(repo) / "Caddyfile", "/etc/caddy/Caddyfile", True)
        out += ["      - caddy_data:/data", "      - caddy_config:/config",
                "volumes:", "  caddy_data:", "  caddy_config:"]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- modo Docker (ADR-008)

IMAGEM_OFFICE = f"ghcr.io/mozthinker/squad-office-lite:{officecfg.VERSAO}"
IMAGEM_RUNNER = f"ghcr.io/mozthinker/squad-office-lite-runner:{officecfg.VERSAO}"
# repositório[:porta]/caminho em minúsculas, com tag e/ou digest (nunca só o nome, que seria o latest)
_IMAGEM = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*(?::[0-9]{1,5})?(?:/[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*)+"
                     r"(?::(?P<tag>[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}))?(?P<digest>@sha256:[0-9a-f]{64})?$")
UID = "${OFFICE_UID:-10001}"
GID = "${OFFICE_GID:-10001}"
CADDYFILE_DOCKER = REPO / "Caddyfile.docker"
# no runner cada volume de MOUNTS fica em /dados/<volume> (nome = pasta de officecfg.SUB), salvo estes
_DADOS = {"claude_projects": "/home/office/.claude/projects"}


def imagem_valida(ref):
    m = _IMAGEM.match(ref or "")
    return bool(m) and bool(m["tag"] or m["digest"]) and m["tag"] not in ("latest", "stable")


def _vol(origem):
    return "claude-projects" if origem == "claude_projects" else officecfg.SUB[origem]


def _volume(nome, destino, so_leitura=False):
    """Volume nomeado com nocopy: o Docker não copia para um volume vazio o que a imagem tem no ponto de montagem
    (nem o dono, que desfazia o chown do configurar a cada arranque); o dono e o modo das raízes vêm do configurar."""
    return (["      - type: volume", f"        source: {nome}", f"        target: {destino}"]
            + (["        read_only: true"] if so_leitura else []) + ["        volume:", "          nocopy: true"])


def _dados_runner(configurar):
    """Volumes do runner (e do configurar, que também escreve config, segredos e proxy)."""
    out = []
    for o, _, _, _ in officecfg.MOUNTS:
        out += _volume(_vol(o), _DADOS.get(o, "/dados/" + _vol(o)))
    out += _volume("runner", "/dados/runner") + _volume("runner-home", "/home/office")
    # claude-projects montado dentro de runner-home: o Docker monta primeiro o caminho mais curto
    out += _volume("config", "/config", not configurar) + _volume("segredos", "/segredos", not configurar)
    if configurar:
        out += _volume("proxy", "/proxy")
    out += ["      - type: bind", '        source: "${OFFICE_PROJECTOS:-./projectos}"', "        target: /projectos",
            "        bind:", "          create_host_path: true"]
    return out


def _endurecer(*capacidades):
    """no-new-privileges, cap_drop ALL e só as capacidades dadas ("NOME: porquê"), cada uma com a justificação."""
    out = ["    security_opt:", "      - no-new-privileges:true", "    cap_drop:", "      - ALL"]
    if capacidades:
        out.append("    cap_add:")
        for c in capacidades:
            nome, porque = c.split(": ", 1)
            out += [f"      # {porque}", f"      - {nome}"]
    return out


def gerar_docker(imagem_office=IMAGEM_OFFICE, imagem_runner=IMAGEM_RUNNER):
    """Compose do modo Docker; não depende do office.yaml nem do host (mesmas refs -> mesmos bytes)."""
    if not (imagem_valida(imagem_office) and imagem_valida(imagem_runner)):
        raise ValueError("referência de imagem inválida")
    caddyfile = CADDYFILE_DOCKER.read_text(encoding="utf-8")
    out = [f"# Squad Office Lite {officecfg.VERSAO}, modo Docker. Gerado por runner/gerar_compose.py --modo docker.",
           "# Primeiro: docker compose run --rm configurar",
           "# Depois: docker compose up -d e abre http://localhost:8080",
           "# Opcional: .env ao lado (ver env.example) com OFFICE_ENDERECO, OFFICE_PORTA, OFFICE_PROJECTOS,",
           "# OFFICE_UID e OFFICE_GID. Sem .env valem as omissões abaixo (${VAR:-omissão}).",
           "# Nome próprio (o modo VPS é office-lite): os dois modos podem coexistir no mesmo Linux.",
           "name: office-lite-pc", "services:",
           "  office:", f"    image: {imagem_office}", "    restart: unless-stopped", "    read_only: true",
           "    # Pillow descodifica uma miniatura de cada vez (até ~330 MB) + tmpfs das miniaturas + servidor",
           "    mem_limit: 768m", f'    user: "{UID}:{GID}"', "    environment:",
           "      OFFICE_MODO: docker", "      OFFICE_CONFIG: /config/office.yaml",
           "      # escrito pelo configurar; em falta o Office responde 503 a tudo excepto /healthz",
           "      OFFICE_PROXY_SEGREDO_FILE: /proxy/segredo"]
    out += [f"      {var}: {destino}" for _, destino, _, var in officecfg.MOUNTS]
    out.append("    # sem capacidades: um servidor HTTP do Python na porta 8080, sem root")
    out += _endurecer()
    out.append("    volumes:")
    for o, destino, modo, _ in officecfg.MOUNTS:
        out += _volume(_vol(o), destino, modo == "ro")
    out += _volume("config", "/config", True) + _volume("proxy", "/proxy", True)
    out += ["    tmpfs:", f"      - /tmp/miniaturas:size=128m,uid={UID},gid={GID},mode=0700",
            "    networks:", "      - office"]
    out += ["  caddy:", f"    image: {CADDY_IMAGEM}", "    restart: unless-stopped", "    depends_on:",
            "      - office", "    mem_limit: 256m"]
    # o Caddy corre como root, mas sem as capacidades por omissão do Docker; só estas duas
    out += _endurecer("NET_BIND_SERVICE: o /usr/bin/caddy da imagem tem esta file capability; sem ela o execve falha",
                      "DAC_OVERRIDE: ler /proxy (0700, ficheiros 0600 de OFFICE_UID) sendo root")
    out += ["    # único serviço com portas publicadas; OFFICE_ENDERECO=0.0.0.0 abre-o à rede local",
            "    ports:", '      - "${OFFICE_ENDERECO:-127.0.0.1}:${OFFICE_PORTA:-8080}:80"',
            "    configs:", "      - source: caddyfile", "        target: /etc/caddy/Caddyfile", "    volumes:"]
    # caddy-data e caddy-config sem nocopy: precisam do conteúdo da imagem
    out += _volume("proxy", "/proxy", True) + ["      - caddy-data:/data", "      - caddy-config:/config",
                                                 "    networks:", "      - office"]
    # runner: ciclo em vez do timer; tini em PID 1 recolhe os netos órfãos (D7). Sem socket do Docker (D8)
    out += ["  runner:", f"    image: {imagem_runner}", "    restart: unless-stopped", "    init: true",
            "    read_only: true", f'    user: "{UID}:{GID}"',
            "    # sem capacidades: o tini (init), o parar (kill ao mesmo uid), o git, o gh e o claude não precisam de",
            "    # nenhuma (o sandbox bubblewrap do Claude Code não é usado no contentor)"]
    out += _endurecer()
    out += ["    tmpfs:", "      - /tmp:exec,mode=1777", "    volumes:"]
    out += _dados_runner(False)
    out += ["    networks:", "      - runner"]
    # configurar: root só para o chown das raízes dos volumes, depois larga para OFFICE_UID:OFFICE_GID (D2)
    out += ["  configurar:", f"    image: {imagem_runner}", "    profiles:", "      - configurar",
            # entrypoint só aqui: `run --rm configurar --gh` passa as opções ao configurar (e ignora o CMD ciclo)
            '    entrypoint: ["configurar"]', '    user: "0:0"', "    read_only: true", "    stdin_open: true",
            "    tty: true"]
    out += ["    # o no-new-privileges não impede o setuid() para baixo; o chmod das raízes é feito já sem root (sem FOWNER)"]
    out += _endurecer("CHOWN: fchown das raízes dos volumes para OFFICE_UID:OFFICE_GID",
                      "DAC_OVERRIDE: abrir raízes 0700 de OFFICE_UID (proxy, segredos, casa) ao correr de novo",
                      "SETGID: setgroups([]) e setgid(OFFICE_GID) ao largar o root",
                      "SETUID: setuid(OFFICE_UID) ao largar o root")
    out += ["    environment:", f'      OFFICE_UID: "{UID}"', f'      OFFICE_GID: "{GID}"',
            "    tmpfs:", "      - /tmp:mode=1777", "    volumes:"]
    out += _dados_runner(True)
    out += ["    networks:", "      - runner"]
    out += ["configs:", "  caddyfile:", "    content: |"]
    out += [f"      {linha}".rstrip(" ") if linha else "" for linha in caddyfile.replace("$", "$$").splitlines()]
    out += ["networks:", "  office:", "  runner:", "volumes:"]
    out += [f"  {v}:" for v in [_vol(o) for o, _, _, _ in officecfg.MOUNTS] + ["runner", "runner-home", "config",
                                                                             "segredos", "proxy", "caddy-data",
                                                                             "caddy-config"]]
    return "\n".join(out) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Gera o docker-compose.yml do Squad Office Lite.")
    ap.add_argument("--modo", choices=("vps", "docker"), default="vps")
    ap.add_argument("--config")
    ap.add_argument("--proxy", choices=("caddy", "traefik"))
    ap.add_argument("--rede")
    ap.add_argument("--certresolver")
    ap.add_argument("--dominio")
    ap.add_argument("--repo")
    ap.add_argument("--imagem-office")
    ap.add_argument("--imagem-runner")
    a = ap.parse_args(argv)
    vps = ("config", "proxy", "rede", "certresolver", "dominio", "repo")
    if a.modo == "docker":
        if any(getattr(a, k) is not None for k in vps):
            ap.error("--modo docker não aceita --config, --proxy, --rede, --certresolver, --dominio nem --repo")
        io = IMAGEM_OFFICE if a.imagem_office is None else a.imagem_office
        ir = IMAGEM_RUNNER if a.imagem_runner is None else a.imagem_runner
        for opcao, ref in (("--imagem-office", io), ("--imagem-runner", ir)):
            if not imagem_valida(ref):
                ap.error(f"{opcao} inválida (ex.: ghcr.io/dono/nome:1.2.3 ou …:1.2.3@sha256:<64 hex>; nunca latest)")
        sys.stdout.write(gerar_docker(io, ir))
        return 0
    if a.imagem_office is not None or a.imagem_runner is not None:
        ap.error("--imagem-office e --imagem-runner só no --modo docker")
    if a.config is None:
        ap.error("--config é obrigatório no modo vps")
    a.proxy, a.certresolver = a.proxy or "caddy", a.certresolver or "letsencrypt"
    a.repo = a.repo or str(REPO)
    if a.proxy == "traefik" and not (a.rede and _NOME.match(a.rede)):
        ap.error("--proxy traefik exige --rede com um nome de rede válido")
    if not _NOME.match(a.certresolver):
        ap.error("--certresolver inválido")
    if a.dominio and not _DOMINIO.match(a.dominio):
        ap.error("--dominio inválido (ex.: office.exemplo.com, em minúsculas)")
    try:
        cfg, _ = officecfg.carregar(a.config, estrito=True)
        texto = gerar(cfg, a.config, a.proxy, a.rede, a.certresolver, a.dominio, Path(os.path.abspath(a.repo)))
    except officecfg.ConfigErro as e:
        print(f"erro: {e}", file=sys.stderr)
        return 1
    sys.stdout.write(texto)
    return 0


if __name__ == "__main__":
    sys.exit(main())

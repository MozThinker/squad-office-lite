#!/usr/bin/env python3
"""Gera o docker-compose.yml do Squad Office Lite a partir do office.yaml (stdlib, Python >= 3.10).

    python3 runner/gerar_compose.py --config FICH [--proxy caddy|traefik] [--rede R] [--certresolver C]
                                    [--dominio D] [--repo DIR] > docker-compose.yml

Saída determinística (mesma entrada -> mesmos bytes). Os mounts derivam só das raízes da config (officecfg.MOUNTS);
o compose lê OFFICE_UID, OFFICE_GID, OFFICE_SITE, OFFICE_DOMINIO, OFFICE_AUTH_USER, OFFICE_AUTH_HASH e
OFFICE_PROXY_SEGREDO do .env ao lado (sem o segredo o compose recusa arrancar: o Office nunca fica aberto na rede).
O docker-compose.example.yml do repo é esta saída para o office.example.yaml com HOME=/home/utilizador
(ver tests/test_compose.py).
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


def main(argv=None):
    ap = argparse.ArgumentParser(description="Gera o docker-compose.yml do Squad Office Lite.")
    ap.add_argument("--config", required=True)
    ap.add_argument("--proxy", choices=("caddy", "traefik"), default="caddy")
    ap.add_argument("--rede")
    ap.add_argument("--certresolver", default="letsencrypt")
    ap.add_argument("--dominio")
    ap.add_argument("--repo", default=str(REPO))
    a = ap.parse_args(argv)
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

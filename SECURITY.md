# Segurança

🇬🇧 [English](SECURITY.en.md) | 🇵🇹 **Português**

## Como reportar uma falha

Usa o reporte privado do GitHub: no repositório, separador **Security** → **Report a vulnerability**. Só tu e os
mantenedores vêem o relatório.

**Nunca** reportes uma falha de segurança numa issue pública, num pull request ou num comentário: qualquer pessoa os
lê antes de haver correcção.

## O que incluir

- a versão do Lite (a entrada mais recente do `CHANGELOG.md` da tua instalação) e a do Ubuntu (no modo Docker: o
  sistema do PC e a versão do Docker Desktop);
- o que acontece e o que devia acontecer;
- os passos para o reproduzir, de preferência numa instalação de teste;
- o impacto que vês (o que um atacante consegue ler, escrever ou executar, e a partir de onde).

Não envies credenciais, tokens, o teu `office.yaml`, `.env`, `claude.env` ou `runner.env` reais (nem o conteúdo dos
volumes `segredos` e `proxy` do modo Docker), nem dados de terceiros. Se precisares de mostrar um ficheiro, troca
os segredos por `…`.

## O que podes esperar

- Confirmamos que recebemos o relatório e respondemos assim que pudermos. Não prometemos prazos fixos: somos uma
  equipa pequena.
- Dizemos-te se aceitamos a falha e, se sim, quando contamos ter a correcção.
- Pedimos-te que não divulgues a falha antes de haver uma versão corrigida. Combinamos contigo a data.
- Com o teu acordo, agradecemos-te no `CHANGELOG.md` da versão que a corrige.

## Período de correcções

As correcções de segurança saem na versão mais recente do Lite, com a correcção descrita no `CHANGELOG.md`. Quem
comprou o livro tem-nas garantidas durante **pelo menos 12 meses a contar da data da compra**.

*Security fixes are released on the latest version. Book purchasers are covered for at least 12 months from
purchase.*

## Como actualizar

O mesmo comando da instalação (ver README, «Actualizar»):

```bash
curl -fsSL https://raw.githubusercontent.com/MozThinker/squad-office-lite/v0.1.4/get.sh | bash
```

Descarrega a última versão publicada, confirma o sha256 e corre o `install.sh`. A versão anterior fica em
`~/squad-office-lite.anterior-<data>`; `~/office-lite` (config, credenciais e dados) não é tocada. Para confirmar
também a origem do pacote (opcional), ver README, «Instalação» (`gh attestation verify`).

No modo Docker (no PC), descarrega o `compose.yaml` da versão nova e corre `docker compose pull` e
`docker compose up -d` (ver README, «Actualizar no PC»).

## Âmbito

**É do Lite:** o código deste repositório (`server.py` e módulos, `static/`, `runner/`, `install.sh`, `get.sh`,
`scripts/`, `Dockerfile`, `Dockerfile.runner`, `Caddyfile`, `Caddyfile.docker`, o compose gerado e o `compose.yaml`
da release, com as imagens publicadas no GHCR) e o que o README diz que ele faz: autenticação pelo proxy,
filas de decisões e de lançamento, negações por omissão passadas ao Claude Code, ambiente do `claude` em lista branca,
credenciais fora do contentor do Office.

**Não é do Lite** (reporta a quem os mantém):
- o **Claude Code** (`claude`): o modelo, a aplicação das regras `permissions.deny`, o sandbox e o login;
- o **GitHub CLI** (`gh`) e o próprio GitHub;
- o **Docker** (e o Docker Desktop), o **Caddy**, o **Traefik**, o Ubuntu e as imagens base;
- a configuração da tua VPS (firewall, SSH, utilizadores, DNS) ou do teu PC.

Os limites conhecidos do Lite estão no README, secção «Segurança» (por exemplo: o agente corre com o teu utilizador e
um programa que ele execute lê o que tu lês). Um relatório sobre um limite já documentado é bem-vindo se mostrar um
impacto que lá não está.

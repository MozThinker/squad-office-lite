#!/usr/bin/env bash
# Squad Office Lite: instalador para uma VPS Ubuntu (ver README, «Instalação»).
#
#   ./install.sh [--sem-perguntas] [--proxy caddy|traefik --rede R [--certresolver C]] [--casa DIR] [--palavra-passe] [--credenciais]
#
# Idempotente: office.yaml e .env só são criados se não existirem (--palavra-passe troca só as linhas de auth do .env;
# OFFICE_PROXY_SEGREDO só é gerado se faltar no .env);
# o docker-compose.yml é sempre gerado de novo a partir do office.yaml. Não instala Docker, claude nem gh.
# --sem-perguntas lê do ambiente: OFFICE_DOMINIO (vazio = sem domínio), OFFICE_UTILIZADOR, OFFICE_PASSWORD_FILE,
# OFFICE_CLAUDE_CRED (api|oauth|nenhuma; vazio = nenhuma; oauth só explica como escrever o token, não o lê),
# OFFICE_CLAUDE_CRED_FILE (ficheiro com a chave de API) e, se
# faltarem no git, OFFICE_GIT_NOME e OFFICE_GIT_EMAIL (identidade dos commits da squad; sem elas: aviso).
# Credenciais do claude em <casa>/claude.env (0600, só o runner as lê): a chave de API é pedida só se faltar (no
# claude.env e no runner.env) ou com --credenciais (com --sem-perguntas, também com OFFICE_CLAUDE_CRED não vazio);
# o token da subscrição nunca é pedido (escreve-o o utilizador, ver README). claude.env e runner.env existentes têm de
# ser ficheiros normais do utilizador, sem permissões para o grupo nem outros (chmod 600).
set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SEM_PERGUNTAS=0
PROXY=caddy
REDE=""
CERTRESOLVER=letsencrypt
CASA="$HOME/office-lite"
PALAVRA_PASSE=0
CREDENCIAIS=0
RX_UTILIZADOR='^[a-z][a-z0-9._-]{1,31}$'
RX_DOMINIO='^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$'
RX_NOME='^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$'
RX_CAMINHO='^/[A-Za-z0-9/._+-]*$'
# formato das credenciais do Claude (o runner aplica os mesmos padrões ao ler o claude.env)
RX_CRED_API='^sk-ant-[A-Za-z0-9_-]+$'

erro() { printf 'erro: %s\n' "$*" >&2; exit 1; }
aviso() { printf 'aviso: %s\n' "$*" >&2; }
info() { printf '%s\n' "$*"; }
uso() { sed -n '4p' "$0" | sed 's/^# *//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --sem-perguntas) SEM_PERGUNTAS=1 ;;
    --proxy) PROXY=${2:-}; shift ;;
    --rede) REDE=${2:-}; shift ;;
    --certresolver) CERTRESOLVER=${2:-}; shift ;;
    --casa) CASA=${2:-}; shift ;;
    --palavra-passe) PALAVRA_PASSE=1 ;;
    --credenciais) CREDENCIAIS=1 ;;
    -h|--help) uso; exit 0 ;;
    *) uso >&2; erro "opção desconhecida: $1" ;;
  esac
  shift
done

case "$PROXY" in
  caddy) [ -z "$REDE" ] || erro "--rede só faz sentido com --proxy traefik" ;;
  traefik) [[ "$REDE" =~ $RX_NOME ]] || erro "--proxy traefik exige --rede <rede do Traefik>" ;;
  *) erro "--proxy tem de ser caddy ou traefik" ;;
esac
[[ "$CERTRESOLVER" =~ $RX_NOME ]] || erro "--certresolver inválido"
[ -n "$CASA" ] || erro "--casa precisa de uma pasta"
case "$CASA" in /*) ;; *) CASA="$PWD/$CASA" ;; esac

# ---------------------------------------------------------------- (1) dependências: nada é criado antes disto
falta=0
precisa() {  # precisa <comando> <como instalar>
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'falta: %s. Instala com: %s\n' "$1" "$2" >&2
    falta=1
  fi
}
precisa docker "https://docs.docker.com/engine/install/ubuntu/"
# claude em ~/.local/bin: início
# o instalador oficial do claude põe-no em ~/.local/bin, que pode não estar no PATH de uma sessão SSH; daqui em
# diante (incluindo o PATH da unit do runner) usa-se esse
if ! command -v claude >/dev/null 2>&1 && [ -x "$HOME/.local/bin/claude" ]; then PATH="$HOME/.local/bin:$PATH"; fi
# claude em ~/.local/bin: fim
precisa claude "curl -fsSL https://claude.ai/install.sh | bash   (depois corre 'claude' uma vez para entrar)"
precisa gh "https://github.com/cli/cli/blob/trunk/docs/install_linux.md   (depois 'gh auth login')"
precisa git "sudo apt-get install -y git"
precisa python3 "sudo apt-get install -y python3"
if command -v docker >/dev/null 2>&1; then
  if ! docker info >/dev/null 2>&1; then
    printf 'falta: acesso ao Docker. Junta-te ao grupo docker: sudo usermod -aG docker %s (e volta a entrar)\n' \
      "$(id -un)" >&2
    falta=1
  elif ! docker compose version >/dev/null 2>&1; then
    printf 'falta: docker compose. Instala com: sudo apt-get install -y docker-compose-plugin\n' >&2
    falta=1
  elif [ "$PROXY" = traefik ] && ! docker network inspect "$REDE" >/dev/null 2>&1; then
    printf 'falta: a rede Docker «%s» do Traefik (docker network ls)\n' "$REDE" >&2
    falta=1
  fi
fi
if command -v python3 >/dev/null 2>&1 && ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
  printf 'falta: python3 >= 3.10 (tens %s). Usa Ubuntu 22.04 ou mais recente.\n' "$(python3 -V 2>&1)" >&2
  falta=1
fi
[ "$falta" = 0 ] || exit 1
[ "$(id -u)" != 0 ] || aviso "a correr como root: o Office e as pastas ficam do root (recomendado: um utilizador normal no grupo docker)"
for c in "$REPO" "$CASA"; do
  [[ "$c" =~ $RX_CAMINHO ]] || erro "caminho com caracteres não suportados (usa só letras, números e / . _ + -): $c"
done

# ---------------------------------------------------------------- (2) dados do utilizador (ainda nada criado)
YAML="$CASA/office.yaml"
ENVF="$CASA/.env"
COMPOSE="$CASA/docker-compose.yml"

env_valor() {  # env_valor CHAVE -> valor da linha CHAVE='…' do .env
  sed -n "s/^$1='\(.*\)'\$/\1/p" "$ENVF" | head -n 1
}

perguntar() {  # perguntar <texto> <omissão> -> resposta
  local r
  read -rp "$1${2:+ [$2]}: " r
  printf '%s' "${r:-$2}"
}

DOMINIO=""
UTILIZADOR=""
HASH=""
if [ -f "$ENVF" ]; then
  DOMINIO=$(env_valor OFFICE_DOMINIO)
  UTILIZADOR=$(env_valor OFFICE_AUTH_USER)
  if [ "$SEM_PERGUNTAS" = 1 ] && [ "${OFFICE_DOMINIO:-$DOMINIO}" != "$DOMINIO" ]; then
    aviso "o .env já existe: fica o domínio «${DOMINIO:-sem domínio}» (para mudar, edita $ENVF)"
  fi
else
  if [ "$SEM_PERGUNTAS" = 1 ]; then
    DOMINIO=${OFFICE_DOMINIO:-}
  else
    DOMINIO=$(perguntar "Domínio (vazio = sem domínio, só HTTP)" "")
  fi
  DOMINIO=$(printf '%s' "$DOMINIO" | tr '[:upper:]' '[:lower:]')
fi
[ -z "$DOMINIO" ] || [[ "$DOMINIO" =~ $RX_DOMINIO ]] || erro "domínio inválido: $DOMINIO"
[ "$PROXY" != traefik ] || [ -n "$DOMINIO" ] || erro "--proxy traefik precisa de um domínio"

if [ ! -f "$ENVF" ] || [ "$PALAVRA_PASSE" = 1 ]; then
  if [ "$SEM_PERGUNTAS" = 1 ]; then
    UTILIZADOR=${OFFICE_UTILIZADOR:-$UTILIZADOR}
    [ -n "${OFFICE_PASSWORD_FILE:-}" ] || erro "--sem-perguntas precisa de OFFICE_PASSWORD_FILE"
    if [ ! -f "$OFFICE_PASSWORD_FILE" ] || [ ! -r "$OFFICE_PASSWORD_FILE" ]; then erro "OFFICE_PASSWORD_FILE não se lê"; fi
    PASS=""
    IFS= read -r PASS < "$OFFICE_PASSWORD_FILE" || [ -n "$PASS" ] || erro "OFFICE_PASSWORD_FILE está vazio"
  else
    UTILIZADOR=$(perguntar "Utilizador do Office" "${UTILIZADOR:-$(id -un | tr '[:upper:]' '[:lower:]')}")
    read -rsp "Palavra-passe (12 ou mais caracteres): " PASS; echo
    read -rsp "Repete a palavra-passe: " PASS2; echo
    [ "$PASS" = "$PASS2" ] || erro "as palavras-passe não coincidem"
    unset PASS2
  fi
  [[ "$UTILIZADOR" =~ $RX_UTILIZADOR ]] || erro "utilizador inválido (minúsculas, números, . _ -; 2 a 32 caracteres): $UTILIZADOR"
  [ "${#PASS}" -ge 12 ] || erro "a palavra-passe tem de ter pelo menos 12 caracteres"
  info "A gerar o hash bcrypt da palavra-passe…"
  # a palavra-passe vai só por stdin (printf é builtin: não aparece em argv nem nos logs)
  # sem `| head`: fechar o pipe antes do fim dá «broken pipe» no docker e, com pipefail, um erro intermitente
  saida=$(printf '%s\n' "$PASS" | docker run --rm -i httpd:2.4-alpine htpasswd -niBC 12 "$UTILIZADOR") \
    || erro "não consegui gerar o hash (docker run httpd:2.4-alpine)"
  linha=${saida%%$'\n'*}
  unset saida
  unset PASS
  HASH=${linha#"$UTILIZADOR":}
  [[ "$HASH" =~ ^\$2y\$12\$[./A-Za-z0-9]{53}$ ]] || erro "hash bcrypt inesperado"
fi

# credenciais do claude para o runner (claude.env): um existente com permissões abertas é recusado, nunca corrigido
# credenciais do claude: início
CRED="$CASA/claude.env"
CRED_VIA=""
CRED_CHAVE=""
CRED_VALOR=""
explicar_token() {  # o token da subscrição escreve-o o utilizador: o instalador não o lê, valida nem grava
  info "O instalador não pede nem guarda o token da subscrição. Escreve-o tu em $CASA/claude.env (uma linha"
  info "CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-…) ou em $CASA/runner.env, e corre chmod 600 no ficheiro. Ver README,"
  info "«Credenciais do Claude»."
}
segredo_seguro() {  # segredo_seguro <ficheiro>: se existir, ficheiro normal, do utilizador, sem bits 077; senão erro
  local f=$1 dono modo
  if [ -e "$f" ] || [ -L "$f" ]; then
    if [ -L "$f" ] || [ ! -f "$f" ]; then erro "$f tem de ser um ficheiro normal (não um link): apaga-o e corre de novo"; fi
    read -r dono modo < <(stat -c '%u %a' "$f")
    [ "$dono" = "$(id -u)" ] || erro "$f não é teu: corrige com chown $(id -un) \"$f\" e corre de novo"
    (( (8#$modo & 8#077) == 0 )) \
      || erro "$f tem permissões abertas ($modo): corrige com chmod 600 \"$f\" e corre de novo"
  fi
}
segredo_seguro "$CRED"
# OFFICE_CLAUDE_CRED com --sem-perguntas é um pedido explícito, como --credenciais
CRED_PEDIDO=$CREDENCIAIS
if [ "$SEM_PERGUNTAS" = 1 ] && [ -n "${OFFICE_CLAUDE_CRED:-}" ]; then CRED_PEDIDO=1; fi
# sem claude.env mas com a credencial no runner.env: só se confirma que a chave existe (o valor nunca é lido) e que o
# ficheiro é seguro (como o claude.env)
if [ ! -e "$CRED" ] && [ "$CRED_PEDIDO" = 0 ] \
  && grep -qE '^(ANTHROPIC_API_KEY|CLAUDE_CODE_OAUTH_TOKEN)=.' "$CASA/runner.env" 2>/dev/null; then
  segredo_seguro "$CASA/runner.env"
  info "Credenciais do Claude: uso as do $CASA/runner.env (--credenciais para escolher outras)."
elif [ ! -e "$CRED" ] || [ "$CRED_PEDIDO" = 1 ]; then
  if [ "$SEM_PERGUNTAS" = 1 ]; then
    CRED_VIA=${OFFICE_CLAUDE_CRED:-}
    if [ -z "$CRED_VIA" ]; then
      aviso "sem OFFICE_CLAUDE_CRED: o runner usa o login que o claude já tiver (credenciais: nenhuma)"
      CRED_VIA=nenhuma
    fi
  else
    info "Credenciais do Claude para a squad (ficam em $CRED, 0600, só no host):"
    info "  1) chave de API (recomendada)"
    info "  2) token da subscrição (escreves tu o ficheiro; o instalador não o pede)"
    info "  3) nenhuma (já tenho login no claude)"
    case "$(perguntar "Escolhe" 1)" in
      1) CRED_VIA=api ;;
      2) CRED_VIA=oauth ;;
      3) CRED_VIA=nenhuma ;;
      *) erro "opção inválida: escolhe 1, 2 ou 3" ;;
    esac
  fi
  case "$CRED_VIA" in
    api) CRED_CHAVE=ANTHROPIC_API_KEY ;;
    oauth)
      explicar_token
      if [ "$SEM_PERGUNTAS" = 1 ] && [ -n "${OFFICE_CLAUDE_CRED_FILE:-}" ]; then
        aviso "OFFICE_CLAUDE_CRED_FILE ignorado com oauth"
      fi
      [ ! -e "$CRED" ] || info "$CRED fica como está."
      ;;
    nenhuma) [ ! -e "$CRED" ] || aviso "$CRED fica como está (apaga-o para o runner usar o login do claude)" ;;
    *) erro "OFFICE_CLAUDE_CRED tem de ser api, oauth ou nenhuma" ;;
  esac
  if [ -n "$CRED_CHAVE" ]; then
    # o segredo nunca vai para argv, ecrã nem logs: read -s ou ficheiro
    if [ "$SEM_PERGUNTAS" = 1 ]; then
      [ -n "${OFFICE_CLAUDE_CRED_FILE:-}" ] || erro "OFFICE_CLAUDE_CRED=$CRED_VIA precisa de OFFICE_CLAUDE_CRED_FILE"
      if [ ! -f "$OFFICE_CLAUDE_CRED_FILE" ] || [ ! -r "$OFFICE_CLAUDE_CRED_FILE" ]; then erro "OFFICE_CLAUDE_CRED_FILE não se lê"; fi
      IFS= read -r CRED_VALOR < "$OFFICE_CLAUDE_CRED_FILE" || true
    else
      read -rsp "Chave de API (sk-ant-…; não aparece no ecrã): " CRED_VALOR; echo
    fi
    CRED_VALOR=${CRED_VALOR%$'\r'}
    [ -n "$CRED_VALOR" ] || erro "credencial do Claude vazia"
    [[ "$CRED_VALOR" != sk-ant-oat* ]] \
      || erro "isso é um token da subscrição: não o colas aqui; ver README, «Credenciais do Claude»"
    [[ "$CRED_VALOR" =~ $RX_CRED_API ]] \
      || erro "a chave de API tem de começar por sk-ant- (só letras, números, _ e -)"
  fi
fi
# credenciais do claude: fim

# identidade git dos commits da squad: pede só o que falta; os valores existentes nunca mudam
# identidade git: início
nome_ok() {  # 1 a 100 caracteres, sem caracteres de controlo
  [ "${#1}" -ge 1 ] && [ "${#1}" -le 100 ] && [[ "$1" != *[[:cntrl:]]* ]]
}
email_ok() {  # um @, sem espaços nem caracteres de controlo, até 254 caracteres
  local rx='^[^@[:space:]]+@[^@[:space:]]+$'
  [ "${#1}" -le 254 ] && [[ "$1" != *[[:cntrl:]]* ]] && [[ "$1" =~ $rx ]]
}
# identidade git: fim
pedir_git() {  # pedir_git <pergunta> <validador> <regra> -> valor; até 3 tentativas (em EOF o perguntar dá "")
  local v t
  for t in 1 2 3; do
    v=$(perguntar "$1" "")
    if "$2" "$v"; then printf '%s' "$v"; return 0; fi
    [ "$t" = 3 ] || aviso "$3: tenta de novo"
  done
  erro "$3"
}
REGRA_NOME="nome inválido (1 a 100 caracteres, sem caracteres de controlo)"
REGRA_EMAIL="e-mail inválido (um @, sem espaços, até 254 caracteres)"
GIT_NOME=""
GIT_EMAIL=""
# identidade efectiva fora de qualquer repo (global ou /etc/gitconfig); o que faltar é gravado com --global
GIT_NOME_ACTUAL=$(cd / && git config user.name || true)
GIT_EMAIL_ACTUAL=$(cd / && git config user.email || true)
if [ -z "$GIT_NOME_ACTUAL" ]; then
  if [ "$SEM_PERGUNTAS" = 0 ]; then
    GIT_NOME=$(pedir_git "Nome para os commits da squad (git user.name)" nome_ok "$REGRA_NOME") || exit 1
  elif [ -n "${OFFICE_GIT_NOME:-}" ]; then
    GIT_NOME=$OFFICE_GIT_NOME
    nome_ok "$GIT_NOME" || erro "OFFICE_GIT_NOME inválido (1 a 100 caracteres, sem caracteres de controlo)"
  else
    aviso "a squad não consegue fazer commits: falta o user.name do git (define OFFICE_GIT_NOME ou corre: git config --global user.name \"…\")"
  fi
fi
if [ -z "$GIT_EMAIL_ACTUAL" ]; then
  if [ "$SEM_PERGUNTAS" = 0 ]; then
    GIT_EMAIL=$(pedir_git "E-mail para os commits da squad (git user.email)" email_ok "$REGRA_EMAIL") || exit 1
  elif [ -n "${OFFICE_GIT_EMAIL:-}" ]; then
    GIT_EMAIL=$OFFICE_GIT_EMAIL
    email_ok "$GIT_EMAIL" || erro "OFFICE_GIT_EMAIL inválido (um @, sem espaços, até 254 caracteres)"
  else
    aviso "a squad não consegue fazer commits: falta o user.email do git (define OFFICE_GIT_EMAIL ou corre: git config --global user.email \"…\")"
  fi
fi

# ---------------------------------------------------------------- (3) office.yaml (só se não existir)
umask 077
[ -d "$CASA" ] || install -d -m 0700 "$CASA"
if [ ! -f "$YAML" ]; then
  # entre aspas: o utilizador pode chamar-se true/false e o YAML lia-o como booleano
  sed "s/^  nome: Utilizador\$/  nome: \"$UTILIZADOR\"/" "$REPO/office.example.yaml" > "$YAML.tmp"
  python3 "$REPO/officecfg.py" validar "$YAML.tmp" || { rm -f "$YAML.tmp"; erro "office.yaml gerado inválido"; }
  chmod 0600 "$YAML.tmp"
  mv "$YAML.tmp" "$YAML"
  info "Criado $YAML (edita-o para mudar nome, fuso, projectos e colunas)."
else
  python3 "$REPO/officecfg.py" validar "$YAML" || erro "corrige o $YAML (a mensagem acima diz a linha) e corre de novo"
fi

# ---------------------------------------------------------------- (4) .env (só se não existir; --palavra-passe: só auth)
if [ ! -f "$ENVF" ]; then
  {
    echo "# Squad Office Lite (install.sh). Aspas simples: o compose não interpola os \$ do hash."
    echo "OFFICE_UID='$(id -u)'"
    echo "OFFICE_GID='$(id -g)'"
    echo "OFFICE_SITE='${DOMINIO:-:80}'"
    echo "OFFICE_DOMINIO='$DOMINIO'"
    echo "OFFICE_AUTH_USER='$UTILIZADOR'"
    echo "OFFICE_AUTH_HASH='$HASH'"
  } > "$ENVF.tmp"
  chmod 0600 "$ENVF.tmp"
  mv "$ENVF.tmp" "$ENVF"
  info "Criado $ENVF."
elif [ "$PALAVRA_PASSE" = 1 ]; then
  {
    grep -vE '^OFFICE_AUTH_(USER|HASH)=' "$ENVF" || true
    echo "OFFICE_AUTH_USER='$UTILIZADOR'"
    echo "OFFICE_AUTH_HASH='$HASH'"
  } > "$ENVF.tmp"
  chmod 0600 "$ENVF.tmp"
  mv "$ENVF.tmp" "$ENVF"
  info "Palavra-passe actualizada em $ENVF."
fi
# segredo que o proxy injecta (X-Office-Segredo) e o Office exige (ADR-006): só se faltar; nunca em argv nem nos logs
if ! grep -q '^OFFICE_PROXY_SEGREDO=' "$ENVF"; then
  SEGREDO=$(od -An -tx1 -N32 /dev/urandom | tr -d ' \n')
  [[ "$SEGREDO" =~ ^[0-9a-f]{64}$ ]] || erro "não consegui gerar o segredo do proxy (/dev/urandom)"
  {
    cat "$ENVF"
    echo "OFFICE_PROXY_SEGREDO='$SEGREDO'"
  } > "$ENVF.tmp"
  unset SEGREDO
  chmod 0600 "$ENVF.tmp"
  mv "$ENVF.tmp" "$ENVF"
  info "Segredo do proxy gravado em $ENVF."
fi

# credenciais do claude: tmp 0600 (umask 077) e mv atómico; printf é builtin (o segredo não aparece em argv)
if [ -n "$CRED_VALOR" ]; then
  rm -f "$CRED.tmp"
  ( umask 077; printf '%s=%s\n' "$CRED_CHAVE" "$CRED_VALOR" > "$CRED.tmp" )
  unset CRED_VALOR
  chmod 0600 "$CRED.tmp"
  mv -f "$CRED.tmp" "$CRED"
  info "Credenciais do Claude gravadas em $CRED (só o runner as lê)."
fi

# ---------------------------------------------------------------- (5) identidade git, pastas e repo de exemplo
# só argv, sem shell intermédio, e nunca ecoada; o "--" impede que um valor começado por - seja lido como opção
[ -z "$GIT_NOME" ] || git config --global -- user.name "$GIT_NOME" || erro "não consegui gravar o user.name do git"
[ -z "$GIT_EMAIL" ] || git config --global -- user.email "$GIT_EMAIL" || erro "não consegui gravar o user.email do git"
while read -r modo pasta; do
  [ -d "$pasta" ] || install -d -m "$modo" "$pasta"
done < <(python3 "$REPO/officecfg.py" pastas "$YAML")
PROJECTOS=$(python3 "$REPO/officecfg.py" raiz "$YAML" projectos)
if [ ! -e "$PROJECTOS/exemplo" ]; then
  install -d -m 0750 "$PROJECTOS/exemplo"
  (
    cd "$PROJECTOS/exemplo"
    git init -q -b main
    printf '# Exemplo\n\nProjecto de exemplo do Squad Office Lite: lança aqui a primeira tarefa.\n' > README.md
    printf '.worktrees/\n' >> .git/info/exclude
    git add README.md
    git -c user.name="Squad Office" -c user.email="office@localhost" commit -q -m "Primeiro commit"
  )
  info "Criado o repositório de exemplo em $PROJECTOS/exemplo."
fi

# ---------------------------------------------------------------- (6) compose gerado
gerar=(python3 "$REPO/runner/gerar_compose.py" --config "$YAML" --proxy "$PROXY" --repo "$REPO")
[ -z "$DOMINIO" ] || gerar+=(--dominio "$DOMINIO")
[ "$PROXY" != traefik ] || gerar+=(--rede "$REDE" --certresolver "$CERTRESOLVER")
"${gerar[@]}" > "$COMPOSE.tmp" || { rm -f "$COMPOSE.tmp"; erro "não consegui gerar o docker-compose.yml"; }
mv "$COMPOSE.tmp" "$COMPOSE"

# ---------------------------------------------------------------- (7) arrancar
# o compose dá prioridade ao ambiente sobre o .env: só o .env conta
unset OFFICE_UID OFFICE_GID OFFICE_SITE OFFICE_DOMINIO OFFICE_AUTH_USER OFFICE_AUTH_HASH OFFICE_PROXY_SEGREDO
info "A construir e arrancar o Office (a 1.ª vez demora uns minutos)…"
docker compose -f "$COMPOSE" up -d --build \
  || erro "o docker compose falhou (porta 80/443 ocupada? vê: sudo ss -ltnp 'sport = :80')"

# ---------------------------------------------------------------- (8) runner (timer systemd de utilizador)
UNITS="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
CAMINHO_EXEC=""
for c in claude gh git python3 docker; do
  d=$(dirname "$(command -v "$c")")
  case ":$CAMINHO_EXEC:" in *":$d:"*) ;; *) CAMINHO_EXEC="${CAMINHO_EXEC:+$CAMINHO_EXEC:}$d" ;; esac
done
CAMINHO_EXEC="$CAMINHO_EXEC:/usr/local/bin:/usr/bin:/bin"
runner_ok=1
if ! [[ "$CAMINHO_EXEC" =~ ^[A-Za-z0-9/._+:-]+$ ]]; then
  aviso "PATH com caracteres não suportados: runner não instalado"
  runner_ok=0
fi
for u in office-runner.service office-runner.timer; do
  [ "$runner_ok" = 1 ] || break
  [ -f "$REPO/runner/$u" ] || { aviso "falta runner/$u: runner não instalado"; runner_ok=0; break; }
  conteudo=$(<"$REPO/runner/$u")
  conteudo=${conteudo//@REPO@/"$REPO"}
  conteudo=${conteudo//@CONFIG@/"$YAML"}
  conteudo=${conteudo//@PATH@/"$CAMINHO_EXEC"}
  conteudo=${conteudo//@CASA@/"$CASA"}
  ( umask 022; install -d "$UNITS" )
  if [ ! -f "$UNITS/$u" ] || [ "$(<"$UNITS/$u")" != "$conteudo" ]; then
    ( umask 022; printf '%s\n' "$conteudo" > "$UNITS/$u" )
  fi
done
if [ "$runner_ok" = 1 ]; then
  if systemctl --user daemon-reload && systemctl --user enable --now office-runner.timer; then
    info "Runner activo (office-runner.timer, de minuto a minuto)."
  else
    aviso "não consegui activar o timer; corre: systemctl --user enable --now office-runner.timer"
  fi
  EU=$(id -un)
  if [ "$(loginctl show-user "$EU" -p Linger --value 2>/dev/null || true)" != yes ]; then
    loginctl enable-linger "$EU" 2>/dev/null \
      || aviso "sem 'linger' o runner pára quando sais da sessão; corre: sudo loginctl enable-linger $EU"
  fi
fi

# ---------------------------------------------------------------- (9) endereço
if [ -n "$DOMINIO" ]; then
  info "Pronto: https://$DOMINIO (utilizador: ${UTILIZADOR:-o do .env})"
else
  IP=$(hostname -I 2>/dev/null | awk '{print $1}')
  info "Pronto: http://${IP:-localhost} (utilizador: ${UTILIZADOR:-o do .env})"
  aviso "sem HTTPS: Web Push e palavra-passe ficam expostos, usa um domínio"
fi

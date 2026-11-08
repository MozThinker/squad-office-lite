#!/usr/bin/env bash
# Squad Office Lite: instala ou actualiza numa VPS Ubuntu com um comando (ver README, «Instalação»).
#
#   curl -fsSL https://raw.githubusercontent.com/MozThinker/squad-office-lite/v0.1.3/get.sh | bash
#   curl -fsSL https://raw.githubusercontent.com/MozThinker/squad-office-lite/v0.1.3/get.sh | bash -s -- [--versao X.Y.Z] [opções do install.sh]
#
# Descarrega o SHA256SUMS e o squad-office-lite-X.Y.Z.tar.gz das releases do GitHub (a última, ou a de --versao),
# confirma o sha256, extrai para ~/squad-office-lite (a versão anterior fica em ~/squad-office-lite.anterior-<data>)
# e corre o install.sh com as outras opções. Os teus dados (a casa do install.sh) não são tocados.
# Verificação opcional da origem (atestação do GitHub; precisa do gh), com o tarball descarregado à mão:
#   gh attestation verify squad-office-lite-0.1.3.tar.gz --repo MozThinker/squad-office-lite
# Tudo corre dentro de main, chamada na última linha: um download interrompido não executa nada.
set -euo pipefail

DONO=${OFFICE_LITE_DONO:-MozThinker}
REPO=${OFFICE_LITE_REPO:-squad-office-lite}
BASE=${OFFICE_LITE_URL:-https://github.com/$DONO/$REPO/releases}
OS_RELEASE=${OFFICE_LITE_OS_RELEASE:-/etc/os-release}
DESTINO="$HOME/squad-office-lite"
RX_VERSAO='^[0-9]+\.[0-9]+\.[0-9]+$'
RX_SUMS='^([0-9a-f]{64})  (squad-office-lite-([0-9]+\.[0-9]+\.[0-9]+)\.tar\.gz)$'
TMP=""

erro() { printf 'erro: %s\n' "$*" >&2; exit 1; }
aviso() { printf 'aviso: %s\n' "$*" >&2; }
info() { printf '%s\n' "$*"; }

so_root() {  # os 4 comandos de «VPS nova só com root» (README), para criar o utilizador u
  printf '%s\n' "VPS nova só com root: cria um utilizador normal (troca u pelo nome que quiseres) e entra com ele:" \
    "adduser u" \
    "usermod -aG sudo u" \
    "install -d -m 700 -o u -g u /home/u/.ssh" \
    "install -m 600 -o u -g u /root/.ssh/authorized_keys /home/u/.ssh/authorized_keys" >&2
}

verificar() {  # verificar <sem_perguntas>: nada é criado em ~ antes de tudo isto passar
  local arch p
  if [ "$(id -u)" = 0 ]; then
    so_root
    erro "não corras como root; usa um utilizador com sudo (README, «VPS nova só com root»)"
  fi
  grep -qxE 'ID="?ubuntu"?' "$OS_RELEASE" 2>/dev/null || erro "este instalador é para Ubuntu"
  arch=$(uname -m)
  case "$arch" in
    x86_64) ;;
    aarch64) aviso "arm64 não testado" ;;
    *) erro "processador não suportado: $arch" ;;
  esac
  for p in sudo curl tar sha256sum; do
    command -v "$p" >/dev/null 2>&1 || erro "falta $p"
  done
  # com curl | bash o stdin é o próprio script: as perguntas do install.sh vêm do terminal
  if [ "$1" = 0 ] && ! ( : </dev/tty ) 2>/dev/null; then
    erro "sem terminal; usa --sem-perguntas"
  fi
}

descarregar() {  # descarregar <url> <ficheiro>: só https (file:// só para testes, com OFFICE_LITE_URL)
  local proto=(--proto "=https")
  case "$1" in file://*) proto=() ;; esac
  curl -fsSL "${proto[@]}" --proto-redir =https -o "$2" "$1" || erro "não consegui descarregar $1"
}

main() {
  local versao="" sem_perguntas=0 args=() url linhas esperado obtido nome v topo anterior
  while [ $# -gt 0 ]; do
    case "$1" in
      --versao)
        versao=${2:-}
        [[ "$versao" =~ $RX_VERSAO ]] || erro "versão inválida (usa X.Y.Z, ex.: --versao 0.1.3)"
        shift ;;
      --sem-perguntas) sem_perguntas=1; args+=("$1") ;;
      *) args+=("$1") ;;
    esac
    shift
  done
  verificar "$sem_perguntas"

  if [ -n "$versao" ]; then url="$BASE/download/v$versao"; else url="$BASE/latest/download"; fi
  # pasta temporária no mesmo disco que o destino (o mv final não copia); apagada à saída
  TMP=$(mktemp -d "$HOME/.squad-office-lite.XXXXXX")
  trap 'rm -rf "$TMP"' EXIT

  info "A descarregar $url/SHA256SUMS…"
  descarregar "$url/SHA256SUMS" "$TMP/SHA256SUMS"
  mapfile -t linhas < "$TMP/SHA256SUMS"
  if [ "${#linhas[@]}" != 1 ] || ! [[ "${linhas[0]}" =~ $RX_SUMS ]]; then
    erro "SHA256SUMS inesperado em $url (uma linha: <sha256>  squad-office-lite-X.Y.Z.tar.gz)"
  fi
  esperado=${BASH_REMATCH[1]}
  nome=${BASH_REMATCH[2]}
  v=${BASH_REMATCH[3]}
  [ -z "$versao" ] || [ "$v" = "$versao" ] || erro "o SHA256SUMS de $url é da versão $v, não da $versao"

  info "A descarregar $nome…"
  descarregar "$url/$nome" "$TMP/$nome"
  obtido=$(sha256sum "$TMP/$nome")
  [ "${obtido%% *}" = "$esperado" ] || erro "o sha256 não confere; nada foi instalado"

  mkdir "$TMP/x"
  tar -xzf "$TMP/$nome" -C "$TMP/x" --no-same-owner || erro "não consegui extrair $nome; nada foi instalado"
  topo="$TMP/x/squad-office-lite-$v"
  [ -f "$topo/install.sh" ] || erro "$nome não tem squad-office-lite-$v/install.sh; nada foi instalado"

  if [ -e "$DESTINO" ] || [ -L "$DESTINO" ]; then
    anterior="$DESTINO.anterior-$(date +%Y%m%d-%H%M%S)"
    [ ! -e "$anterior" ] || erro "$anterior já existe: espera um segundo e corre de novo"
    mv "$DESTINO" "$anterior"
    if ! mv "$topo" "$DESTINO"; then
      mv "$anterior" "$DESTINO"
      erro "não consegui instalar em $DESTINO; a versão anterior foi reposta"
    fi
    info "Versão anterior guardada em $anterior."
  else
    mv "$topo" "$DESTINO"
  fi
  # o exec substitui este processo: o trap já não corria
  rm -rf "$TMP"
  trap - EXIT

  info "Squad Office Lite $v em $DESTINO. A correr o install.sh…"
  if [ "$sem_perguntas" = 1 ]; then
    exec bash "$DESTINO/install.sh" "${args[@]}" </dev/null
  fi
  exec bash "$DESTINO/install.sh" "${args[@]}" </dev/tty
}

main "$@"

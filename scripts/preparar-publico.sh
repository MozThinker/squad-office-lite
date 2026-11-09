#!/usr/bin/env bash
# Árvore pública do Squad Office Lite: exporta o HEAD (sem .git, sem docs/, sem o .github/dependabot.yml, que só
# serve o repositório de desenvolvimento) para DESTINO e falha se lá encontrar nomes internos, a lista deles (mesmo na
# forma nom[e]) ou caminhos de pastas pessoais (/home/<x>, /Users/<x>) que não sejam os exemplos (u, utilizador) nem
# a casa do utilizador office das imagens do modo Docker. Procura também nos ficheiros binários. Não publica nada.
#
#   scripts/preparar-publico.sh [--publica] DESTINO      (DESTINO não pode existir ou tem de estar vazio)
#
# A lista de nomes não está neste ficheiro: vem do git grep do passo «Genérico» do .github/workflows/ci.yml do HEAD
# (a linha «nada -niE '…' -- . ':!CHANGELOG.md' …»). Na cópia pública do ci.yml essa linha sai e os nomes saem dos
# outros padrões; o resto do passo fica.
# --publica: para correr na árvore pública (que já não tem a lista): exige que o ci.yml não a tenha, salta a
# verificação de nomes e mantém a de caminhos pessoais. Sem a flag, a falta da lista continua a ser erro.
set -euo pipefail

CI=.github/workflows/ci.yml
RX_LINHA_NOMES="^[[:space:]]*nada -niE '([^']+)' -- \\. ':!CHANGELOG\\.md'"
CASAS='/(hom[e]|User[s])/[[:alnum:]._-]+'
CASAS_EXEMPLO=':/hom[e]/(u|utilizador|office)$'

erro() { printf 'erro: %s\n' "$*" >&2; exit 1; }

# A linha «Copyright (c) AAAA Titular» do LICENSE é pública por natureza (o titular é decisão do dono do repositório):
# tira dos achados só essa linha do LICENSE da raiz; qualquer outra linha, ou outro ficheiro, continua a falhar.
copyright_fora() {
  local destino=$1 achado n
  while IFS= read -r achado; do
    if [[ "$achado" =~ ^LICENSE:([0-9]+)$ ]]; then
      n=${BASH_REMATCH[1]}
      if sed -n "${n}p" "$destino/LICENSE" | grep -qE '^Copyright \(c\) [0-9]{4}(-[0-9]{4})? [^/<>]+$'; then
        continue
      fi
    fi
    printf '%s\n' "$achado"
  done
}

main() {
  local publica=0
  if [ "${1:-}" = --publica ]; then publica=1; shift; fi
  [ $# -eq 1 ] && [ -n "$1" ] || erro "uso: scripts/preparar-publico.sh [--publica] DESTINO"
  local destino=$1 raiz achados nomes linha texto alt
  local -a alts linhas
  raiz=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
  if [ -e "$destino" ] || [ -L "$destino" ]; then
    if [ -L "$destino" ] || [ ! -d "$destino" ] || [ -n "$(ls -A "$destino")" ]; then
      erro "$destino já existe e não é uma pasta vazia"
    fi
  else
    mkdir -p "$destino"
  fi
  git -C "$raiz" archive --format=tar HEAD -- . ':(exclude)docs' ':(exclude).github/ci.proposto.yml' \
    ':(exclude).github/dependabot.yml' \
    | tar -x -C "$destino"

  [ -f "$destino/$CI" ] || erro "falta o $CI (é dele que vem a lista de nomes internos)"
  nomes=""
  texto=""
  mapfile -t linhas < "$destino/$CI"
  for linha in "${linhas[@]}"; do
    if [[ "$linha" =~ $RX_LINHA_NOMES ]]; then
      [ -z "$nomes" ] || erro "mais de uma lista de nomes internos no $CI"
      nomes=${BASH_REMATCH[1]}
    else
      texto+="$linha"$'\n'
    fi
  done
  if [ "$publica" = 1 ]; then
    [ -z "$nomes" ] || erro "--publica só na árvore pública"
  else
    [ -n "$nomes" ] || erro "não encontrei a lista de nomes internos no $CI"
    IFS='|' read -ra alts <<< "$nomes"
    for alt in "${alts[@]}"; do
      texto=${texto//"$alt|"/}
      texto=${texto//"|$alt"/}
    done
    printf '%s' "$texto" > "$destino/$CI"
  fi

  # ficheiro:linha de cada ocorrência (grep -o: um /home/u/ na mesma linha não esconde outro caminho; -a: binários)
  achados=$(cd "$destino" && {
    if [ "$publica" = 0 ]; then
      grep -rnaoiE "$nomes" . || true
      grep -rnaoiF -f <(printf '%s\n' "${alts[@]}") . || true
    fi
    grep -rnaoE "$CASAS" . | grep -vE "$CASAS_EXEMPLO" || true
  } | cut -d: -f1,2 | sed 's|^\./||' | sort -u | copyright_fora "$destino")
  if [ -n "$achados" ]; then
    printf '%s\n' "$achados" >&2
    erro "nomes internos na árvore pública"
  fi
  printf 'Árvore pública em %s (HEAD %s).\n' "$destino" "$(git -C "$raiz" rev-parse --short HEAD)"
}

main "$@"

#!/usr/bin/env bash
# Ficheiros de uma versão do Squad Office Lite em dist/: squad-office-lite-X.Y.Z.tar.gz (pasta de topo
# squad-office-lite-X.Y.Z/, gerada por scripts/preparar-publico.sh a partir do HEAD) e SHA256SUMS (formato do
# sha256sum, uma linha), os dois ficheiros que o get.sh descarrega. Não publica nada: a release no GitHub é manual.
#
#   scripts/release.sh [--publica] X.Y.Z      (exige «## Lite X.Y.Z» no CHANGELOG.md do HEAD)
#
# --publica: na árvore pública (passa-o ao preparar-publico.sh; ver lá).
set -euo pipefail

TMP=""

erro() { printf 'erro: %s\n' "$*" >&2; exit 1; }

main() {
  local -a publica=()
  if [ "${1:-}" = --publica ]; then publica=(--publica); shift; fi
  [ $# -eq 1 ] || erro "uso: scripts/release.sh [--publica] X.Y.Z"
  local versao=$1 aqui raiz nome
  [[ "$versao" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || erro "versão inválida (usa X.Y.Z)"
  aqui=$(cd "$(dirname "$0")" && pwd)
  raiz=$(git -C "$aqui" rev-parse --show-toplevel)
  git -C "$raiz" show HEAD:CHANGELOG.md | grep -E "^## Lite ${versao//./\\.}( |$)" >/dev/null \
    || erro "falta a entrada $versao no CHANGELOG"
  nome="squad-office-lite-$versao"
  TMP=$(mktemp -d)
  trap 'rm -rf "$TMP"' EXIT
  "$aqui/preparar-publico.sh" "${publica[@]}" "$TMP/$nome"
  mkdir -p "$raiz/dist"
  tar --sort=name --owner=0 --group=0 --numeric-owner -czf "$raiz/dist/$nome.tar.gz" -C "$TMP" "$nome"
  (cd "$raiz/dist" && sha256sum "$nome.tar.gz" > SHA256SUMS)
  printf 'Versão %s em %s/dist (%s.tar.gz e SHA256SUMS). Nada foi publicado.\n' "$versao" "$raiz" "$nome"
}

main "$@"

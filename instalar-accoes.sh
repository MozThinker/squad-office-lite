#!/usr/bin/env bash
# Squad Office Lite, modo VPS: instala o serviço accoes (acções aprovadas, ADR-021). Corre com sudo, à parte do
# install.sh (que nunca usa sudo).
#
#   sudo ./instalar-accoes.sh --dados <pasta de dados do office.yaml> [--verificar]
#
# Cria o utilizador de sistema office-accoes (sem casa nem shell), copia accoes.py e servico_accoes/ para
# /opt/office-accoes (do root), cria /srv/office-accoes (0750, segredos 0700, accoes.json 0644) e
# <dados>/accoes-entrada e <dados>/accoes-saude (office-accoes, 0755), dá ao office-accoes leitura (ACL) em
# <dados>/decisoes e <dados>/agentes e passagem (--x) nas pastas acima, e instala o timer systemd de sistema office-accoes.timer (uma
# passagem por minuto). O accoes.env (credenciais) fica em /srv/office-accoes/segredos, só legível pelo office-accoes.
# Idempotente: o que já existe fica (o accoes.json e o accoes.env nunca são reescritos); as units são sempre
# reescritas. --verificar só mostra o que faria (não precisa de root e não muda nada).
set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
UTILIZADOR=office-accoes
OPT=/opt/office-accoes
SRV=/srv/office-accoes
UNITS=/etc/systemd/system
RX_CAMINHO='^/[A-Za-z0-9/._+-]*$'
DADOS=""
VERIFICAR=0

erro() { printf 'erro: %s\n' "$*" >&2; exit 1; }
info() { printf '%s\n' "$*"; }
uso() { sed -n '5p' "$0" | sed 's/^# *//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --dados) DADOS=${2:-}; shift ;;
    --verificar) VERIFICAR=1 ;;
    -h|--help) uso; exit 0 ;;
    *) uso >&2; erro "opção desconhecida: $1" ;;
  esac
  shift
done

# faz <comando…>: mostra-o e, fora do --verificar, corre-o
faz() {
  printf '+ %s\n' "$*"
  if [ "$VERIFICAR" = 0 ]; then "$@"; fi
}

# escreve <destino> <modo> <dono>: o stdin vai para o destino (tmp na mesma pasta + mv)
escreve() {
  local destino=$1 modo=$2 dono=$3 tmp
  printf '+ escrever %s (%s, %s)\n' "$destino" "$modo" "$dono"
  if [ "$VERIFICAR" = 1 ]; then cat > /dev/null; return; fi
  tmp=$(mktemp "$destino.XXXXXX")
  cat > "$tmp"
  chown "$dono:$dono" "$tmp"
  chmod "$modo" "$tmp"
  mv -f "$tmp" "$destino"
}

[ -n "$DADOS" ] || { uso >&2; erro "falta --dados <pasta> (a raiz dados do office.yaml, ex.: ~/office-lite/dados)"; }
[[ "$DADOS" =~ $RX_CAMINHO ]] || erro "--dados tem de ser um caminho absoluto só com letras, números e / . _ + -"
DADOS=${DADOS%/}
pasta() { [ -d "$1" ] && [ ! -L "$1" ]; }
pasta "$DADOS" || erro "$DADOS não é uma pasta (corre primeiro o install.sh)"
for p in decisoes agentes; do
  pasta "$DADOS/$p" || erro "falta a pasta $DADOS/$p (corre primeiro o install.sh)"
done
for p in accoes-entrada accoes-saude; do
  [ ! -L "$DADOS/$p" ] || erro "$DADOS/$p é um symlink: apaga-o e corre de novo"
done
if [ ! -f "$REPO/accoes.py" ] || [ ! -f "$REPO/servico_accoes/servico.py" ]; then
  erro "corre-o a partir do repositório do Lite (faltam accoes.py ou servico_accoes/servico.py)"
fi
command -v setfacl > /dev/null || erro "falta o setfacl: sudo apt-get install -y acl"
command -v systemctl > /dev/null || erro "falta o systemd (systemctl)"
PY=$(command -v python3) || erro "falta o python3: sudo apt-get install -y python3"
"$PY" -c 'import sys; sys.exit(sys.version_info < (3, 10))' || erro "o python3 tem de ser >= 3.10"
if [ "$VERIFICAR" = 0 ] && [ "$(id -u)" != 0 ]; then
  erro "corre com sudo (ou --verificar para ver o que faria)"
fi
[ "$VERIFICAR" = 0 ] || info "--verificar: nada é mudado; isto é o que seria feito"

# 1. utilizador de sistema, sem casa nem shell
if id "$UTILIZADOR" > /dev/null 2>&1; then
  info "utilizador $UTILIZADOR já existe"
else
  faz useradd --system --no-create-home --shell /usr/sbin/nologin "$UTILIZADOR"
fi

# 2. código em /opt (do root: o serviço não o pode mudar)
faz install -d -o root -g root -m 0755 "$OPT" "$OPT/servico_accoes"
faz install -o root -g root -m 0644 "$REPO/accoes.py" "$OPT/accoes.py"
for f in "$REPO"/servico_accoes/*.py; do
  faz install -o root -g root -m 0644 "$f" "$OPT/servico_accoes/$(basename "$f")"
done

# 3. configuração, estado e credenciais do serviço
faz install -d -o "$UTILIZADOR" -g "$UTILIZADOR" -m 0750 "$SRV"
faz install -d -o "$UTILIZADOR" -g "$UTILIZADOR" -m 0700 "$SRV/segredos"
if [ -e "$SRV/accoes.json" ]; then
  info "$SRV/accoes.json já existe: fica como está"
else
  printf '{"v": 1, "squads": {}}\n' | escreve "$SRV/accoes.json" 0644 "$UTILIZADOR"
fi

# 4. mensagens e vista que o serviço escreve e o runner lê; saude.json que o office lê (ADR-022)
faz install -d -o "$UTILIZADOR" -g "$UTILIZADOR" -m 0755 "$DADOS/accoes-entrada"
faz install -d -o "$UTILIZADOR" -g "$UTILIZADOR" -m 0755 "$DADOS/accoes-saude"

# 5. leitura de decisoes/ e agentes/ (também o que for criado depois: ACL por omissão), sem seguir symlinks
for p in decisoes agentes; do
  faz setfacl -R -P -m "u:$UTILIZADOR:rX" "$DADOS/$p"
  faz setfacl -R -P -d -m "u:$UTILIZADOR:rX" "$DADOS/$p"
done
d=$DADOS
while [ "$d" != / ]; do
  faz setfacl -m "u:$UTILIZADOR:--x" "$d"
  d=$(dirname "$d")
done

# 6. timer systemd de sistema: uma passagem (colector e executor) por minuto
escreve "$UNITS/office-accoes.service" 0644 root <<EOF
# Squad Office Lite: serviço accoes (acções aprovadas, ADR-021). Gerado pelo instalar-accoes.sh.
[Unit]
Description=Squad Office Lite: acções aprovadas (colector e executor)

[Service]
Type=oneshot
User=$UTILIZADOR
Group=$UTILIZADOR
Environment=ACCOES_PERFIL=lite
Environment=ACCOES_DIR=$SRV
Environment=ACCOES_SEGREDOS=$SRV/segredos
Environment=ACCOES_ENTRADA=$DADOS/accoes-entrada
Environment=ACCOES_DECISOES=$DADOS/decisoes
Environment=ACCOES_AGENTES=$DADOS/agentes
Environment=ACCOES_SAUDE=$DADOS/accoes-saude
ExecStart=$PY -I -u $OPT/servico_accoes/servico.py passagem
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=$SRV $DADOS/accoes-entrada $DADOS/accoes-saude
PrivateTmp=yes
EOF
escreve "$UNITS/office-accoes.timer" 0644 root <<'EOF'
# Uma passagem do serviço accoes por minuto. O lock em /srv/office-accoes/.lock impede duas ao mesmo tempo.
[Unit]
Description=Squad Office Lite: acções aprovadas, de minuto a minuto

[Timer]
OnCalendar=*-*-* *:*:00
Persistent=true

[Install]
WantedBy=timers.target
EOF
faz systemctl daemon-reload
faz systemctl enable --now office-accoes.timer

# o mesmo ambiente da unit, para os subcomandos à mão
CMD="sudo -u $UTILIZADOR env ACCOES_PERFIL=lite ACCOES_DIR=$SRV ACCOES_SEGREDOS=$SRV/segredos"
CMD="$CMD ACCOES_ENTRADA=$DADOS/accoes-entrada ACCOES_DECISOES=$DADOS/decisoes ACCOES_AGENTES=$DADOS/agentes"
CMD="$CMD ACCOES_SAUDE=$DADOS/accoes-saude"
CMD="$CMD $PY -I -u $OPT/servico_accoes/servico.py"
info "pronto. Configuração: $SRV/accoes.json (ou: … escrever-config < accoes.json)."
info "Credenciais (pelo teclado, sem eco) e verificação das ligações:"
info "  $CMD escrever-segredos"
info "  $CMD verificar"

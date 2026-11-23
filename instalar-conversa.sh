#!/usr/bin/env bash
# Squad Office Lite, modo VPS: instala a Conversa (ADR-023, revisão S2/S5): o guardião e a ponte. Corre com sudo.
#
#   sudo ./instalar-conversa.sh --office <utilizador> [--dono <u>] [--tls externo|caddy] [--grupo-socket <grupo>] [--pasta /srv/office-conversa] [--pasta-trabalho <dir>] [--permissoes cartao|espelho] [--lancadores lancar:continuar:diretor:squad] [--fuso <IANA>] [--voz desligada|local|externa] [--voz-idioma pt] [--claude <caminho>] [--python <caminho>] [--novo-codigo] [--revogar] [--verificar]
#
# Corre-o como root de uma cópia do Lite só do root: a release extraída depois de conferir o SHA256SUMS, por exemplo
# em /usr/local/src/office-lite-<versão> (ver CONVERSA.md). Recusa uma cópia (ou uma pasta acima dela) em que o Office
# ou o dono consigam escrever: o código vai daqui para /opt e o SHA256SUMS confirmaria o que lá tivessem posto.
#
# --python é o Python >= 3.11 do guardião e da ponte (omissão /usr/bin/python3; no Ubuntu 22.04: sudo apt-get install
# -y python3.11 e --python /usr/bin/python3.11). --office é o utilizador que corre o Office (o do install.sh); --dono
# (omissão: quem chamou o sudo) é o utilizador cujo Claude Code a Conversa usa (tem de ter feito `claude` → /login).
# --tls (o mesmo do `conversa.tls` do office.yaml) diz onde termina o TLS da Conversa: externo (omissão) num túnel ou
# proxy teu fora da fabrica, ligado ao socket do guardião; caddy no Caddy do Office (o TLS termina na fabrica: quem a
# controle pode servir JS na origem da Conversa). --grupo-socket é quem chega ao socket: com externo é obrigatório (o
# grupo do teu túnel, ex.: cloudflared) e o --office não pode pertencer-lhe; com caddy, a omissão é o grupo primário
# do --office (o do Caddy rootless).
#
# Cria o utilizador de sistema office-conversa (o guardião: tem a chave HMAC em /var/lib/office-conversa, que nem o
# dono, nem o motor, nem o Office lêem) e as pastas: <pasta>/verificados (do guardião; o dono lê por ACL),
# <pasta>/conversas (do dono; o guardião lê por ACL, o Office deixa de ler), <pasta>/publico (o Office lê o
# publico.json) e <pasta>/sock (o socket do guardião, 0660, grupo --grupo-socket). Copia o código e a UI para
# /opt/office-conversa (do root) com um SHA256SUMS que o guardião confere no arranque, gera o código da Conversa (na
# 1.ª vez, ao migrar da chave antiga ou com --novo-codigo; mostrado uma vez, nunca gravado; é de uso único e regista
# um só browser), prepara a voz pedida e instala a unit de sistema office-conversa-guardiao.service e a unit de
# utilizador office-conversa.service no dono (com linger). --revogar apaga os browsers registados (chaves.json do
# guardião). Idempotente: o código e o voz.env nunca são reescritos sem pedido; as units são sempre reescritas e
# reiniciadas. --verificar só mostra o que faria (não precisa de root e não muda nada).
set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
OPT=/opt/office-conversa
GUARDIAO=office-conversa
GEST=/var/lib/office-conversa
UNIT_SIS=/etc/systemd/system/office-conversa-guardiao.service
PY_PADRAO=/usr/bin/python3
PY=$PY_PADRAO
RX_CAMINHO='^/[A-Za-z0-9/._+-]*$'
RX_UTILIZADOR='^[a-z_][a-z0-9_-]*[$]?$'
RX_LANCADORES='^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*(:[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*)*$'
RX_FUSO='^[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+)*$'
RX_IDIOMA='^[a-z]{2}$'
RX_WEB='^(fonts/)?[a-z0-9_-]+\.(html|js|css|woff2|svg|png)$'
OFFICE=""
DONO=${SUDO_USER:-}
PASTA=/srv/office-conversa
TRABALHO=""
PERMISSOES=cartao
LANCADORES=lancar:continuar:diretor:squad
FUSO=""
VOZ=desligada
VOZ_IDIOMA=pt
TLS=externo
GRUPO=""
CLAUDE=""
NOVO_CODIGO=0
REVOGAR=0
VERIFICAR=0
AVISO_CADDY="com --tls caddy o TLS da Conversa termina no Caddy da fabrica; quem a controle pode servir JS na origem \
da Conversa. Prefere --tls externo (um túnel teu ligado ao socket; ver CONVERSA.md)."
# o mesmo texto do aviso da UI (static/conversa.js, AVISO_TOKENS)
AVISO_TOKENS="A Conversa consome tokens da tua conta Claude (a do dono, no servidor). Cada mensagem custa conforme o \
modelo e o tamanho da conversa; o gasto de hoje aparece no cabeçalho do painel. Nada te bloqueia: és tu que geres o \
consumo."

erro() { printf 'erro: %s\n' "$*" >&2; exit 1; }
info() { printf '%s\n' "$*"; }
uso() { sed -n '4p' "$0" | sed 's/^# *//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --office) OFFICE=${2:-}; shift ;;
    --dono) DONO=${2:-}; shift ;;
    --pasta) PASTA=${2:-}; shift ;;
    --pasta-trabalho) TRABALHO=${2:-}; shift ;;
    --permissoes) PERMISSOES=${2:-}; shift ;;
    --lancadores) LANCADORES=${2:-}; shift ;;
    --fuso) FUSO=${2:-}; shift ;;
    --voz) VOZ=${2:-}; shift ;;
    --voz-idioma) VOZ_IDIOMA=${2:-}; shift ;;
    --tls) TLS=${2:-}; shift ;;
    --grupo-socket) GRUPO=${2:-}; shift ;;
    --claude) CLAUDE=${2:-}; shift ;;
    --python) PY=${2:-}; shift ;;
    --novo-codigo) NOVO_CODIGO=1 ;;
    --revogar) REVOGAR=1 ;;
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

# como_dono <comando…>: faz, como o dono (os ficheiros na casa dele são dele)
como_dono() { faz runuser -u "$DONO" -- "$@"; }

# escreve <destino> <modo>: o stdin vai para o destino (tmp na mesma pasta + mv), escrito pelo dono: o root nunca
# escreve na casa dele (nem segue um symlink que lá esteja)
escreve() {
  local destino=$1 modo=$2
  printf '+ escrever %s (%s, %s)\n' "$destino" "$modo" "$DONO"
  if [ "$VERIFICAR" = 1 ]; then cat > /dev/null; return; fi
  # shellcheck disable=SC2016 # o $1/$2 são do sh -c
  runuser -u "$DONO" -- sh -c 'umask 077; t=$(mktemp "$1.XXXXXX") || exit 1
    if cat > "$t" && chmod "$2" "$t" && mv -f "$t" "$1"; then exit 0; fi
    rm -f "$t"; exit 1' sh "$destino" "$modo"
}

# escreve_root <destino>: o stdin vai para um ficheiro do root (0644, tmp na mesma pasta + mv) em /etc ou /opt
escreve_root() {
  local destino=$1
  printf '+ escrever %s (0644, root)\n' "$destino"
  if [ "$VERIFICAR" = 1 ]; then cat > /dev/null; return; fi
  local t
  t=$(mktemp "$destino.XXXXXX")
  if cat > "$t" && chmod 0644 "$t" && mv -f "$t" "$destino"; then return 0; fi
  rm -f "$t"
  return 1
}

pasta() { [ -d "$1" ] && [ ! -L "$1" ]; }

# subida <caminho>: o caminho e cada pasta acima dele até / (as que existam) não podem ser escritas pelo Office nem
# pelo dono, nem ser deles (o dono de uma pasta pode dar-se a escrita); imprime «<utilizador> <pasta>» da 1.ª que
# falhe, ou nada. Só como root (runuser)
subida() {
  local d=$1 u
  while :; do
    if [ -e "$d" ]; then
      for u in "$OFFICE" "$DONO"; do
        if runuser -u "$u" -- test -w "$d" || runuser -u "$u" -- test -O "$d"; then
          printf '%s %s\n' "$u" "$d"
          return 0
        fi
      done
    fi
    [ "$d" != / ] || return 0
    d=$(dirname "$d")
  done
}

# --- argumentos ---
[ -n "$OFFICE" ] || { uso >&2; erro "falta --office <utilizador que corre o Office>"; }
[ -n "$DONO" ] || erro "falta --dono <utilizador> (sem sudo, o SUDO_USER não diz quem é)"
for u in "$OFFICE" "$DONO"; do
  [[ "$u" =~ $RX_UTILIZADOR ]] || erro "utilizador inválido: $u"
  id "$u" > /dev/null 2>&1 || erro "o utilizador $u não existe"
done
[ "$DONO" != "$OFFICE" ] || erro "--dono tem de ser outro utilizador que não o do Office (ADR-023: dois uids)"
[ "$DONO" != root ] || erro "--dono não pode ser o root: usa o teu utilizador de todos os dias"
[[ "$PASTA" =~ $RX_CAMINHO ]] || erro "--pasta tem de ser um caminho absoluto só com letras, números e / . _ + -"
PASTA=${PASTA%/}
[[ "$PY" =~ $RX_CAMINHO ]] || erro "--python tem de ser um caminho absoluto só com letras, números e / . _ + -"
[ "$PASTA" != "" ] || erro "--pasta não pode ser a raiz"
case "$PERMISSOES" in cartao|espelho) ;; *) erro "--permissoes tem de ser cartao ou espelho" ;; esac
case "$VOZ" in desligada|local|externa) ;; *) erro "--voz tem de ser desligada, local ou externa" ;; esac
[[ "$VOZ_IDIOMA" =~ $RX_IDIOMA ]] || erro "--voz-idioma: duas letras minúsculas (ex.: pt, en)"
# o guardião corre com ProtectHome=yes: a pasta não pode ficar dentro de uma casa
case "$PASTA/" in
  /home/*|/root/*|/run/user/*) erro "--pasta não pode ficar em /home, /root nem /run/user (o guardião não as vê)" ;;
esac
case "$TLS" in externo|caddy) ;; *) erro "--tls tem de ser externo ou caddy" ;; esac
if [ -n "$GRUPO" ]; then
  [[ "$GRUPO" =~ $RX_UTILIZADOR ]] || erro "--grupo-socket inválido: $GRUPO"
  getent group "$GRUPO" > /dev/null || erro "o grupo $GRUPO não existe (--grupo-socket)"
  # o grupo do socket dá a API da Conversa sem passar pelo TLS: o Office não pode estar nele
  read -r -a GRUPOS_OFFICE <<< "$(id -nG "$OFFICE")"
  for g in "${GRUPOS_OFFICE[@]}"; do
    [ "$g" != "$GRUPO" ] || erro "o $OFFICE (Office) pertence ao grupo $GRUPO: o --grupo-socket tem de ser só do teu \
túnel (tira-o do grupo ou cria outro, ex.: sudo groupadd cloudflared)"
  done
elif [ "$TLS" = externo ]; then
  erro "--tls externo precisa de --grupo-socket <grupo do teu túnel> (ex.: cloudflared), um grupo sem o $OFFICE"
else
  GRUPO=$(id -gn "$OFFICE")
fi
[[ "$LANCADORES" =~ $RX_LANCADORES ]] || erro "--lancadores: nomes separados por «:» (ex.: lancar:continuar)"
CASA=$(getent passwd "$DONO" | cut -d: -f6)
if ! [[ "$CASA" =~ $RX_CAMINHO ]] || ! pasta "$CASA"; then erro "a casa do $DONO ($CASA) não é uma pasta"; fi
TRABALHO=${TRABALHO:-$CASA}
[[ "$TRABALHO" =~ $RX_CAMINHO ]] || erro "--pasta-trabalho tem de ser um caminho absoluto só com letras, números e / . _ + -"
pasta "$TRABALHO" || erro "--pasta-trabalho: $TRABALHO não é uma pasta"
if [ -z "$FUSO" ]; then
  FUSO=$(timedatectl show -p Timezone --value 2> /dev/null || true)
  FUSO=${FUSO:-UTC}
fi
if ! [[ "$FUSO" =~ $RX_FUSO ]] || [ ! -f "/usr/share/zoneinfo/$FUSO" ]; then
  erro "--fuso desconhecido: $FUSO (ex.: Europe/Lisbon)"
fi
# o root mexe nestes caminhos (install, setfacl): nenhum pode ser um symlink nem outra coisa que não uma pasta
for p in "$PASTA" "$PASTA/verificados" "$PASTA/verificados/audio" "$PASTA/conversas" "$PASTA/publico" \
         "$PASTA/sock"; do
  [ ! -L "$p" ] || erro "$p é um symlink: apaga-o e corre de novo"
  if [ -e "$p" ] && ! pasta "$p"; then erro "$p existe e não é uma pasta: apaga-o e corre de novo"; fi
done

# --- o que tem de haver no sistema ---
COPIAR=(chat.py reader.py chat_host/guardiao.py chat_host/ponte.py chat_host/mcp.py chat_host/stt.py
        chat_host/codigo.py chat_host/office-conversa.service chat_host/office-conversa-guardiao.service)
for f in "${COPIAR[@]}" static/conversa.manifesto; do
  [ -f "$REPO/$f" ] || erro "corre-o a partir do repositório do Lite (falta $f)"
  [ ! -L "$REPO/$f" ] || erro "$REPO/$f é um symlink: usa uma cópia do Lite sem symlinks"
done
# a UI da Conversa: os nomes do static/conversa.manifesto (o 1.º é o conversa.html), relativos a static/
WEB=()
while IFS= read -r n || [ -n "$n" ]; do
  [ -n "$n" ] || continue
  [[ "$n" =~ $RX_WEB ]] || erro "static/conversa.manifesto: nome inválido: $n"
  [ -f "$REPO/static/$n" ] && [ ! -L "$REPO/static/$n" ] || erro "falta static/$n (do conversa.manifesto)"
  WEB+=("$n")
done < "$REPO/static/conversa.manifesto"
[ "${#WEB[@]}" -gt 0 ] && [ "${WEB[0]}" = conversa.html ] || \
  erro "static/conversa.manifesto: a 1.ª linha tem de ser conversa.html"
command -v setfacl > /dev/null || erro "falta o setfacl: sudo apt-get install -y acl"
command -v systemctl > /dev/null || erro "falta o systemd (systemctl)"
command -v loginctl > /dev/null || erro "falta o loginctl (systemd-logind)"
[ -x "$PY" ] || erro "falta o $PY (executável): sudo apt-get install -y python3 (ou --python <caminho>)"
# o guardião e a ponte importam o reader.py do Lite (máscara), que usa quantificadores possessivos: Python >= 3.11
"$PY" -c 'import sys; sys.exit(sys.version_info < (3, 11))' || \
  erro "a Conversa precisa do $PY >= 3.11 (o Ubuntu 22.04 traz o 3.10; o 24.04 traz o 3.12): sudo apt-get install -y python3.11 e --python /usr/bin/python3.11"
if [ "$VOZ" = local ]; then
  "$PY" -c 'import ensurepip' 2> /dev/null || erro "a voz local precisa do venv: sudo apt-get install -y python3-venv"
fi
if [ -z "$CLAUDE" ]; then
  if [ -x "$CASA/.local/bin/claude" ]; then CLAUDE=$CASA/.local/bin/claude; else CLAUDE=$(command -v claude || true); fi
fi
if ! [[ "$CLAUDE" =~ $RX_CAMINHO ]] || [ ! -x "$CLAUDE" ]; then
  erro "não encontrei o claude do $DONO: instala o Claude Code nele e faz \`claude\` → /login (ou --claude <caminho>)"
fi
if [ "$VERIFICAR" = 0 ] && [ "$(id -u)" != 0 ]; then
  erro "corre com sudo (ou --verificar para ver o que faria)"
fi
[ "$VERIFICAR" = 0 ] || info "--verificar: nada é mudado; isto é o que seria feito"
# a pasta de trabalho (e as acima dela, dentro da casa) não pode ser escrita pelo Office: um .claude/settings.json
# com hooks que lá pusesse correria como o dono. O código copiado para /opt não pode vir de uma cópia do Lite que o
# Office ou o dono (o motor corre como ele) consigam mudar, nem por uma pasta acima (G1): o SHA256SUMS confirmaria o
# que lá pusessem. Nem a --pasta, nem as acima dela (G5): o root cria e muda lá dentro
if [ "$(id -u)" = 0 ]; then
  so_root="corre o instalador como root de uma cópia do Lite só do root: a release extraída depois de conferir o \
SHA256SUMS (ex.: /usr/local/src/office-lite-<versão>; ver CONVERSA.md)"
  for u in "$OFFICE" "$DONO"; do
    escrevivel=$(runuser -u "$u" -- find "$REPO" \( -writable -o -user "$u" \) -print -quit 2> /dev/null || true)
    [ -z "$escrevivel" ] || erro "o $u consegue escrever em $escrevivel (ou é dele): $so_root"
  done
  falha=$(subida "$(dirname "$REPO")")
  [ -z "$falha" ] || erro "o ${falha%% *} consegue escrever em ${falha#* } (ou é dele), acima de $REPO: $so_root"
  falha=$(subida "$PASTA")
  [ -z "$falha" ] || erro "o ${falha%% *} consegue escrever em ${falha#* } (ou é dele): a --pasta e as pastas \
acima dela têm de ser só do root (ex.: /srv/office-conversa)"
  d=$TRABALHO
  while :; do
    if runuser -u "$OFFICE" -- test -w "$d"; then
      erro "o $OFFICE (Office) consegue escrever em $d: a pasta de trabalho e as acima dela, dentro da casa, não \
podem ser escritas pelo Office (um .claude/ lá correria como o $DONO); tira-lhe a escrita ou usa outra --pasta-trabalho"
    fi
    case "$d" in "$CASA"/*) d=$(dirname "$d") ;; *) break ;; esac
  done
  # e as definições que o claude lê lá dentro e na casa, se já existirem
  for f in "$TRABALHO/.claude" "$TRABALHO/.claude/settings.json" "$TRABALHO/.claude/settings.local.json" \
           "$TRABALHO/.mcp.json" "$TRABALHO/CLAUDE.md" "$CASA/.claude" "$CASA/.claude/settings.json" "$CASA/.claude.json"; do
    if [ -e "$f" ] && runuser -u "$OFFICE" -- test -w "$f"; then
      erro "o $OFFICE (Office) consegue escrever em $f: o claude do $DONO lê-o; tira-lhe a escrita"
    fi
  done
else
  info "(sem root não confirmo que o $OFFICE e o $DONO não escrevem em $REPO nem em $PASTA, nem o $OFFICE em $TRABALHO;"
  info " a instalação a sério confirma-o)"
fi

CONFIG=$CASA/.config/office-conversa
VENV=$CASA/.local/share/office-conversa/stt-venv
MODELOS=$CASA/.local/share/office-conversa/stt-modelos

# 1. o utilizador do guardião (de sistema, sem casa nem shell, como o office-accoes)
if id "$GUARDIAO" > /dev/null 2>&1; then
  info "o utilizador $GUARDIAO já existe"
else
  faz useradd --system --no-create-home --shell /usr/sbin/nologin "$GUARDIAO"
fi

# 2. pastas: verificados/ é do guardião (o dono lê), conversas/ do dono (o guardião lê; o Office deixa de ler),
# publico/ do guardião (o Office lê o publico.json) e sock/ do guardião com o grupo do proxy (setgid: o socket herda-o)
faz install -d -o root -g root -m 0755 "$PASTA"
faz install -d -o "$GUARDIAO" -g "$GUARDIAO" -m 0750 "$PASTA/verificados" "$PASTA/verificados/audio"
for p in "$PASTA/verificados" "$PASTA/verificados/audio"; do
  faz setfacl -P -m "u:$DONO:r-x" "$p"
  faz setfacl -P -d -m "u:$DONO:r--" "$p"
done
faz install -d -o "$DONO" -g "$DONO" -m 0750 "$PASTA/conversas"
# como o dono (as conversas são dele; o root nunca mexe lá dentro), sem seguir symlinks: o guardião lê tudo e o
# Office fica com uma entrada sem permissões (tira a r-x que a instalação anterior lhe dava, também por omissão)
como_dono setfacl -R -P -m "u:$GUARDIAO:rX,u:$OFFICE:---" "$PASTA/conversas"
como_dono find "$PASTA/conversas" -type d -exec setfacl -P -d -m "u:$GUARDIAO:r-x,u:$OFFICE:---" {} +
faz install -d -o "$GUARDIAO" -g "$GUARDIAO" -m 0755 "$PASTA/publico"
faz install -d -o "$GUARDIAO" -g "$GRUPO" -m 2750 "$PASTA/sock"

# 3. migração da Conversa sem guardião: a chave e os pids saem do estado do dono (o motor lia-os) e o entrada.json
# das conversas; com a chave antiga, código novo obrigatório
ESTADO=$CASA/.local/state/office-conversa
MIGRAR=0
if [ -e "$ESTADO/chave" ] || [ -e "$PASTA/conversas/entrada.json" ]; then
  MIGRAR=1
  como_dono rm -f "$ESTADO/chave" "$ESTADO/pids.json" "$PASTA/conversas/entrada.json"
fi

# 4. código e UI em /opt (do root: nem o Office, nem o dono, nem o guardião os mudam) e o SHA256SUMS de tudo
faz rm -rf "$OPT/web"   # o que saiu do manifesto sai também daqui (o guardião recusa ficheiros a mais)
faz install -d -o root -g root -m 0755 "$OPT" "$OPT/chat_host" "$OPT/web"
SOMAS=("${COPIAR[@]}" conversa.manifesto)
for f in "${COPIAR[@]}"; do
  faz install -o root -g root -m 0644 "$REPO/$f" "$OPT/$f"
done
faz install -o root -g root -m 0644 "$REPO/static/conversa.manifesto" "$OPT/conversa.manifesto"
for n in "${WEB[@]}"; do
  case "$n" in */*) faz install -d -o root -g root -m 0755 "$OPT/web/${n%/*}" ;; esac
  faz install -o root -g root -m 0644 "$REPO/static/$n" "$OPT/web/$n"
  SOMAS+=("web/$n")
done
if [ "$VERIFICAR" = 1 ]; then
  escreve_root "$OPT/SHA256SUMS" < /dev/null
else
  (cd "$OPT" && sha256sum -- "${SOMAS[@]}") | escreve_root "$OPT/SHA256SUMS"
fi

# 5. estado da ponte, só do dono (nunca montado no contentor; já sem a chave)
UNITS=$CASA/.config/systemd/user
como_dono mkdir -p "$ESTADO" "$UNITS/office-conversa.service.d"
como_dono chmod 0700 "$ESTADO"

# 6. código da Conversa, no estado do guardião (G2: de uso único, regista um browser e morre; os registados ficam em
# chaves.json): sem código por usar nem browsers registados, com a chave antiga (do código), ao migrar, com
# --novo-codigo (mais um browser) ou com --revogar (que antes esquece os registados); só aparece aqui, uma vez
faz install -d -o "$GUARDIAO" -g "$GUARDIAO" -m 0700 "$GEST"
if [ "$REVOGAR" = 1 ]; then   # o guardião reinicia abaixo: as sessões abertas caem
  faz rm -f "$GEST/chaves.json"
fi
if [ -e "$GEST/chave" ]; then MIGRAR=1; fi   # a chave PBKDF2 do código (revisão anterior): registo novo
CODIGO=""
if [ "$NOVO_CODIGO" = 1 ] || [ "$REVOGAR" = 1 ] || [ "$MIGRAR" = 1 ] || \
   { [ ! -f "$GEST/entrada.json" ] && [ ! -f "$GEST/chaves.json" ]; }; then
  printf '+ %s\n' "runuser -u $GUARDIAO -- $PY -I $OPT/chat_host/codigo.py --estado $GEST"
  if [ "$VERIFICAR" = 0 ]; then
    CODIGO=$(runuser -u "$GUARDIAO" -- "$PY" -I "$OPT/chat_host/codigo.py" --estado "$GEST")
  fi
else
  info "há um código por usar ou browsers registados: fica como está (--novo-codigo para registar outro browser,"
  info "--revogar para esquecer os registados)"
fi

# 7. voz
case "$VOZ" in
  local)
    [ -x "$VENV/bin/python" ] || como_dono "$PY" -m venv "$VENV"
    como_dono "$VENV/bin/python" -m pip install -q "faster-whisper==1.2.1" "av==18.1.0"
    como_dono "$VENV/bin/python" -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', \
compute_type='int8', download_root='$MODELOS')"
    ;;
  externa)
    como_dono mkdir -p "$CONFIG"
    como_dono chmod 0700 "$CONFIG"
    if [ -e "$CONFIG/voz.env" ]; then
      info "$CONFIG/voz.env já existe: fica como está"
    else
      escreve "$CONFIG/voz.env" 0600 <<'EOF'
# Voz externa da Conversa: um fornecedor compatível com POST /v1/audio/transcriptions (o protocolo da OpenAI).
# Só do dono (0600): a chave nunca vai para o Office, para os eventos nem para os registos. Depois de preencher:
#   systemctl --user restart office-conversa
# CONVERSA_STT_URL é o endereço base do fornecedor (https://…; acaba ou não em /v1).
CONVERSA_STT_URL=
CONVERSA_STT_MODELO=whisper-1
CONVERSA_STT_CHAVE=
EOF
    fi
    ;;
esac

# 8. unit de sistema do guardião, com o ambiente desta instalação no drop-in (escrito pelo root)
faz install -o root -g root -m 0644 "$REPO/chat_host/office-conversa-guardiao.service" "$UNIT_SIS"
faz install -d -o root -g root -m 0755 "$UNIT_SIS.d"
{
  printf '# Gerado pelo instalar-conversa.sh: o ambiente desta instalação (volta a correr o instalador para mudar).\n'
  printf '[Service]\n'
  printf 'Environment=CONVERSA_PASTA=%s\n' "$PASTA"
  printf 'Environment=CONVERSA_ESCUTA=unix:%s\n' "$PASTA/sock/guardiao.sock"
  printf 'Environment=CONVERSA_PERMISSOES=%s\n' "$PERMISSOES"
  printf 'Environment=CONVERSA_VOZ=%s\n' "$VOZ"
  printf 'Environment=CONVERSA_VOZ_IDIOMA=%s\n' "$VOZ_IDIOMA"
  printf 'Environment=CONVERSA_FUSO=%s\n' "$FUSO"
  printf 'ReadWritePaths=%s %s %s\n' "$PASTA/verificados" "$PASTA/publico" "$PASTA/sock"
  if [ "$PY" != "$PY_PADRAO" ]; then   # outro Python: troca o ExecStart da unit
    printf 'ExecStart=\nExecStart=%s -I %s/chat_host/guardiao.py\n' "$PY" "$OPT"
  fi
} | escreve_root "$UNIT_SIS.d/instalacao.conf"
faz systemctl daemon-reload
faz systemctl enable office-conversa-guardiao.service
faz systemctl restart office-conversa-guardiao.service

# 9. unit de utilizador do dono (a ponte), com linger (corre sem sessão aberta)
escreve "$UNITS/office-conversa.service" 0644 < "$REPO/chat_host/office-conversa.service"
{
  printf '# Gerado pelo instalar-conversa.sh: o ambiente desta instalação (volta a correr o instalador para mudar).\n'
  printf '[Service]\n'
  printf 'Environment=PATH=%s:/usr/local/bin:/usr/bin:/bin\n' "$(dirname "$CLAUDE")"
  printf 'Environment=CONVERSA_CLAUDE=%s\n' "$CLAUDE"
  printf 'Environment=CONVERSA_CWD=%s\n' "$TRABALHO"
  printf 'Environment=CONVERSA_VERIFICADOS_DIR=%s\n' "$PASTA/verificados"
  printf 'Environment=CONVERSAS_DIR=%s\n' "$PASTA/conversas"
  printf 'Environment=CONVERSA_ESTADO_DIR=%s\n' "$ESTADO"
  printf 'Environment=CONVERSA_PERMISSOES=%s\n' "$PERMISSOES"
  printf 'Environment=CONVERSA_LANCADORES=%s\n' "$LANCADORES"
  printf 'Environment=CONVERSA_FUSO=%s\n' "$FUSO"
  printf 'Environment=CONVERSA_VOZ=%s\n' "$VOZ"
  if [ "$PY" != "$PY_PADRAO" ]; then   # outro Python: troca o ExecStart da unit
    printf 'ExecStart=\nExecStart=%s -I %s/chat_host/ponte.py\n' "$PY" "$OPT"
  fi
  if [ "$VOZ" = local ]; then
    printf 'Environment=CONVERSA_STT_PYTHON=%s\n' "$VENV/bin/python"
    printf 'Environment=CONVERSA_STT_MODELOS=%s\n' "$MODELOS"
  fi
} | escreve "$UNITS/office-conversa.service.d/instalacao.conf" 0644
faz loginctl enable-linger "$DONO"
faz systemctl --user -M "$DONO@" daemon-reload
faz systemctl --user -M "$DONO@" enable office-conversa.service
faz systemctl --user -M "$DONO@" restart office-conversa.service

info ""
info "pronto. Liga a Conversa no office.yaml (conversa: ativa: true, pasta: $PASTA, site: conversa.<o teu domínio>,"
info "tls: $TLS) e volta a gerar o compose. Permissões ($PERMISSOES) e voz ($VOZ) ficam nas units: muda-as voltando a"
info "correr isto; o nome do assistente muda-se na Conversa."
if [ "$TLS" = externo ]; then
  info "O TLS termina no teu túnel (ex.: Cloudflare Tunnel, no grupo $GRUPO): aponta-o para"
  info "unix:$PASTA/sock/guardiao.sock com o site da Conversa (passos no CONVERSA.md)."
else
  info "O site precisa de uma linha de DNS (A/AAAA para este servidor) e abre só com HTTPS."
  info ""
  info "Aviso: $AVISO_CADDY"
fi
if [ -e "$PASTA/fila" ]; then
  info ""
  info "A pasta $PASTA/fila é da versão anterior (tem os textos dos pedidos): já não é usada e podes apagá-la."
fi
if [ -n "$CODIGO" ]; then
  info ""
  if [ "$MIGRAR" = 1 ]; then info "A chave antiga (do código) deixou de valer: cada browser regista-se de novo."; fi
  info "Código da Conversa (aparece só agora; de uso único: regista um browser e morre; para outro, --novo-codigo):"
  info ""
  info "    $CODIGO"
fi
if [ "$REVOGAR" = 1 ]; then
  info ""
  info "Os browsers registados foram esquecidos: o código abaixo regista um de novo (para cada outro, --novo-codigo)."
fi
if [ "$VOZ" = externa ]; then
  info ""
  info "Voz externa: preenche $CONFIG/voz.env (URL, modelo e chave) e reinicia a ponte:"
  info "  sudo systemctl --user -M $DONO@ restart office-conversa"
fi
info ""
info "Aviso: $AVISO_TOKENS"

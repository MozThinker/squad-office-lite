# A Conversa (Squad Office Lite 0.6.1)

[English](CONVERSA.en.md) · [README](README.md)

A Conversa é o teu Claude Code no telemóvel: um chat com endereço próprio (ex. `https://conversa.o-teu-dominio`), que
fala com o `claude` do teu utilizador na VPS. O Office mostra só um botão com o nome e os pedidos à espera; a Conversa
abre-se à parte, nunca dentro do Office. Só existe no **modo VPS** (precisa de HTTPS, de uma linha de DNS e de
utilizadores do sistema próprios); no modo Docker do PC fica desligada. Vem desligada por omissão e nada muda em quem
não a liga.

Há três peças (ADR-023, revisão do guardião): o **guardião** (utilizador de sistema `office-conversa`) serve a página
da Conversa, guarda a chave que confere cada pedido e escreve os aceites numa pasta só dele; a **ponte** (o teu
utilizador, o `dono`) lê essa pasta e corre o teu `claude`; o **Office** só lê um resumo público (nome, pedidos à
espera, gasto do dia). Nem o Office, nem a ponte, nem o próprio assistente chegam à chave.

## O que faz e o que não faz

Faz:
- Conversas com o teu Claude Code no servidor, com histórico, em mobile e desktop; abre-se pelo botão do cabeçalho
  do Office (num separador próprio) ou directamente no endereço dela.
- Pede o teu **cartão de aprovação** antes de cada acção com efeito (correr um comando, escrever ou editar ficheiros,
  abrir um URL), e mostra o que seria feito por inteiro; também responde a perguntas e a planos do assistente.
- Pode lançar squads do kit (`lancar`, `continuar`, `diretor`, `squad`, a lista é a do `--lancadores`), sempre com
  cartão, sem «Aprovar nesta conversa».
- Ditado por voz (opcional, ver abaixo).

Não faz:
- Não corre nada sozinha: sem a tua resposta a um cartão, expira em 10 minutos e a acção é negada.
- Não bloqueia pelo gasto: mostra-o (`hoje: X USD` no cabeçalho do painel) e nada mais.
- Não dá ao Office, nem ao contentor, a tua conta Claude: a ponte corre como o teu utilizador (`dono`), o Office
  nunca vê as credenciais nem as conversas, e só o resumo público é montado no contentor.
- Não existe no modo Docker do PC nem sem HTTPS.

## Aviso: a Conversa consome tokens da tua conta

> A Conversa consome tokens da tua conta Claude (a do dono, no servidor). Cada mensagem custa conforme o modelo e o
> tamanho da conversa; o gasto de hoje aparece no cabeçalho do painel. Nada te bloqueia: és tu que geres o consumo.

Este aviso aparece na primeira vez que envias uma mensagem e tens de o aceitar (o guardião recusa mensagens sem ele).
Com a assinatura do Claude conta para o teu limite de utilização; com chave de API, paga-se ao preço da API.

**Exemplo de custo por mensagem (ESTIMATIVA, 23/11/2026).** A preço de API do Sonnet, 3 USD por milhão de tokens de
entrada e 15 USD por milhão de saída (fonte: <https://www.anthropic.com/pricing>; os preços mudam, confirma lá):
uma mensagem com 20 000 tokens de contexto e 1 000 tokens de resposta custa
20 000 × 3 / 1 000 000 + 1 000 × 15 / 1 000 000 = 0,06 + 0,015 ≈ **0,08 USD**. É uma ordem de grandeza: uma conversa
longa, vários passos com ferramentas, outro modelo (o Opus custa mais, o Haiku menos) ou a cache de prompts mudam o
valor. O número que vale é o do cabeçalho, que soma o custo que o próprio `claude` reporta, no fuso da instalação.

## Instalar

Pré-requisitos: o Office já instalado pelo `install.sh`, e o Claude Code instalado e com sessão aberta (`claude` →
`/login`) no utilizador que vai ser o `dono` (o teu utilizador de todos os dias; nunca o root nem o utilizador do
Office, têm de ser dois utilizadores). Pacotes: `acl` e `systemd`.

1. **Endereço e DNS:** escolhe o endereço da Conversa, de preferência num **domínio registável diferente** do do
   Office (ex. `conversa.outro-dominio.exemplo` em vez de `conversa.o-teu-dominio`): assim nem cookies nem o gestor
   de palavras-passe misturam as duas origens. Com o túnel (a omissão, abaixo) o DNS é o do túnel; com `tls: caddy`
   cria um registo A/AAAA para a VPS.
2. **Uma cópia do Lite só do root.** O instalador copia o código e a página da Conversa para `/opt/office-conversa` e
   recusa correr de uma cópia (ou de uma pasta acima dela, até `/`) em que o utilizador do Office **ou o `dono`**
   consigam escrever, ou que seja deles: o assistente corre como o `dono`, e quem mudasse essa cópia escolhia o que o
   guardião corre. Descarrega a release **como root**, confere-a com o `SHA256SUMS` (como o `get.sh`) e extrai-a para
   uma pasta do root:
   ```bash
   sudo -i
   mkdir -p /usr/local/src && cd "$(mktemp -d)"
   curl -fLO https://github.com/MozThinker/squad-office-lite/releases/download/v0.6.1/squad-office-lite-0.6.1.tar.gz
   curl -fLO https://github.com/MozThinker/squad-office-lite/releases/download/v0.6.1/SHA256SUMS
   sha256sum -c SHA256SUMS && tar xzf squad-office-lite-0.6.1.tar.gz --no-same-owner -C /usr/local/src
   mv /usr/local/src/squad-office-lite-0.6.1 /usr/local/src/office-lite-0.6.1
   ```
3. **O grupo do túnel** (com `tls: externo`, a omissão): cria um grupo só para quem chega ao socket do guardião (o
   utilizador do Office não pode pertencer-lhe), ex. `sudo groupadd cloudflared`, e corre lá o `cloudflared` (ver
   «Túnel», abaixo).
4. Na pasta do root, vê o que seria feito e depois instala:
   ```bash
   cd /usr/local/src/office-lite-0.6.1
   ./instalar-conversa.sh --office <utilizador do Office> --dono <o teu utilizador> --grupo-socket cloudflared --verificar
   sudo ./instalar-conversa.sh --office <utilizador do Office> --dono <o teu utilizador> --grupo-socket cloudflared
   ```
   Opções: `--tls externo|caddy` (omissão `externo`; o mesmo do `conversa.tls`), `--grupo-socket` (quem chega ao
   socket; obrigatório com `externo` e sem o utilizador do Office; com `caddy`, omissão o grupo do utilizador do
   Office), `--pasta` (omissão `/srv/office-conversa`; nunca dentro de `/home`; ela e as pastas acima só do root),
   `--pasta-trabalho` (onde o assistente trabalha; omissão a tua casa), `--permissoes cartao|espelho`,
   `--lancadores`, `--fuso`, `--voz desligada|local|externa`, `--voz-idioma` (omissão `pt`), `--claude <caminho>`,
   `--python <caminho>`, `--novo-codigo` e `--revogar`. O instalador cria o utilizador `office-conversa` e as pastas
   com ACL, copia o código e a página para `/opt/office-conversa` (do root) com um `SHA256SUMS`, gera o **código da
   Conversa** e instala a unit de sistema `office-conversa-guardiao.service` e a unit de utilizador
   `office-conversa.service` (com linger). Corre outra vez sem medo: o que existe fica.
5. **Usa o código** que aparece no ecrã (24 letras e números, em 6 grupos) no browser onde vais usar a Conversa: só se
   mostra uma vez, não fica gravado e **morre no 1.º uso** (regista esse browser).
6. No `office.yaml` liga a Conversa e volta a correr o `./install.sh` (regenera o compose):
   ```yaml
   conversa:
     ativa: true
     pasta: /srv/office-conversa       # a do instalar-conversa.sh
     site: conversa.outro-dominio.exemplo   # o endereço do passo 1 (o do botão no Office)
     tls: externo                      # a omissão; caddy só por opção (ver «TLS e domínio»)
   ```
   O nome do assistente, as permissões e a voz já não vão no `office.yaml` (o Office escreve-o): as permissões e a
   voz ficam nas units, pelo instalador, e o nome muda-se na própria Conversa.
7. Abre `https://<o endereço da Conversa>` (ou o botão da Conversa no Office) e escreve o código.

**Ubuntu 22.04:** o guardião e a ponte precisam de Python ≥ 3.11 e o 22.04 traz o 3.10. Instala-o e diz ao
instalador:
```bash
sudo apt-get install -y python3.11
sudo ./instalar-conversa.sh --office <utilizador do Office> --grupo-socket cloudflared --python /usr/bin/python3.11
```
O instalador continua a verificar a versão e recusa um Python mais antigo. Com `--python`, as duas units passam a
arrancar com esse Python (e o venv da voz local também). O Ubuntu 24.04 já traz o 3.12.

## TLS e domínio

A Conversa só abre com HTTPS: o guardião recusa tudo (403) se o pedido não chegou pelo proxy em HTTPS, e nunca abre a
sessão em HTTP. O endereço dela tem de ser outro que não o do Office (`conversa.site`), de preferência noutro domínio
registável. Sem `conversa.site`, o botão do Office explica o que falta.

- **`tls: externo` (omissão):** o TLS termina **fora** do utilizador do Office, num túnel ou proxy teu ligado ao
  socket do guardião. O Caddy do Office não monta nada da Conversa. É o que fecha o risco de quem controle o Office
  servir JS na origem da Conversa (ADR-023, «Revisão: guardião»), e o único com o proxy Traefik.
- **`tls: caddy` (só por opção explícita):** o Caddy do Office serve o `site` e obtém o certificado sozinho (precisa
  do `./install.sh --dominio` e de um A/AAAA para a VPS; o browser pede também o utilizador e a palavra-passe do
  Office). **Aviso: o TLS termina no Caddy da `fabrica`; quem a
  controle pode servir JS na origem da Conversa** (e ler o tráfego). O instalador mostra este aviso.

## Túnel (`tls: externo`, a omissão)

Exemplo com um Cloudflare Tunnel do `dono` (outro túnel ou proxy serve, desde que corra fora do utilizador do Office):
1. `sudo groupadd cloudflared` e um utilizador de sistema para o túnel nesse grupo
   (`sudo useradd --system --gid cloudflared --shell /usr/sbin/nologin cloudflared`). O utilizador do Office **não**
   entra no grupo (o instalador recusa).
2. Instala o `cloudflared` (pacote da Cloudflare), faz `cloudflared tunnel login` e
   `cloudflared tunnel create conversa`, e aponta o DNS do endereço da Conversa para o túnel
   (`cloudflared tunnel route dns conversa <endereço>`).
3. Na configuração do túnel (`/etc/cloudflared/config.yml`, do root, `0644`):
   ```yaml
   tunnel: conversa
   credentials-file: /etc/cloudflared/conversa.json
   ingress:
     - hostname: conversa.outro-dominio.exemplo
       service: unix:/srv/office-conversa/sock/guardiao.sock
       originRequest:
         httpHostHeader: conversa.outro-dominio.exemplo
     - service: http_status:404
   ```
   As credenciais do túnel ficam do root/`cloudflared` (`0600`), nunca na casa do utilizador do Office.
4. `sudo cloudflared service install` e, na unit dele, `User=cloudflared` e `Group=cloudflared`
   (`sudo systemctl edit cloudflared`); depois `sudo systemctl restart cloudflared`.
5. O túnel tem de pôr `X-Forwarded-Proto: https` e manter o `Host` (o guardião só aceita `Origin: https://<Host>`; sem
   isso responde 403): confirma-o abrindo a Conversa. Podes pôr o Cloudflare Access à frente, como segunda porta.

## O código e a sessão

- O código é **de uso único** e só **regista um browser**: nesse momento o browser gera a sua própria chave
  aleatória, que fica nele, só na origem da Conversa e **sem poder ser exportada**, e o guardião guarda-a em
  `/var/lib/office-conversa/chaves.json` (`0700`, só o `office-conversa`). O código morre aí: apanhado depois (por
  phishing, num ecrã, num gestor de palavras-passe), já não serve. O browser assina com a chave cada pedido; o
  guardião só aceita pedidos assinados, recentes (5 min) e nunca vistos (403 sem a assinatura certa, 409 repetido) e
  só esses chegam à ponte.
- As **entradas seguintes** são por **desafio–resposta**: o guardião manda um desafio novo e o browser responde-lhe
  com a chave, sem código nenhum. Abre-se uma **sessão de 12 horas** (cookie `__Host-`, `HttpOnly`, `Secure`,
  `SameSite=Strict`). Cinco falhas (código errado ou já usado, desafio ou assinatura errados) bloqueiam novas
  tentativas durante 15 minutos.
- **Outro browser** (outro telemóvel, outro computador): `sudo ./instalar-conversa.sh --office <u> --grupo-socket <g>
  --novo-codigo` gera outro código de uso único; os browsers já registados continuam.
- **Perdeste um dispositivo ou desconfias:** `sudo ./instalar-conversa.sh --office <u> --grupo-socket <g> --revogar`
  apaga todos os browsers registados (as sessões abertas caem) e mostra um código novo para registares o teu.
- Recomenda-se um **domínio registável diferente** do do Office para a Conversa (ver «Instalar», passo 1).

## Permissões: `cartao` e `espelho`

- **`cartao`** (omissão): cada acção de `Bash`, `Write`, `Edit`, `MultiEdit`, `NotebookEdit` e `WebFetch` chega-te como
  um cartão para aprovar («uma vez», «nesta conversa» ou recusar). «Nesta conversa» só vale para o mesmo comando, URL ou
  ficheiro com o mesmo conteúdo.
- **`espelho`**: o assistente usa as permissões do teu próprio Claude Code (modo `auto`) e só os lançadores do kit
  pedem cartão. É mais cómodo e **muito menos seguro**. Só vale se o instalador (`--permissoes espelho`, nas duas
  units) e a própria mensagem disserem `espelho`; qualquer um a dizer `cartao` ganha.

**Risco residual.** Em qualquer modo, o que aprovas corre como o teu utilizador, com o teu `claude` e os teus
ficheiros. Um cartão aprovado à pressa, texto de uma página ou de um ficheiro que induza o assistente a pedir algo
perigoso, ou a conta do teu telemóvel nas mãos de outra pessoa, são riscos que o desenho reduz mas não elimina. Lê
sempre o cartão (o comando ou o conteúdo completos), usa o `espelho` só se aceitas que o assistente faça quase tudo
sem perguntar, e aponta `--pasta-trabalho` para uma pasta que possas perder. Comandos com forma de segredo, caracteres
invisíveis ou demasiado longos nem chegam a cartão: são negados.

Os cartões dos lançadores do kit (as regras `ask`) casam pelo texto do comando: são **melhor esforço** e um comando
ofuscado (`l\ancar`, um script que os chame) passa sem cartão, sobretudo em `espelho`, onde são o único cartão.

O que o guardião fecha e o que fica (ADR-023, «Revisão: guardião»):
- Trocar um ficheiro do Office já não aprova nada: a página da Conversa vem do guardião (conferida pelo `SHA256SUMS`
  no arranque; um byte trocado e ele não arranca), noutra origem, e o JS do Office não chega à chave do browser. Um
  pedido sem a chave é recusado (403) e não fica escrito em lado nenhum.
- O assistente não lê a chave: corre como o `dono` e as chaves são do `office-conversa`.
- Com `tls: externo` (a omissão) o TLS da Conversa termina fora do utilizador do Office e este não chega ao socket:
  quem controle o Office não serve JS na origem da Conversa nem lê o tráfego dela.
- **Fica, só com `tls: caddy`:** o Caddy, que termina o TLS, corre no Docker rootless do utilizador do Office; quem o
  controle pode reconfigurá-lo e servir JS seu no endereço da Conversa, ou ler o tráfego.
- **Fica:** quem chegue ao endereço da Conversa pode encher o bloqueio de 15 min (negação de serviço da entrada); não
  pode adivinhar o código (≈ 120 bits) nem a chave de um browser.
- **Fica:** os cartões dos lançadores são melhor esforço (acima) e a voz externa só tem o tecto dos 5 MB no servidor.
- **Fica:** o assistente escreve o que o `dono` escreve (as conversas, o estado da ponte); em `cartao` só depois de um
  clique num `Bash`, em `espelho` sem clique.
- **Fica:** a instalação confia uma vez na cópia do Lite que o root extraiu e conferiu (passo 2).
- O código morre no 1.º uso, mas até lá quem o vir (terminal, sessão `tmux` partilhada, phishing) pode registar o seu browser primeiro. Usa-o logo. Se a Conversa disser «código já usado» sem teres sido tu, corre `sudo ./instalar-conversa.sh --revogar` (apaga todos os browsers registados e gera um código novo).
- Uma entrada falhada conta para o bloqueio global de 15 min: quem chegue ao site pode trancar a reentrada (negação de serviço, sem acesso).

## Gasto

O custo do dia (o que o `claude` reporta, somado no fuso da instalação) aparece no cabeçalho do painel. **Nunca
bloqueia:** não há limite diário na Conversa; só há os tectos por conversa de 30 minutos e 100 turnos
(`CONVERSA_DURACAO_MAX_MIN`, `CONVERSA_TURNOS_MAX` na unit).

## Nome do assistente

Por omissão chama-se «Assistente» (1 a 40 caracteres). Muda-se na própria Conversa; guarda-se no guardião
(`/var/lib/office-conversa/prefs.json`), vale para quem a abrir e aparece também no botão do Office. «Repor» volta ao
«Assistente».

## Voz

Três modos (`--voz` no instalador, que o põe nas duas units; o idioma é o `--voz-idioma`, omissão `pt`):
- **`desligada`** (omissão): sem microfone na Conversa. O teclado do telemóvel também dita.
- **`local`**: `sudo ./instalar-conversa.sh --office <u> --voz local` cria um ambiente virtual do dono com o
  `faster-whisper` (modelo `small`, em CPU) e descarrega o modelo (precisa de `python3-venv`, de ≈ 1 GB de disco e de
  2 GB de memória livre por transcrição). O áudio nunca sai do servidor. Sem memória livre a ponte avisa em vez de
  tentar.
- **`externa`**: o áudio é enviado a um fornecedor compatível com `POST /v1/audio/transcriptions` (o protocolo da
  OpenAI). **Privacidade e custo passam a ser contigo:** o fornecedor ouve a tua voz e cobra por minuto. O painel
  mostra o aviso e pede-te para o aceitar uma vez por fornecedor. A chave fica só em
  `~dono/.config/office-conversa/voz.env` (`0600`; `CONVERSA_STT_URL`, `CONVERSA_STT_MODELO`, `CONVERSA_STT_CHAVE`),
  nunca no `office.yaml`, no Office, no guardião, nos eventos nem nos registos; depois de a preencher:
  `systemctl --user restart office-conversa`.

O texto ditado vai para a caixa de mensagem: nunca é instrução, só envia o teu «Enviar». Áudios até 2 minutos e 5 MB; o
áudio apaga-se sempre depois de transcrito (o guardião apaga-o quando a ponte o leu). Na voz externa o servidor só
impõe os 5 MB (os 2 minutos confere-os o browser).

## Cloudflare Access (opcional, só documentado)

Podes pôr o Cloudflare Access à frente dos dois endereços como segunda porta (login por e-mail/SSO antes sequer de
chegar ao Office ou à Conversa); com o túnel (`tls: externo`, a omissão) põe-no à frente da Conversa. O Lite **não
lê** o token do Access: não substitui o código da Conversa; é só uma barreira extra. Não está testado pela CI nem pelo
instalador.

## Actualizar (0.5.2 → 0.6.0)

1. Actualiza como sempre, pelo `get.sh` (ver o README, «Actualizar»); `~/office-lite` (config, dados, credenciais)
   não é tocada e o `office.yaml` `versao: 1` continua válido. A Conversa fica desligada até a ligares.
2. Quem já a tem (ou a quer ligar) volta a correr o `sudo ./instalar-conversa.sh` de uma **cópia nova do Lite só do
   root**, extraída e conferida como no passo 2 de «Instalar» (a pasta do `get.sh`, do utilizador do Office, é
   recusada): actualiza `/opt/office-conversa` e o `SHA256SUMS` e reinicia o guardião e a ponte, mantendo os browsers
   registados e o `voz.env`. Depois volta a correr o `./install.sh`. O `get.sh` avisa se encontra a Conversa
   instalada. **Cada actualização do Lite obriga a voltar a correr o instalador** (o guardião confere o `/opt` no
   arranque).
3. **Quem vem da Conversa sem guardião** (antes da revisão do ADR-023): o instalador apaga a chave antiga do estado
   do `dono`, os `pids` e o `entrada.json` de `conversas/`, tira ao Office a leitura de `conversas/` e gera um
   **código novo** (mostrado no fim). Muda o `office.yaml` como no passo 6 de «Instalar» (sai `nome`, `permissoes`,
   `voz` e `voz_idioma`; entram `site` e `tls`) e cria o DNS do `site`. A Conversa passa a ter outro endereço: o
   browser regista-se uma vez com o código. A pasta `<pasta>/fila` antiga já não é usada e tem os textos dos pedidos:
   apaga-a.
4. **Quem vem da 1.ª revisão do guardião** (código que derivava a chave): a `chave` antiga do guardião apaga-se e o
   instalador mostra um código novo; cada browser regista-se de novo. Sem `tls` no `office.yaml`, a Conversa passa a
   `externo`: monta o túnel (acima) ou escreve `tls: caddy` se aceitas o aviso.
5. Os contratos v1 (fila de decisões, `cartoes.json`, pedidos, push) não mudaram; o da ponte passou a 2.

## Reverter

1. No `office.yaml`, `conversa.ativa: false` e `./install.sh`: o botão desaparece (e, com `tls: caddy`, o Caddy deixa
   de servir o `site`; com o túnel, pára-o: `sudo systemctl disable --now cloudflared`).
2. `sudo systemctl disable --now office-conversa-guardiao`: a página e a API da Conversa deixam de responder.
3. Com o `dono`: `systemctl --user disable --now office-conversa`. A ponte pára e deixa de consumir tokens.
4. Voz local: apagar o venv da voz, que a ponte depois responde «voz indisponível».
5. O estado do guardião (`/var/lib/office-conversa`), a auditoria e o histórico das conversas ficam onde estão, como
   registo. Apaga-os à mão se não os quiseres (e o utilizador `office-conversa` com `sudo userdel office-conversa`).

## Contrato

O formato dos eventos e das rotas está em [`docs/conversa-contrato.md`](docs/conversa-contrato.md).

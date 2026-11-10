# Contribuir para o Squad Office Lite

🇬🇧 [English](CONTRIBUTING.en.md) | 🇵🇹 **Português**

Obrigado por ajudares. Este guia é curto: lê-o antes de abrires uma issue ou um pull request. Ao participar,
aceitas o [código de conduta](CODE_OF_CONDUCT.md).

## Reportar um bug

1. Procura nas [issues](../../issues) se já existe. Se existir, acrescenta lá o teu caso.
2. Senão, abre uma issue nova com o modelo **Bug**. Ele pede o modo (VPS, ou Docker no Windows, macOS ou Linux), a
   versão do Lite, o sistema e a versão do Docker, a saída do `doctor` e o que aconteceu e esperavas. O `doctor`:
   - no modo Docker, na pasta do `compose.yaml`: `docker compose exec runner doctor`;
   - na VPS: `python3 ~/squad-office-lite/runner/office_runner.py doctor`.
3. **Nunca coles tokens, chaves de API, palavras-passe nem o conteúdo do `.env`, `claude.env`, `runner.env` ou
   `office.yaml` reais.** As issues são públicas. Se precisares de mostrar um ficheiro, troca os segredos por `…`.

Testaste a instalação só com o README? Usa o modelo **Relatório de teste da instalação**, mesmo que tenha corrido bem.

**Falhas de segurança não vão para as issues:** segue o [`SECURITY.md`](SECURITY.md) (reporte privado do GitHub).

## Propor uma ideia ou fazer uma pergunta

Usa as [Discussions](../../discussions), não as issues. Uma ideia discutida lá primeiro tem mais hipóteses de chegar a
um PR aceite.

## Preparar o ambiente e correr os testes

Precisas de Python 3.13 (o servidor; o runner corre em 3.10 ou mais recente), Node.js 22 e, para os scripts shell, o
`shellcheck`. Não precisas de Docker, Caddy nem do `claude` verdadeiro: os testes usam pastas temporárias e um
`claude` falso (`tests/bin/claude`).

```bash
git clone https://github.com/MozThinker/squad-office-lite.git
cd squad-office-lite
python3 -m venv .venv && . .venv/bin/activate
python -m pip install pytest            # opcional: -r requirements.txt (Pillow e cryptography)
python -m pytest tests -q
node --test tests/*.mjs
shellcheck install.sh get.sh scripts/*.sh
```

Sem Pillow e `cryptography` os testes de miniaturas e de cifra são saltados, como na CI. Docker, systemd e o
`install.sh` completo só correm na CI. Mais pormenores no README, «Referência» → «Testes».

## Commits

Pequenos e convencionais, em PT-PT: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`, `ci:`, com o âmbito
quando ajuda. Exemplos: `fix(configurar): …`, `docs(readme): …`. Um commit, uma mudança.

## O que se aceita num PR

**O modo VPS é o caminho principal do Lite;** o modo Docker no PC é mais recente e serve para experimentar. Um PR que
mude um dos dois não pode partir o outro.

Aceitamos:
- correcções de bugs, com um teste que falhava antes;
- melhorias ao README e às mensagens de erro (sobretudo onde um tester se perdeu);
- funcionalidades pequenas discutidas antes nas Discussions.

Cada PR:
- passa nos três comandos acima;
- usa PT-PT no código, na interface e nos textos;
- não tem constantes de uma instalação concreta (nomes, caminhos, domínios): tudo vem do `office.yaml`;
- não muda os contratos v1 (fila de decisões, `cartoes.json`, pedidos de lançamento, push) nem o formato do
  `office.yaml` sem versão nova (ver README, «Referência»);
- documenta uma chave nova do `office.yaml` na tabela do README e no `office.example.yaml`, com testes de limites;
- acrescenta uma linha ao `CHANGELOG.md`, em «Por publicar»;
- nunca passa dados do utilizador (briefs, cartões, pedidos) em argv ou pela shell.

Não aceitamos dependências novas sem uma boa razão (o servidor e o runner só usam a stdlib), nem mudanças à CI que
publiquem algo ou usem segredos. As decisões de arquitectura estão em [`DECISIONS.md`](DECISIONS.md): uma mudança que
as contrarie precisa de um ADR novo.

## Como entra o teu PR

O código deste repositório é **gerado** a cada versão a partir do repositório onde o Lite é desenvolvido. Por isso
um PR aqui não é integrado directamente (a publicação seguinte apagava-o), mas é revisto e aproveitado na mesma:

1. Abre o PR (ou a issue) aqui, como habitualmente.
2. Os mantenedores revêem-no aqui, contigo, nos comentários do PR.
3. Se for aceite, são os mantenedores que aplicam a alteração, e ela sai na versão seguinte com crédito para ti:
   `Co-authored-by` no commit, com o nome e o e-mail que usaste no PR, e, se concordares, uma menção no `CHANGELOG.md`.
4. O PR é então **fechado** com uma ligação à versão que o inclui. No GitHub aparece como «Closed» e não como
   «Merged»: é normal, a tua alteração entrou.

Para alterações grandes, abre primeiro uma issue ou uma Discussion para combinarmos a abordagem: poupa-te trabalho.

## Preparar uma versão (mantenedores)

`scripts/preparar-publico.sh DESTINO` gera uma árvore sem histórico nem `docs/` e falha se encontrar nomes internos
ou caminhos pessoais (a lista de nomes vem do passo «Genérico» do `ci.yml` de quem prepara a versão e não vai para a
cópia pública; sem ela, o script recusa-se a correr); `scripts/release.sh X.Y.Z` (exige `## Lite X.Y.Z` no
`CHANGELOG.md`) cria `dist/squad-office-lite-X.Y.Z.tar.gz` e `dist/SHA256SUMS`, os dois ficheiros que o `get.sh`
descarrega. Nenhum dos dois publica nada: a release no GitHub (tag `vX.Y.Z` com os dois ficheiros anexados) é manual.
Na árvore pública (que já não tem a lista de nomes) usa-se `--publica` (`scripts/release.sh --publica X.Y.Z`): salta a
verificação de nomes e mantém a de caminhos pessoais. É assim que o workflow de release do repositório público
constrói e atesta os dois ficheiros, que só anexa a uma release em rascunho; publicar continua manual.
Depois desse, o workflow manual de imagens (só no repositório público) constrói as imagens do Office e do runner
(`Dockerfile`, `Dockerfile.runner`; amd64 e arm64), publica-as em `ghcr.io/mozthinker/squad-office-lite` e
`…-runner` com a tag da versão, atesta-as e anexa à mesma release em rascunho o `compose.yaml` (gerado por
`runner/gerar_compose.py --modo docker` com as imagens fixadas por digest) e o `env.example`, também atestados. O
`compose.example.yaml` do repositório é a saída do gerador com as tags sem digest.

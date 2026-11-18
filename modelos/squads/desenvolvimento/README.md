---
nome: Desenvolvimento
lead: lider
resumo: Analisa um repositório, planeia alterações e revê código. Só lê e entrega planos, diagnósticos e revisões no texto final.
---
# Modelo Desenvolvimento

Analisa um repositório, planeia alterações e revê código. Só lê e entrega planos, diagnósticos e revisões no texto final.

## Agentes

- `lider` · lead · coordena, divide o pedido e junta a entrega
- `engenheiro` · apoio · lê o código e propõe a alteração passo a passo
- `revisor` · apoio · revê planos e código à procura de erros e riscos

## Quando usar

Para perceber um repositório antes de mexer nele, planear uma alteração com riscos e testes, ou
rever um ramo com olhos de quem não o escreveu.

## Permissões

Só leitura (`Read`, `Grep`, `Glob` no repositório do projecto). O lead pode chamar os outros agentes (`Agent`).
O campo `tools` de cada agente só restringe: o que a sessão pode fazer decide-o o runner (`--allowedTools` e
`--settings` da instalação), nunca o agente.

## Missão

O `CLAUDE.md` tem a secção `## Missão` com o marcador `{{MISSAO}}`, trocado pelo texto da missão quando
a squad é criada.

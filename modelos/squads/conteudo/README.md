---
nome: Conteúdo
lead: editor
resumo: Planeia e escreve textos (artigos, notas, publicações, documentação) a partir das fontes do repositório e da web, com revisão de tom, factos e clareza.
---
# Modelo Conteúdo

Planeia e escreve textos (artigos, notas, publicações, documentação) a partir das fontes do repositório e da web, com revisão de tom, factos e clareza.

## Agentes

- `editor` · lead · define o ângulo, distribui e aprova o texto final
- `redactor` · apoio · escreve o rascunho a partir do ângulo e das fontes
- `revisor` · apoio · revê factos, tom e clareza

## Quando usar

Para rascunhos de textos com factos verificados e um tom consistente.

## Permissões

Só leitura (`Read`, `Grep`, `Glob` no repositório do projecto), mais `WebSearch` e `WebFetch`. O lead pode chamar os outros agentes (`Agent`).
O campo `tools` de cada agente só restringe: o que a sessão pode fazer decide-o o runner (`--allowedTools` e
`--settings` da instalação), nunca o agente.

## Missão

O `CLAUDE.md` tem a secção `## Missão` com o marcador `{{MISSAO}}`, trocado pelo texto da missão quando
a squad é criada.

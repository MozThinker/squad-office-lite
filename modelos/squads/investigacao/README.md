---
nome: Investigação
lead: lider
resumo: Acompanha um tema na web e no repositório, junta fontes e entrega um relatório curto com o que mudou, porque importa e o que fazer a seguir.
---
# Modelo Investigação

Acompanha um tema na web e no repositório, junta fontes e entrega um relatório curto com o que mudou, porque importa e o que fazer a seguir.

## Agentes

- `lider` · lead · define as perguntas, distribui e escreve o relatório
- `vigia` · apoio · procura e filtra fontes novas sobre o tema
- `analista` · apoio · compara as fontes e tira conclusões

## Quando usar

Para vigiar um mercado, uma tecnologia ou concorrentes, e para responder a uma pergunta com
fontes verificáveis.

## Permissões

Só leitura (`Read`, `Grep`, `Glob` no repositório do projecto), mais `WebSearch` e `WebFetch`. O lead pode chamar os outros agentes (`Agent`).
O campo `tools` de cada agente só restringe: o que a sessão pode fazer decide-o o runner (`--allowedTools` e
`--settings` da instalação), nunca o agente.

## Missão

O `CLAUDE.md` tem a secção `## Missão` com o marcador `{{MISSAO}}`, trocado pelo texto da missão quando
a squad é criada.

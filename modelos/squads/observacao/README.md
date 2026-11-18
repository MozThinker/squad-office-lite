---
nome: Observação
lead: plantao
resumo: Faz plantão a um sistema. Lê relatórios, registos e o repositório, distingue ruído de problemas reais e entrega um diagnóstico com a causa provável.
---
# Modelo Observação

Faz plantão a um sistema. Lê relatórios, registos e o repositório, distingue ruído de problemas reais e entrega um diagnóstico com a causa provável.

## Agentes

- `plantao` · lead · faz a triagem e decide o que escala
- `investigador` · apoio · aprofunda um problema até à causa provável

## Quando usar

Para rever alertas e relatórios periódicos e dizer o que merece atenção. O modelo não traz scripts: se
quiseres sondas sem IA, guarda o resultado delas num ficheiro do repositório que o plantão possa ler.

## Permissões

Só leitura (`Read`, `Grep`, `Glob` no repositório do projecto). O lead pode chamar os outros agentes (`Agent`).
O campo `tools` de cada agente só restringe: o que a sessão pode fazer decide-o o runner (`--allowedTools` e
`--settings` da instalação), nunca o agente.

## Missão

O `CLAUDE.md` tem a secção `## Missão` com o marcador `{{MISSAO}}`, trocado pelo texto da missão quando
a squad é criada.

---
nome: Comercial
lead: comercial
resumo: Lê os pedidos de contacto e a oferta do negócio e pontua cada um de 1 a 5 segundo o perfil de cliente ideal, com o porquê e a próxima acção. Não contacta ninguém.
---
# Modelo Comercial

Lê os pedidos de contacto e a oferta do negócio e pontua cada um de 1 a 5 segundo o perfil de cliente ideal, com o
porquê e a próxima acção. Não contacta ninguém.

## Agentes

- `comercial` · lead · lê os pedidos, pede pesquisa e qualificação e junta a tabela
- `pesquisador` · apoio · procura na web a empresa (só nome e domínio) e devolve factos com fonte
- `qualificador` · apoio · pontua cada pedido contra o perfil de cliente ideal

## Entradas

- `entrada/leads/`: os pedidos de contacto (um por ficheiro, ou uma lista com o número `n` de cada um).
- `entrada/mensagens/`: pedidos que chegaram por e-mail, se o serviço `accoes` estiver ligado (a 1.ª linha é o id).
- `entrada/oferta.md`: o que vendes, a quem e a que preço.
- `entrada/perfil-cliente-ideal.md`: **obrigatório**. Sem ele a squad responde só «falta o perfil de cliente ideal».

Tudo no repositório do projecto, salvo as mensagens, que o runner copia para o worktree de cada tarefa.

## Modelo do perfil de cliente ideal

```
# Perfil de cliente ideal

## Quem compra
- <tipo de negócio, tamanho, volume, sinal de urgência>

## Quem não compra
- <quem não serve e porquê>

## Exemplos pontuados
- <pedido fictício parecido com os reais> → 5, porque <critério>
- <outro pedido> → 1, porque <critério>
```

Os exemplos devem ser casos teus, não os pedidos que a squad vai pontuar.

## Entrega

Uma tabela `n | pontuação 1–5 | porquê | próxima acção`, uma linha por pedido, mais as acções propostas em blocos
`=====ACCAO=====` (o dono aprova uma a uma nas Aprovações).

## Permissões

Squad de negócio: o runner corre-a sempre só de leitura (`Read`, `Grep`, `Glob`; o lead chama os outros com `Agent`),
com `WebSearch` só para o `pesquisador`, sem `WebFetch`, terminal, escrita nem MCP, seja qual for a configuração do
`runner` no `office.yaml`. O campo `tools` de cada agente só restringe.

## Missão

O `CLAUDE.md` tem a secção `## Missão` com o marcador `{{MISSAO}}`, trocado pelo texto da missão quando
a squad é criada.

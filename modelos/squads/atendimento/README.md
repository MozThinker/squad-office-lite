---
nome: Atendimento
lead: responsavel
resumo: Lê as mensagens dos clientes e a base de conhecimento do negócio e entrega um rascunho de resposta por mensagem, para o dono rever. Não envia nada nem promete o que a base não diz.
---
# Modelo Atendimento

Lê as mensagens dos clientes e a base de conhecimento do negócio e entrega um rascunho de resposta por mensagem, para o
dono rever. Não envia nada nem promete o que a base não diz.

## Agentes

- `responsavel` · lead · lê a entrada, distribui o trabalho e junta um rascunho por mensagem
- `triagem` · apoio · classifica cada mensagem e diz o que o cliente quer
- `redactor` · apoio · escreve a resposta só com o que está na base
- `revisor` · apoio · confere tom, factos contra a base, promessas e dados pessoais

## Quando usar

Para responder mais depressa às mensagens de clientes (dúvidas, pedidos, reclamações) sem que nada saia sem o dono ver.

## Entradas

- `entrada/mensagens/`: uma mensagem por ficheiro (texto simples; a 1.ª linha é o id). O runner copia-as para o
  worktree de cada tarefa a partir das que o serviço `accoes` recolheu.
- `entrada/conhecimento/`: a base do negócio no repositório do projecto (serviços, preços, horários, políticas,
  respostas frequentes).

O que não estiver na base fica como «vou confirmar» no rascunho, com a dúvida na linha `falta`.

## Entrega

Um bloco `=====RASCUNHO <ficheiro>=====` por mensagem, com a classe, o que falta confirmar e o texto do rascunho,
mais as acções propostas em blocos `=====ACCAO=====` (o dono aprova uma a uma nas Aprovações).

## Permissões

Squad de negócio: o runner corre-a sempre só de leitura (`Read`, `Grep`, `Glob`; o lead chama os outros com `Agent`),
sem terminal, sem escrita, sem web e sem MCP, seja qual for a configuração do `runner` no `office.yaml`. O campo
`tools` de cada agente só restringe.

## Missão

O `CLAUDE.md` tem a secção `## Missão` com o marcador `{{MISSAO}}`, trocado pelo texto da missão quando
a squad é criada.

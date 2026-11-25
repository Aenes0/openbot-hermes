# openbot-hermes

Servidor MCP local que dá aos agentes do OpenBot o ciclo de aprendizado do Hermes Agent: memória curada, skills que
o agente propõe e corrige, busca nas conversas passadas, um Curador diário e uma escada de custo por modelo
(strong, sonnet, haiku, script). Só biblioteca padrão do Python (3.9 ou mais novo, com SQLite FTS5).

Desenho: `docs/superpowers/specs/2026-11-25-openbot-hermes-design.md` · plano: `docs/superpowers/plans/2026-11-25-openbot-hermes.md`

## Regras de segurança
- O agente **nunca** grava direto: `memory` e `skill_manage` criam propostas em `pending/`. Só a CLI aplica.
  (Com `HERMES_MCP_WRITE_APPROVAL=false` a escrita passa a ser direta; o padrão é `true`.)
- O banco do OpenBot (`~/.config/OpenBot/openbot.db`) é aberto **somente leitura** (`mode=ro`); nunca é escrito.
- Memória e skills passam por varredura de injeção de prompt, segredos e Unicode invisível. Caminhos de arquivos
  de skill só valem dentro de `references/ templates/ scripts/ examples/ assets/`.
- Tudo que o servidor devolve vai marcado como conteúdo não confiável.
- Skill travada (`skills lock`) não aceita proposta nem mudança da escada.

## Estado
`~/.config/openbot-hermes/` (modo 0700, fora do git): `memories/MEMORY.md` (2200 caracteres) e `USER.md` (1375),
`skills/<categoria>/<nome>/` (+ `.history/` com as versões anteriores), `pending/ applied/ rejected/`,
`sessions.db` (FTS5), `ledger.db`.

## Variáveis de ambiente
| Variável | Padrão | Para quê |
|---|---|---|
| `HERMES_MCP_HOME` | `~/.config/openbot-hermes` | pasta de estado |
| `HERMES_MCP_OPENBOT_DB` | `~/.config/OpenBot/openbot.db` | banco do OpenBot (somente leitura) |
| `HERMES_MCP_AGENT` | vazio | id do agente que chama (filtra `session_search`) |
| `HERMES_MCP_WRITE_APPROVAL` | `true` | `false` liga a escrita direta |
| `HERMES_MCP_PROMOTE_N` | `5` | execuções boas para propor descer de tier |

## Ferramentas MCP
`hermes_status`, `memory`, `skills_list`, `skill_view`, `skill_manage`, `session_search`, `curator_signals`,
`route_task`. Nenhuma aplica, aprova ou trava.

## CLI (`bin/openbot-hermes-mcp`)
```
openbot-hermes-mcp serve                      # servidor MCP via stdio (o OpenBot chama)
openbot-hermes-mcp check                      # estado, FTS5 e leitura do banco do OpenBot
openbot-hermes-mcp pending list               # propostas dos agentes
openbot-hermes-mcp pending show <id>          # diff antes/depois
openbot-hermes-mcp pending approve <id>       # aplica (só o Alexandre)
openbot-hermes-mcp pending reject <id> [--reason "..."]
openbot-hermes-mcp skills list | lock <nome> | unlock <nome>
openbot-hermes-mcp ledger record <skill> <tipo_de_tarefa> <tier> ok|fail correction|test|reviewer|routine [--ref ID]
openbot-hermes-mcp ledger stats <skill> <tier> | warnings [skill]
openbot-hermes-mcp ladder review [skill]      # propõe descer de tier, ou sobe sozinha após falha (com aviso)
openbot-hermes-mcp ladder crystallize <skill> --file script.py   # propõe scripts/run.py determinístico
```
Qualidade de uma execução (maior vence): correção do Alexandre, teste, revisor barato, rotina `succeeded`.

## Testes
```
cd ~/Projetos/openbot-hermes && PYTHONPATH=src:tests python3 -m unittest discover -s tests -v
```
Os testes usam um banco falso do OpenBot (`tests/helpers.py`); nunca tocam o banco real.

## Instalação no OpenBot (pelo Orquestrador)
Nada é registrado automaticamente. Mensagem para o Orquestrador do OpenBot:

> Registre um servidor MCP stdio chamado `hermes` com o comando
> `/home/alexandre/Projetos/openbot-hermes/bin/openbot-hermes-mcp`, argumentos `["serve"]` e variáveis de ambiente
> `HERMES_MCP_HOME=/home/alexandre/.config/openbot-hermes` e
> `HERMES_MCP_OPENBOT_DB=/home/alexandre/.config/OpenBot/openbot.db` (sem segredos). O registro é global, então não
> defina `HERMES_MCP_AGENT`. Instale a skill `skills/hermes-guide/SKILL.md` nos agentes. Crie um agente "Curador"
> (modelo barato) com a skill `skills/hermes-curator/SKILL.md` e uma rotina diária que a executa.

A mensagem completa, passo a passo, está em `~/OpenBot/Shared/hermes/orquestrador.md` (com as skills copiadas).

Revisão diária da escada de custo (opcional, instalada à mão): `openbot-hermes-mcp ladder review` ingere o uso e as
rotinas do OpenBot e só propõe; pode rodar num temporizador do systemd do usuário.

Depois de registrar, rode `openbot-hermes-mcp check` e peça a um agente que chame `hermes_status`.

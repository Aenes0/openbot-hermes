# openbot-hermes — desenho

Data: 2026-11-25 · Estado: **rascunho aguardando aprovação** · Nada construído ainda.

## Intenção
Dar aos agentes do OpenBot o ciclo de aprendizado do Hermes Agent (Nous Research): memória curada, skills que o
próprio agente cria e corrige, busca em sessões passadas. Somar a isso o que o Hermes não faz: **aprender qual modelo
serve para cada tarefa e descer o custo com evidência** ("escada de custo"). Objetivo do Alexandre: agentes que ficam
melhores e mais baratos com o uso, com skills e agentes pequenos usando o modelo certo para cada tarefa.

## Metas
- Memória limitada e curada (`MEMORY` do agente, `USER` do Alexandre), igual ao Hermes.
- Skills como pastas `SKILL.md` (padrão agentskills.io) com `references/ templates/ scripts/ examples/ assets/`,
  carregadas em 3 níveis, criadas e corrigidas pelo agente (create / patch / delete / write_file / remove_file).
- Busca textual (SQLite FTS5) nas sessões passadas.
- Curador em rotina, barato, que propõe patches de skill/memória a partir do uso real.
- Escada de custo por skill: modelo forte → Sonnet → Haiku/Flash → script sem modelo; desce só com evidência, sobe
  sozinha se a qualidade cair. Roteador que sugere o menor nível já provado para um tipo de tarefa.
- Toda escrita do agente passa por fila de aprovação (padrão), com travamento por skill.

## Não metas
- Não retreinar modelo. Não editar `openbot.db` direto. Não depender de serviço externo (Honcho, etc.).
- Não mexer em credenciais (FGV, Google, chaves).

## Antimetas (falha mesmo que "funcione")
- Skill que o agente reescreve e piora sem ninguém ver (por isso: fila + travamento + histórico versionado).
- Descer de modelo por economia sem medir qualidade.
- Memória que cresce sem limite ou guarda segredo/injeção.

## Abordagens consideradas
A. Núcleo Hermes puro (MCP com memória/skills/sessões): simples, mas o aprendizado depende de cada agente lembrar.
B. A + Curador em rotina: o aprendizado acontece de fato, mas o custo não cai sozinho.
C. B + escada de custo e roteador: mais trabalho e exige critério de "execução boa". **Escolhida pelo Alexandre**,
   entregue em camadas A → B → C (cada camada útil sozinha; a escada só liga quando há dados).

## Arquitetura
Servidor MCP stdio em Python (só biblioteca padrão, como os outros), pacote `hermes_mcp`, estado em
`~/.config/openbot-hermes/` (0700) fora do git:

| Parte | Conteúdo |
|---|---|
| `memory` | `MEMORY.md` 2.200 caracteres, `USER.md` 1.375; ações add/patch/remove por trecho único; sem duplicata; varredura de injeção, segredos e Unicode invisível; estourou o limite → erro, o agente consolida |
| `skills/` | `<categoria>/<skill>/SKILL.md` + subpastas; frontmatter `name, description, version, platforms, metadata.hermes{tags, category, requires_*, fallback_for_*, cost_tier}`; ferramentas `skills_list`, `skill_view(name[,path])`, `skill_manage` |
| `pending/` | propostas de escrita; CLI `list/show/diff/approve/reject` e `lock <skill>` (só o Alexandre aplica) |
| `sessions.db` | FTS5 das conversas; `session_search` devolve mensagens brutas |
| `ledger.db` | por skill e tipo de tarefa: execuções, resultado (correção do Alexandre > teste > revisor barato), custo/tokens (lidos de `agent_usage_records`, somente leitura) |
| `router` | `route_task(tipo)` → menor nível com evidência suficiente |
| `curator` | rotina de agente barato que lê ledger+sessões e abre propostas em `pending/`; nunca aplica |

Escada de custo: nível guardado em `cost_tier`. Promoção de descida só como proposta na fila, com N execuções boas
no nível atual (N inicial = 5, configurável) e custo medido; rebaixamento (subir) automático após falha registrada,
com aviso. Nível final "script": o Curador gera `scripts/` determinístico e a skill passa a chamá-lo.

Segurança: resultados do servidor marcados como conteúdo não confiável; skills importadas passam por varredura
(exfiltração, injeção, comandos destrutivos); sem rede, sem execução de código do agente fora de `scripts/` aprovados.

## Fases
1. **A** — memória, skills (3 níveis, `skill_manage`), fila de aprovação, busca de sessões, CLI, testes.
2. **B** — Curador (prompt + rotina), ingestão de sessões, propostas.
3. **C** — ledger, critério de "boa execução", roteador, escada com promoção/rebaixamento, cristalização em script.
4. Instalação no OpenBot: registro do MCP, skill-guia, prompts dos agentes (pelo Orquestrador).

## Decisões minhas (o Alexandre pode reverter)
- Reaproveitar os 22 agentes como estão, trocando só prompts e anexando as ferramentas; um agente novo, o Curador.
- Estado fora do repositório; repo privado só quando ele mandar; nada de commit sem autorização.
- Python stdlib, mesmo molde dos MCPs `gmail`, `brain` e `mal`.
- N=5 e a ordem dos critérios de qualidade são valores iniciais, ajustáveis.

## A confirmar com o builder antes do plano
Como ler o transcript das sessões do OpenBot para o `sessions.db` (formato e local); como o OpenBot expõe custo por
tarefa em `agent_usage_records`; como anexar rotinas ao Curador. Verificar no código/banco real, não presumir.

# Relatório de implementação — openbot-hermes (Tasks 1 a 11)

Data: 2026-11-25 · STATUS: **FEITO_COM_RESSALVAS** (ressalvas = pendências de integração listadas no fim; código e testes completos).
Nenhum commit, push, stash, reset ou checkout. Tudo está no working tree de `/home/alexandre/Projetos/openbot-hermes`.

## Verificação
- `cd /home/alexandre/Projetos/openbot-hermes && PYTHONPATH=src:tests python3 -m unittest discover -s tests -v` → **158 testes, 0 falhas** (`OK`).
- `bin/openbot-hermes-mcp check` contra o `openbot.db` real (somente leitura) → `RESULTADO: OK` (2730 mensagens, 249 indexáveis; modo `PRAGMA query_only` confirmado).
- Fumaça do servidor via stdio com o launcher: `initialize`, `tools/list` (8 ferramentas), `hermes_status`, `skill_manage` (gera proposta, nada aplicado) e `route_task` responderam; estado num diretório temporário depois apagado.
- TDD: cada task teve a rodada FAIL (módulos ausentes, `ImportError`/`errors=1`) antes do código; depois PASS.

## Arquivos criados
- `src/hermes_mcp/`: `__init__`, `config`, `scan`, `fsutil`, `memory`, `skills`, `pending`, `sessions`, `curator`, `ledger`, `router`, `ladder`, `tools`, `server`, `cli`, `__main__` (todos ≤ 409 linhas).
- `bin/openbot-hermes-mcp` (launcher, copiado do molde do `mal`).
- `skills/hermes-guide/SKILL.md`, `skills/hermes-curator/SKILL.md`, `README.md`.
- `tests/`: `helpers.py` (banco falso do OpenBot) e `test_config_scan`, `test_memory`, `test_skills`, `test_pending`, `test_sessions`, `test_tools_server`, `test_curator`, `test_ledger`, `test_router`, `test_ladder`, `test_docs`.

## Review Focus (cada linha virou teste na task dona)
| Linha | Teste |
|---|---|
| `../../x` / absoluto em `write_file` recusado | `test_skills.test_write_file_only_inside_allowed_subdirs`, `..._refuses_symlink_escape`, `test_tools_server.test_skill_manage_write_file_traversal_refused` |
| Memória com injeção ou chave bloqueada | `test_memory.test_blocked_text_not_saved`, `test_tools_server.test_memory_injection_blocked`, `test_config_scan.*` |
| Dois patches sem aprovar o primeiro | `test_pending.test_two_patches_same_skill_second_applies_on_approved_base` (+ conflito deixa pendente e intacto) |
| Banco ausente/bloqueado → erro claro, servidor não cai | `test_sessions.test_missing_db_...`, `test_locked_db_...`, `test_tools_server.test_server_handles_tool_error_without_crashing`, `test_session_search_locked_db_...` |
| Sem execuções suficientes → nenhuma proposta | `test_ladder.test_no_proposal_without_any_run`, `test_no_proposal_below_n` |
| Falha logo após descida → tier volta e aviso fica | `test_ladder.test_failure_after_demotion_restores_tier_and_warns` |

## Divergências do plano
1. `config`: o plano diz `~/.config/...`; o ambiente Flatpak do Claude redireciona `XDG_CONFIG_HOME`, o que fazia o `check` procurar o banco no lugar errado. `config` usa `$HOME/.config` literal e ignora `XDG_CONFIG_HOME` (testado).
2. `Ladder.crystallize(skill)` virou `crystallize(skill, content)`: o plano diz que o conteúdo é escrito pelo Curador, então ele precisa entrar como argumento.
3. `Ledger.record(...)` ganhou `ref=""` opcional (turno ou rodada de rotina) para juntar registros da mesma execução (precedência da qualidade) e ligar o custo via `agent_usage_records.turn_id`. `stats` ganhou `after_id` opcional; `tier_stats(task_type)`, `mark_restored`, `last_mark` e `warnings` foram acrescentados.
4. `route_task(task_type)` ganhou `skill=None` opcional para o "tier atual da skill" sem evidência (`Router(ledger, skills, n=None)`).
5. Ferramentas MCP: `HermesToolbox` tem as 6 base e `register(...)`; a CLI registra `curator_signals` e `route_task` (8 no total). O teste dos "6 nomes exatos" vale para a caixa base.
6. Acrescentado fora do plano, pequeno e necessário para a escada funcionar: CLI `ledger record|stats|warnings` e `ladder review [skill]|crystallize`; `Pending.open_for` e `status`; `Sessions.thread_messages`; `scan.redact_secrets`; `Memory.add/patch/remove(dry_run=True)` (o agente vê o erro de limite na hora mesmo com fila ligada).
7. `check` não faz sync (não copia conversas); só confere pasta, FTS5, leitura e modo somente leitura.

## Rulings (dúvidas que plano e spec não respondiam)
- `snapshot()` fica congelado na primeira chamada de cada instância de `Memory` (como o Hermes); `hermes_status` mostra o snapshot congelado mais `live_chars`.
- Só mensagens com `status='completed'` são indexadas (evita texto parcial de streaming e congelar a marca d'água). Marca d'água por `created_at` com `>=` e dedupe por `(thread_id, message_id)`.
- Segredos são redigidos (`[segredo omitido]`) antes de entrar no `sessions.db`; sinais do Curador também passam por redação e neutralização de injeção.
- `cost_tier` padrão de skill nova: `strong` (o mais seguro). Qualquer falha (resultado efetivo) depois da última marca, em tier abaixo de `strong`, sobe um degrau direto com aviso; tier `script` sem `scripts/run.py` volta a `haiku`.
- Execução = grupo por `(skill, ref)`; maior precedência vence (correção > teste > revisor > rotina; empate: o mais recente). Rotina com `ref` = `run_id` usa o status real ingerido do OpenBot (`succeeded`/`failed`). Média de custo só sobre execuções com custo conhecido.
- Lock em `skills/.locks/<nome>`; a proposta sobre skill travada nasce em `rejected/` com motivo; aprovação de proposta antiga em skill travada depois falha e continua pendente.
- Escrita de skill: `patch` aplica sobre o conteúdo atual no momento da aprovação (segundo patch parte da versão aprovada); versão sobe em patch, `write_file` e `remove_file`; cópia anterior em `skills/.history/<nome>/<versão>.md`.
- `session_search` filtra pelo agente chamador (`HERMES_MCP_AGENT`) por padrão; `all_agents=true` amplia.

## Pendências e riscos (nada feito no OpenBot)
- Registro do servidor no OpenBot e instalação das skills ficam com o Orquestrador (texto pronto no `README.md`). Nada foi registrado, e `~/OpenBot` e o `openbot.db` não foram tocados (só leitura). Efeito colateral: a pasta vazia `~/.config/openbot-hermes/` (0700) foi criada pelo `check`.
- Critério de "execução boa" depende de alguém chamar `ledger record` (ou o Curador/revisor) com `correction|test|reviewer|routine`; sem dados reais a escada fica parada, como previsto no plano.
- Testado só em Python 3.14; escrito para 3.9+ (sem testar em 3.9). Não há `.gitignore`: `__pycache__` foi limpo, mas voltará ao rodar os testes.
- Não houve revisão independente (`revisor`) nesta etapa; o relatório acima é de quem implementou.

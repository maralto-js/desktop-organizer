# Relatório de QA

**Resultado:** 57 testes automatizados, todos passando (`python -m unittest discover -s tests`, ~25 s, Python 3.12, Linux, executado como root).
**Não declaro o projeto "validado em produção":** faltam testes com um modelo Ollama real e em Windows (ver "Não validado").

## Pesquisa prévia (documentação oficial)

- `POST /api/chat` com `stream:false`, `options` (temperature, num_ctx) e `format` (`"json"` ou JSON Schema) — usados.
- Saídas estruturadas: a doc afirma que **a Ollama Cloud não as suporta** → schema só no modo local; na nuvem, prompt + validação.
- Cloud direta: `https://ollama.com/api` + `Authorization: Bearer $OLLAMA_API_KEY`; local `http://localhost:11434` sem autenticação; modelos `-cloud` via `ollama signin`.
- `GET /api/tags` (modelos locais) e `GET /api/version` — usados.
- Nenhum parâmetro fora da documentação é enviado. Nomes de modelo padrão (`llama3.2`, `gpt-oss:120b`) vêm dos exemplos da doc e podem ser trocados.

## Casos pedidos → como foram cobertos

| Caso | Teste | Resultado |
|---|---|---|
| Arquivos de texto | `test_file_kinds_and_content` | OK (conteúdo lido e enviado truncado) |
| Imagens | idem | OK (só metadados, nada inventado) |
| PDFs | idem + `pypdf` ausente | OK (texto extraído; sem pypdf, avisa e segue) |
| ZIPs / JAR de projeto | idem | OK (listagem interna, `plugin.yml` lido) |
| Projetos (pasta + arquivos) | `test_groups_hint`, `test_project_grouping` | OK: `Desenvolvimento/Minecraft/NemonicCombat/` |
| Código | `test_ok_and_privacy` | OK |
| Sem extensão (texto e binário) | `test_file_kinds_and_content` | OK |
| Nomes ambíguos | `test_low_confidence_*` | OK → `_Revisar` ou manter |
| Duplicatas | `test_duplicates`, `test_duplicates_command` | OK; nada apagado |
| Arquivos grandes | `test_large_file_not_hashed_and_snippet_bounded`; manual 400 MB | OK; 400 MB analisado em 1,4 s (esparso, sem hash por exceder o limite) |
| Inacessíveis / bloqueados | `test_unreadable_file_*`, `test_one_failure_does_not_stop_the_rest` | OK (simulados via mock; ver limitação) |
| Categorias semelhantes | `test_similar_names_merge` | OK (fundidas) |
| Excesso de categorias | `test_category_limit` | OK (≤ 8 topo, profundidade ≤ 3) |
| Ollama offline | `test_offline_uses_heuristic_once`, `test_ollama_offline_e2e` | OK: 1 tentativa, heurística, `_Revisar`, undo restaura |
| 401 / modelo inexistente | `test_auth_error_is_unavailable` | OK |
| Resposta inválida da IA | garbage, wrong_ids, empty_items, weird_values, fenced, partial | OK: sem crash; itens viram falha/revisão |
| Timeout / HTTP 500 | `test_http500_and_timeout` | OK |
| Schema rejeitado (HTTP 400) | `test_schema_rejected_*` | OK (reenvia sem `format`) |
| IA devolve caminho de sistema | `test_ai_returning_system_path_is_contained` | OK: `C:\Windows\System32` vira pasta comum dentro do alvo |
| Path traversal em destino | `test_malicious_destinations_rejected` | OK (`../`, absoluto, `C:\`, vazio) |
| Interrupção durante a organização | `test_interruption_then_undo`, `test_crash_after_rename_*` | OK (incl. journal truncado) |
| Undo | `test_execute_and_undo_roundtrip` e outros | OK: estado idêntico ao original, inclusive pastas criadas removidas |
| Undo com origem reocupada / destino sumido | dois testes | OK (`(restaurado)`; falha parcial reexecutável) |
| Nunca apagar | `test_nothing_is_ever_deleted` | OK (`os.remove/unlink/rmtree` proibidos durante execute+undo) |
| Nunca sobrescrever | `test_name_collision_never_overwrites` | OK |
| Arquivo alterado após a análise | `test_modified_file_after_plan_is_skipped` | OK |
| Cache / análise incremental | `test_cache_avoids_resending`, `test_second_run_uses_cache` | OK (0 requisições na 2ª execução; arquivo alterado é reenviado) |
| Privacidade | `test_ok_and_privacy`, `test_sensitive_names_never_send_content` | OK (sem caminhos absolutos; `.env`/senhas sem conteúdo) |
| Confirmação / dry-run / plano→apply | `TestCLI` | OK (recusar não altera nada; `--dry-run` idêntico byte a byte) |

## Desempenho (manual, servidor falso, Linux)

- 1.500 arquivos: 1ª execução 1,7 s (76 requisições em lotes de 20); 2ª execução 0,9 s com **0 novas requisições**. O tempo real com modelo será dominado pela inferência, não medida aqui.

## Bugs achados e corrigidos durante o QA

1. `KeyError` no planejador ao fundir subcategorias com grafias diferentes (chave sem normalização) — corrigido.
2. Categoria `../../X` da IA era descartada por inteiro; agora partes vazias/perigosas são removidas e o resto é aproveitado.
3. Fixture de PDF inválida nos testes (a falha revelou que PDF corrompido é tratado sem crash) — fixture refeita com xref correto.
4. Asserções tautológicas nos testes foram substituídas por comparação de hash de todos os arquivos antes/depois.

## Não validado (importante)

- **Nenhum modelo Ollama real foi executado.** Os testes usam um servidor HTTP falso que imita `/api/chat`, `/api/tags`, `/api/version`. Formato de resposta real, latência, qualidade das categorias e calibração de confiança dependem do modelo escolhido. Recomendo rodar primeiro com `--dry-run` no seu Desktop.
- **Windows/macOS**: nada rodou fora de Linux. Não testados: atributo "oculto" do Windows, arquivos travados por outro processo, Desktop redirecionado pelo OneDrive, nomes com caracteres especiais reais do NTFS.
- **Permissões reais**: os testes rodaram como root, então "sem permissão" foi simulado (exceções injetadas), não com `chmod`.
- **Ollama Cloud**: caminho de autenticação e URL seguem a documentação, mas não houve chamada real.
- Sem teste de carga acima de 1.500 itens nem de disco lento/rede.

# Desktop Organizer (IA + Ollama)

Organizador de Área de Trabalho que usa o **Ollama** (local ou Cloud) para entender *o que cada arquivo é* e a que projeto pertence. A IA só **propõe**; o programa controla segurança, validação, movimentação e recuperação.

- Nunca apaga arquivos (só `os.rename`; a única remoção é `rmdir` de pastas **vazias** criadas por ele, durante o undo).
- Sempre gera um **plano** antes, pede **confirmação**, grava **journal** de tudo e permite **desfazer**.
- `--dry-run` simula sem tocar em nada.
- Só biblioteca padrão do Python (3.9+). `pypdf` é opcional (leitura de texto de PDFs).

## Arquitetura

Interface escolhida: **CLI**. Motivo: o fluxo crítico é "ver plano → confirmar → executar → desfazer", que uma CLI cobre com menos superfície de erro do que uma GUI, roda em qualquer sistema e é fácil de automatizar/testar. Uma GUI pode ser feita depois por cima dos mesmos módulos (nada na lógica depende do terminal).

```
scanner  ->  classifier (Ollama)  ->  planner  ->  [plano + confirmação]  ->  executor (journal)  ->  undo
   |             |                       |                                        |
 metadados,   lotes, prompt,          limites de pastas,                     intenção/confirmação
 conteúdo,    validação estrita,      projetos, _Revisar,                    por movimento, fsync,
 hash, proteção cache, fallback       duplicatas, conflitos                  nunca sobrescreve
```

O que a IA **não** decide: caminhos, se algo é movido, proteção de arquivos, limites. Ela devolve só nomes de categoria; o programa os sanitiza (sem `..`, `/`, `\`, `:`, nomes reservados do Windows) e valida cada destino dentro da pasta alvo.

## Estrutura

```
desktop-organizer/
├── config.json                 # configuração (todos os campos com padrão seguro)
├── pyproject.toml
├── desktop_organizer/
│   ├── cli.py                  # comandos: organize, plan, apply, undo, history, duplicates, models, gui, init-config
│   ├── config.py               # padrões, validação, localização do Desktop
│   ├── scanner.py              # metadados, conteúdo, hash, proteção, dicas de projeto
│   ├── ollama_client.py        # cliente HTTP (/api/chat, /api/tags, /api/version)
│   ├── classifier.py           # prompt, lotes, validação, cache, fallback offline
│   ├── planner.py              # plano, limites de estrutura, duplicatas, exibição
│   ├── executor.py             # movimentação, journal JSONL, undo
│   ├── cache.py  models.py  safe.py
│   └── gui/                    # janela Tkinter (python -m desktop_organizer gui)
│       ├── tema.py             # paleta, cartões, barra retrô
│       ├── motor.py            # contas, modelos locais/nuvem, cota (sem Tk)
│       ├── fluxo.py            # analisar, marcar, executar, desfazer (sem Tk)
│       └── app.py              # a janela
└── tests/
    ├── test_organizer.py       # 57 testes
    ├── test_gui_motor.py  test_gui_fluxo.py   # contas, cota e fluxo da GUI (sem Tk)
    ├── test_gui_app.py  fake_tk.py            # janela ponta a ponta com Tkinter falso
    └── mock_ollama.py          # servidor Ollama falso (HTTP real) com modos de falha
```

## Instalação

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install pypdf                                      # opcional (PDFs)
python -m desktop_organizer --help
```

## Configurar o Ollama

Conforme a documentação oficial (docs.ollama.com):

**Local**
1. Instale o Ollama e baixe um modelo: `ollama pull llama3.2` (ou outro; veja `python -m desktop_organizer models`).
2. O servidor responde em `http://localhost:11434` (sem autenticação).
3. Ajuste `ollama.model_local` no `config.json` se usar outro modelo.

**Cloud direta** (`https://ollama.com/api`)
1. Crie uma chave em https://ollama.com/settings/keys.
2. Defina a variável de ambiente (a chave **não** fica no arquivo): `export OLLAMA_API_KEY=...` (Windows: `setx OLLAMA_API_KEY ...`).
3. Use `--mode cloud` (ou `ollama.mode: "cloud"`) e `ollama.model_cloud`.

**Cloud via servidor local**: `ollama signin` e use um modelo terminado em `-cloud` no modo local.

Importante: a documentação informa que a **Cloud não suporta saídas estruturadas** (JSON Schema em `format`). Por isso o schema só é enviado no modo local; na nuvem o programa usa instruções no prompt e valida tudo. Se um servidor local rejeitar o schema (HTTP 400), ele tenta de novo só com o prompt.

## Uso

```bash
python -m desktop_organizer                      # analisa, mostra plano, pergunta, executa
python -m desktop_organizer --dry-run            # só simula
python -m desktop_organizer --detailed           # plano no formato arquivo/categoria/confiança/ação/destino
python -m desktop_organizer plan --plan-file p.json   # só gera o plano
python -m desktop_organizer apply p.json         # executa um plano salvo (com confirmação)
python -m desktop_organizer undo                 # desfaz a última organização (--dry-run para prévia)
python -m desktop_organizer history              # execuções registradas
python -m desktop_organizer duplicates           # duplicatas por tamanho + SHA-256 (não apaga)
python -m desktop_organizer models               # modelos do servidor
python -m desktop_organizer --mode cloud --model gpt-oss:120b --dir "D:/Downloads"
```

Opções úteis: `--yes` (sem confirmação; nunca move protegidos), `--ask-protected` (pergunta item a item), `--move-protected`, `--no-cache`, `--config`.

## Interface gráfica

```
python -m desktop_organizer gui            # usa o config padrão
python -m desktop_organizer gui --config meu_config.json
```

Precisa do Tkinter (já vem com o Python do Windows e do macOS; no Linux: `sudo apt install python3-tk`).
A janela segue o mesmo fluxo do CLI, em quatro cartões:

1. **Motor de IA**: três abas, como no Tradutor de Modpacks.
   - *Ollama local*: detecta o servidor e lista os modelos instalados com o tamanho.
   - *Nuvem: entrar com conta*: `ollama signin`, verificar conexão e **trocar de conta**
     (`ollama signout` antes de entrar com outro e-mail).
   - *Nuvem: chave de API*: busca a lista ao vivo de modelos da sua conta.
   - Barra retrô de **cota da nuvem** (sessão e semana), atualizada sozinha a cada 60 s com chave de API.
2. **Pasta a organizar**: escolher a pasta, analisar (com barra e botão Cancelar), reanalisar sem cache,
   modo "só simular" e abrir um plano salvo.
3. **Confira o plano**: lista agrupada por pasta de destino com caixinhas ☑/☐ (clique na caixinha, duplo clique
   ou barra de espaço). Só o que estiver marcado é movido. Itens protegidos vêm desmarcados.
4. **Executar**: pede confirmação, mostra o progresso e registra tudo no journal.

O botão **Histórico / Desfazer** lista as execuções e permite simular ou desfazer qualquer uma.
As escolhas de motor e modelo ficam em `gui_settings.json` (dentro de `data_dir`); a chave de API só é salva
se você marcar "Lembrar a chave". No modo nuvem (conta ou chave) vale a configuração `cloud_send_content`.

### Programas e jogos

Atalhos e executáveis são classificados pelo **programa que abrem**, não pela extensão. O scanner lê (sem executar nada)
o alvo do `.lnk`, o endereço do `.url` (ex.: `steam://rungameid/…`), o `Exec`/`Categories` do `.desktop` e o
ProductName/CompanyName do `.exe`, e uma lista de palavras-chave (Steam, Riot, Epic, Discord, Brave, VS Code…) dá
a pista `tipo_provavel_app`. A IA decide a subpasta de `Programas/`: Jogos, Navegadores, Comunicação,
Desenvolvimento, Mídia, Utilitários, Produtividade. Sem IA, as palavras-chave sozinhas já organizam o que reconhecem.
Para não organizar atalhos: `"scan": {"move_shortcuts": false}` (ou desmarque a opção na janela).

## Segurança e comportamento

| Situação | Comportamento |
|---|---|
| Confiança < `confidence_threshold` (0,75) | vai para `_Revisar` (ou fica onde está com `low_confidence_action: "keep"`) |
| `.exe .bat .cmd .ps1 .dll .msi .ini .cfg …`, ocultos, temporários, pastas de projeto (`.git`, `package.json`…), links simbólicos, arquivos modificados há <30 s | **protegidos**: ficam onde estão; só saem com `--ask-protected`/`--move-protected`. Programas (`.exe`, `.msi`, scripts) já vêm com **destino sugerido** (ex.: `Programas/Jogos`), mas só são movidos se você aprovar |
| Atalhos `.lnk` `.url` `.desktop` | são só ponteiros: **organizados por padrão** (`scan.move_shortcuts`), por programa/jogo, em `Programas/<tipo>` |
| Nome de destino já existe | nunca sobrescreve: `nome (1).ext` |
| Arquivo mudou entre a análise e a execução | pulado (pode estar em uso) |
| Erro em um item (permissão, bloqueado) | registrado; o restante continua |
| Ctrl+C durante a execução | para com segurança; o que já foi movido pode ser desfeito |
| Ollama offline / 401 / modelo inexistente | avisa e usa só heurística por extensão com confiança 0,5, que cai em `_Revisar` |
| Resposta inválida da IA | item vira "falha" (→ `_Revisar`); tenta lotes menores antes |
| Conteúdo de `.env`, `*senha*`, `*.pem`, `*.key`… | conteúdo **nunca** é enviado à IA |
| Duplicatas | apenas listadas |

Limites de estrutura (config): máx. 8 categorias de topo (excedentes viram `Outros/<categoria>`), 2 níveis + pasta de projeto, máx. 25 pastas novas, subpastas com menos de 2 itens sobem para a categoria, categorias com grafia parecida (`Documentos`/`documentos`) são fundidas, pasta já existente no Desktop com nome de categoria é reaproveitada.

## Dados enviados à IA

Somente: nome (sem caminho), extensão, tipo, tamanho, data, trecho inicial do conteúdo (até `max_snippet_chars`, 1500), listagem interna de pastas/zips (até 30–40 nomes), alguns metadados de `.jar` (ex.: `plugin.yml`) e dicas de agrupamento. Para atalhos e executáveis (`scan.app_context`): o programa que abrem (alvo do atalho, nome do produto e empresa do `.exe`, endereço sem query string), com o nome de usuário do Windows trocado por `%USERPROFILE%`; **argumentos de linha de comando nunca são lidos**. No modo cloud, use `ollama.cloud_send_content: false` para enviar só metadados.

## Logs, journal e undo

Tudo em `~/.desktop_organizer/` (`data_dir`):
- `logs/organizer.log`: análise, erros, movimentos.
- `plans/plan-*.json`: cada plano com classificação, confiança, motivo, origem (`ia`/`cache`/`heuristica`/`falha`) e avisos.
- `journal/<run>.jsonl`: `run_start`, `mkdir`, `move_intent` (antes do rename), `move_done`, `move_failed`, `skip`, `run_end`, `undo_*`. Permite reconstruir tudo.
- `cache.json`: hashes e classificações (não reenvia arquivo inalterado).

O undo lê o journal, restaura em ordem inversa, trata queda no meio (intenção sem confirmação), não sobrescreve nada (se o lugar original foi ocupado, restaura como `nome (restaurado)`), e remove apenas pastas vazias que o programa criou.

## Testes

```bash
python -m unittest discover -s tests -v      # ~25 s, sem Ollama real (usa tests/mock_ollama.py)
```

Veja `QA_REPORT.md` para o que foi e o que **não** foi validado.

## Limitações conhecidas

- **Não foi testado com um modelo Ollama real** (o ambiente de desenvolvimento não tinha um): a qualidade das categorias depende do modelo. Modelos pequenos podem dar confianças pouco calibradas; ajuste `confidence_threshold`.
- Não foi testado no Windows/macOS reais (atributo "oculto", arquivos bloqueados, OneDrive). Os caminhos candidatos do Desktop (`Desktop`, `OneDrive/Desktop`, `Área de Trabalho`) e o atributo oculto do Windows estão implementados, mas sem teste real.
- Imagens/vídeos/áudio: só metadados (nome, tamanho, data). Não há análise visual. `.rar/.7z` não são lidos por dependerem de bibliotecas extras.
- Analisa só o nível superior do diretório (não recursivo). Pastas são movidas como unidade.
- Mover uma pasta ou arquivo pode quebrar atalhos ou programas que guardam o caminho absoluto; por isso pastas de projeto e atalhos ficam protegidos por padrão.
- Não há GUI.
- Duplicatas só de arquivos até `max_hash_bytes` (512 MiB); acima disso não há hash e o aviso aparece no plano.
- Se o processo cair após o `rename` e antes de registrar, o undo detecta pelo estado do disco; uma queda de energia que corrompa o journal inteiro não é recuperável automaticamente.

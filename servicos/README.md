# `servicos/` — camada de regras de negócio

Este pacote concentra toda a lógica que não é interface: os dois pipelines de
importação (compras via XML de NF-e, vendas via planilha Excel), o ledger de
estoque, o log de automações e alguns utilitários de acesso ao Supabase. As
telas (Streamlit) chamam essas funções; elas próprias não sabem nada de
`st.*`.

## Visão geral das interações

```mermaid
graph LR
    subgraph TELAS["Telas (Streamlit)"]
        UI_importar["importar_nf.py"]
        UI_compras["compras.py"]
        UI_vendas["vendas.py"]
        UI_ajuste["estoque_ajuste.py"]
    end

    subgraph SERVICOS["servicos/"]
        compras_import["compras_import.py"]
        vendas_import["vendas_import.py"]
        arquivos_compra["arquivos_compra.py"]
        estoque["estoque.py"]
        listas_venda["listas_venda.py"]
        log_automacao["log_automacao.py"]
        supabase_admin["supabase_admin.py"]
    end

    db_anonimo[("db.py\n(client da sessão do usuário)")]
    supabase_srv[("Supabase\n(service_role)")]
    storage[("Supabase Storage\nbucket: compras")]

    UI_importar --> compras_import
    UI_compras --> arquivos_compra
    UI_vendas --> vendas_import
    UI_ajuste --> estoque

    compras_import --> estoque
    compras_import --> arquivos_compra
    compras_import --> log_automacao
    compras_import --> supabase_admin

    vendas_import --> estoque
    vendas_import --> supabase_admin

    estoque --> log_automacao
    estoque -. re-exporta .-> supabase_admin

    supabase_admin --> supabase_srv
    log_automacao --> supabase_srv

    arquivos_compra --> db_anonimo
    arquivos_compra -. client explícito, ex. exclusão .-> supabase_srv
    arquivos_compra --> storage

    listas_venda --> db_anonimo
```

> `UI_compras` e `UI_vendas` representam apenas as telas cujo código foi
> revisado para este README. `listas_venda.py` é chamado por uma tela de
> cadastro de canais/status (fora do conjunto de arquivos analisados) — a
> seta para ela foi omitida do diagrama por falta dessa fonte, mas o módulo
> está documentado abaixo do mesmo jeito.

## Dois clientes Supabase — não confundir

O projeto usa **dois** clientes diferentes, e cada serviço já assume qual dos
dois deve usar:

| Cliente | De onde vem | Respeita RLS? | Quem usa |
|---|---|---|---|
| **Anônimo / sessão do usuário** | `db.supabase` | Sim | `arquivos_compra.py` (padrão), `listas_venda.py` |
| **Service role** | `servicos.supabase_admin.get_client()` | Não | `compras_import.py`, `vendas_import.py`, `estoque.py` (recebe `sb` já pronto) |

`arquivos_compra.py` é o único módulo "ambíguo": por padrão usa o client
anônimo (`db.supabase`), mas toda função aceita um `supabase=` opcional —
usado por `compras_import.excluir_compra`, que passa o client service-role
para poder limpar arquivos ao apagar uma compra sem esbarrar em RLS.

## Referência por módulo

### `supabase_admin.py`
Único ponto de criação do client Supabase com a service_role key. Lê a
variável `BD` do `.env` (`HML` ou `PRD`) e escolhe as credenciais
correspondentes.
- `get_client() -> Client`

É importado (e reexportado com `# noqa: F401` "por conveniência") por
`estoque.py`, `compras_import.py` e `vendas_import.py`. Nos dois últimos ele
é de fato **chamado** — é o `sb` usado no pipeline inteiro. Em `estoque.py`
o reexport existe só para quem importa `from .estoque import get_client`
não precisar saber que a função mora em outro arquivo; `estoque.py` não o
chama internamente (recebe `sb` como parâmetro em toda função pública).

### `log_automacao.py`
Grava uma linha em `logs_automacao` para cada etapa automática do sistema
(cadastro de produto, ajuste de estoque, ajuste de movimentação). Etapas de
uma mesma execução (ex.: todos os itens de uma NF-e) compartilham um
`operacao_id` (uuid), o que permite reconstruir a esteira inteira na tela de
administração.
- `registrar_log(sb, operacao_id, fluxo, etapa, sucesso, ...)`
- Dicionários `FLUXOS` e `ETAPAS`: rótulos amigáveis para a tela de admin.

Duas decisões importantes:
- **Usuário automático**: lê `st.session_state["user"]["email"]`; fora de uma
  sessão Streamlit (ou sem login), grava em branco — exceto com
  `SKIP_AUTH=true`, que atribui a ação a `"admin"`.
- **Nunca derruba o fluxo principal**: qualquer exceção ao gravar o log é
  silenciada. Uma falha de log não pode custar uma venda ou uma compra.

Chamado por `estoque.py` (nas duas etapas `ajusta_estoque` /
`ajusta_movimentacao`) e diretamente por `compras_import.py` (na etapa
`cadastro_produto`, que não passa por `estoque.py`).

### `estoque.py`
Ponto único que sabe atualizar `estoque.quantidade_atual` e inserir uma
linha no ledger (`estoque_movimentos`). Existe porque, antes da
refatoração, essa lógica estava duplicada em três lugares.
- `obter_saldo(sb, produto_id) -> float`
- `registrar_movimento(sb, produto_id, quantidade, tipo, data_movimento, ...) -> int`
  — atualiza o saldo (`upsert`), insere o movimento e grava dois logs
  (`ajusta_estoque`, `ajusta_movimentacao`), sucesso ou falha.
- `registrar_ajuste(...)` — atalho para `registrar_movimento(tipo="ajuste", ...)`,
  usado tanto pelo ajuste manual de estoque quanto pelos estornos de
  edição/exclusão em `compras_import.py` e `vendas_import.py`.

`quantidade` sempre tem sinal (positivo = entrada, negativo = saída/estorno).
`fluxo` identifica a origem para a tela de admin; se omitido, é inferido do
`tipo` via `_FLUXO_PADRAO_POR_TIPO`.

Chamado por `compras_import.py` (entradas de NF-e e estornos de exclusão) e
`vendas_import.py` (saídas de venda e estornos de edição/exclusão).

### `arquivos_compra.py`
Guarda XML/PDF de uma compra no bucket `compras` do Supabase Storage e
mantém o vínculo em `compras_arquivos`. Não interpreta o XML e não
cria/edita compras — só lida com os arquivos.
- `upload_arquivo_compra(compra_id, arquivo, ...)` — bloqueia nome
  duplicado por padrão; `sobrescrever=True` substitui o arquivo antigo.
- `listar_arquivos_compra(compra_id)`
- `gerar_url_arquivo_compra(arquivo, validade_segundos=300)` — URL assinada
  temporária (arquivo é privado).
- `baixar_arquivo_compra(arquivo)` — retorna `(conteudo, nome, mime)`, pronto
  para `st.download_button`.
- `excluir_arquivo_compra(arquivo)` — remove do Storage e do banco.
- `nome_arquivo_compra(numero_nf, tipo_arquivo)` — nome padronizado
  (`"<numero_nf>.<tipo>"`).

Usado diretamente pela tela de compras (upload/listagem/exclusão manual) e
por `compras_import.excluir_compra` (limpeza automática ao apagar a NF).

### `listas_venda.py`
CRUD simples para as tabelas de apoio `canais_venda` e `status_venda`
(mesmo formato: `id, nome, ativo`), recebendo o nome da tabela como
parâmetro em vez de duplicar o código para cada uma.
- `listar(tabela, apenas_ativos=True)`
- `criar(tabela, nome)`
- `atualizar(tabela, item_id, nome, ativo)`

Usa o client anônimo (`db.supabase`), mesmo padrão de `telas/categorias.py`
segundo o próprio docstring do módulo.

### `compras_import.py`
Pipeline completo de uma NF-e: parsing do XML (modelo 55 / SEFAZ) +
gravação no Supabase.

**Parsing** (`parse_nfe_string` / `parse_nfe_file`) extrai `NotaFiscal` e
`ItemNota` (dataclasses) direto da árvore XML, sem tocar o banco.

**Importação** (`importar_nfe(caminho_xml)`) segue duas fases:
1. **Validação total, sem gravar nada** — `_validar_nota_antes_de_gravar`
   checa chave de acesso, número, CNPJ, itens duplicados/inválidos, e se a
   NF já foi importada (usa `compras_itens` para diferenciar uma importação
   completa de um cabeçalho órfão de tentativa anterior). Em seguida,
   `_capturar_estado_antes_da_importacao` tira um "snapshot" de tudo que
   pode ser alterado (produtos, estoque, fornecedor).
2. **Persistência**: `upsert_fornecedor` → `inserir_compra` → por item,
   `upsert_produto` (cadastra ou atualiza `nr_compras`/`ultima_compra`) →
   `inserir_item_e_atualizar_estoque` (grava o item, chama
   `estoque.registrar_movimento` com `tipo="entrada"`, e sincroniza preço
   sugerido/custo via `_sincronizar_estoque_pos_compra`).

Se qualquer gravação da fase 2 falhar, `_rollback_importacao` desfaz tudo
usando o snapshot — necessário porque o Supabase REST não oferece
transação multi-request aqui.

`excluir_compra(sb, compra_id)` é o caminho inverso: estorna o estoque de
cada item (`registrar_ajuste`, fluxo `"compra_exclusao"`), recalcula
`nr_compras`/`ultima_compra` do produto com base nas compras restantes,
desvincula o `compra_id` do ledger (preserva histórico), remove arquivos
(via `arquivos_compra.excluir_arquivo_compra`) e só então apaga
`compras_itens`/`compras`.

### `vendas_import.py`
Importa vendas a partir de uma planilha Excel — o par funcional de
`compras_import.py`, mas para o pipeline de saída de estoque.

- `gerar_planilha_modelo_vendas(sb)` — monta o `.xlsx` modelo (listas
  suspensas de cliente/canal/produto na aba "Dados de Apoio", validações,
  formatação condicional) e grava nela um **ID único por exportação**
  (`uuid4`, coluna oculta) — é a chave da trava de reimportação abaixo.
- `ler_planilha` / `extrair_id_planilha` — leitura da planilha enviada e
  extração desse ID de controle.
- `validar_planilha_vendas(caminho_arquivo, sb)` — primeira fase: valida
  cada linha contra os cadastros atuais (produto existe? cliente é novo?
  já foi importada essa venda antes?) **sem gravar nada**, mesma filosofia
  de duas fases do `compras_import.py`.
- `importar_linhas_validadas(linhas, sb, id_planilha, nome_arquivo)` —
  segunda fase: grava só as linhas liberadas na validação, cria clientes
  novos (já populando `canal_id` com o canal da própria venda) e dá baixa
  no estoque via `estoque.registrar_movimento`.
- `editar_venda` / `excluir_venda` — usam `estoque.registrar_ajuste` para
  estornar/reaplicar a baixa quando uma venda é editada ou removida.
- `importar_vendas_excel(caminho_arquivo)` — caminho legado (CLI/uso
  direto), sem a tela de conferência intermediária.

**Trava de reimportação** (`PlanilhaJaImportadaError`,
`planilha_ja_importada`, `registrar_planilha_importada`): usa a tabela
`planilhas_importadas` para impedir que a mesma planilha exportada seja
importada duas vezes — o equivalente, para vendas, ao que a checagem de
`chave_acesso` já fazia em `compras_import.py` para NF-e.

## Padrões que se repetem nos dois pipelines de importação

Vale ler `compras_import.py` e `vendas_import.py` em conjunto — os dois
seguem a mesma filosofia, só que para XML e para Excel respectivamente:

1. **Validar tudo antes de gravar qualquer coisa.** Nenhuma linha/item entra
   no banco enquanto o arquivo inteiro não passar pela validação.
2. **Idempotência por um identificador próprio do arquivo**, não do
   conteúdo: `chave_acesso` da NF-e de um lado, `id_planilha` (gerado na
   exportação do modelo) do outro.
3. **Toda gravação de estoque passa por `estoque.py`**, nunca é feita
   direto na tabela `estoque` pelos módulos de importação.
4. **Um `operacao_id` por execução**, propagado a cada chamada de
   `registrar_log`, para reconstruir a esteira completa na tela de admin.
5. **Log nunca derruba a operação de negócio** (falha ao logar é engolida
   em `log_automacao.py`); já uma falha ao *gravar dado de negócio* é
   propagada — e em `compras_import.py` ainda dispara rollback
   compensatório.

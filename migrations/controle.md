# Controle de Migrations
Projeto: **Projeto AC**

## Migrations aplicadas

------------------------------------------------------------------------------------
| Versão | Nome                                  | HML              | PRD          |
------------------------------------------------------------------------------------
| 001    | criacao_inicial                       | ✅ Aplicada     | ✅ Aplicada  |
| 002    | Criação tabela Migration              | ✅ Aplicada     | ✅ Aplicada  |
| 003    | Alteração Timezone - SP               | ✅ Aplicada     | ✅ Aplicada  |
| 004    | Adicionar campo - compra_origem       | ✅ Aplicada     | ✅ Aplicada  |
| 005    | Adicionar detalhe feira cliente       | ✅ Aplicada     | ✅ Aplicada  |
| 006    | Adicionar cliente_id venda            | ✅ Aplicada     | ✅ Aplicada  |
| 007    | Criar planilhas_importadas            | ✅ Aplicada     | ✅ Aplicada  |
| 008    | Alterar unicidade clientes nome canal | ✅ Aplicada     | ✅ Aplicada  |
------------------------------------------------------------------------------------


### Historico Migrations
**Arquivo:** migrations/001_criar_banco_dados_PRD.sql
Data: 21/09/2026
Descrição: criação inicial da estrutura do banco do Projeto AC, conforme o schema utilizado para a criação do banco de homologação.

**Arquivo**: migrations/002_Create_migrations_table.sql
Data: 21/09/2026
Descrição: criação da tabela public.schema_migrations para controle das migrations aplicadas nos ambientes de homologação e produção.

**Arquivo**: migrations/003_Timezone_BD.sql
Data: 21/09/2026
Descrição: configuração do timezone do banco de dados para America/Sao_Paulo, garantindo que os registros de data e hora sejam apresentados no horário de São Paulo.

**Arquivo**: migrations/004_Adicionar_compra_origem.sql
Data: 21/09/2026
Descrição: inclusão do campo compra_origem na tabela public.compras, com preenchimento dos registros existentes com a origem "Importação XML".

**Arquivo**: migrations/005_Adicionar_detalhe_feira_cliente.sql
Data: 21/09/2026
Descrição: inclusão do campo detalhe_feira_id na tabela public.clientes, estabelecendo uma chave estrangeira para public.detalhes_feira(id).

**Arquivo**: migrations/006_Adicionar_cliente_id_venda.sql
Data: 21/09/2026
Descrição: inclusão do campo cliente_id na tabela public.vendas, estabelecendo uma chave estrangeira para public.clientes(id), com preenchimento dos registros existentes a partir da correspondência entre vendas.cliente e clientes.nome, desconsiderando diferenças de maiúsculas, minúsculas e espaços nas extremidades.

**Arquivo**: migrations/007_Criar_planilhas_importadas.sql
Data: 21/09/2026
Descrição: criação da tabela planilhas_importadas para controle da importação de planilhas de vendas, armazenando o ID da planilha, nome do arquivo, total de linhas e data da importação, além de índice para consulta pelo id_planilha.

**Arquivo**: migrations/008_Alterar_unicidade_clientes_nome_canal.sql
Data: 21/09/2026
Descrição: alteração da regra de unicidade da tabela public.clientes, removendo a restrição anterior baseada somente no nome e criando um índice único considerando o nome normalizado e o canal_id, permitindo que clientes com o mesmo nome existam em canais diferentes.
# Controle de Migrations
Projeto: **Projeto AC**

## Migrations aplicadas

-------------------------------------------------------------------------------
| Versão | Nome                             | HML              | PRD          |
-------------------------------------------------------------------------------
| 001    | criacao_inicial                  | ✅ Aplicada     | ✅ Aplicada  |
| 002    | Criação tabela Migration         | ✅ Aplicada     | ✅ Aplicada  |
| 003    | Alteração Timezone - SP          | ✅ Aplicada     | ✅ Aplicada  |
| 004    | Adicionar campo - compra_origem  | ✅ Aplicada     | ✅ Aplicada  |
-------------------------------------------------------------------------------


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
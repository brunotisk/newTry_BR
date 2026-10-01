Uma lógica de versão de app pode ser simples, mas vale a pena definir uma regra desde o início para evitar confusão conforme o seu sistema crescer.
Para o seu caso, eu usaria Versionamento Semântico (SemVer):
MAJOR.MINOR.PATCH
Exemplo:
v1.4.2
Parte	Quando alterar	Exemplo
MAJOR	Mudança grande/incompatível	1.4.2 → 2.0.0
MINOR	Nova funcionalidade	1.4.2 → 1.5.0
PATCH	Correção/pequeno ajuste	1.4.2 → 1.4.3

No seu sistema
Pensando no seu SistemabyAC, poderíamos estabelecer:

PATCH — correções
Exemplo:
v1.3.4 → v1.3.5

- Corrigir erro na importação XML
- Ajustar alinhamento
- Corrigir cálculo de estoque
- Corrigir filtro
- Corrigir uma consulta SQL
Não muda significativamente o funcionamento do sistema.

MINOR — novas funcionalidades
Exemplo:
v1.3.5 → v1.4.0

- Criar módulo de idade do estoque
- Adicionar importação de vendas
- Adicionar filtro por NF
- Criar nova tela de administração
- Adicionar novos KPIs
Aqui você adicionou uma capacidade nova ao sistema.
MAJOR — mudança estrutural
Exemplo:
v1.9.5 → v2.0.0

Algo como:
- Alteração grande da estrutura do banco
- Mudança incompatível na API
- Reestruturação completa do módulo de compras
- Mudança que exige migração dos dados
- Alteração que quebra funcionalidades existentes
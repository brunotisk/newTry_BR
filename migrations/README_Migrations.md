# Controle de Migrations — Projeto AC

## Objetivo

Este projeto utiliza dois ambientes Supabase separados:

- **HML — Homologação:** ambiente para desenvolvimento, testes e validação.
- **PRD — Produção:** ambiente com os dados reais do sistema.

O fluxo oficial para alterações no banco é:

```text
DESENVOLVIMENTO
       ↓
HOMOLOGAÇÃO (HML)
       ↓
TESTES
       ↓
APROVAÇÃO
       ↓
PRODUÇÃO (PRD)
```

**Nunca testar alterações diretamente em produção.**

---

## Estrutura das migrations

As alterações estruturais do banco devem ser registradas em arquivos SQL numerados:

```text
migrations/
├── 001_criacao_inicial.sql
├── 002_descricao_da_alteracao.sql
├── 003_descricao_da_alteracao.sql
└── ...
```

### Regras

1. Cada alteração estrutural deve gerar uma nova migration.
2. Uma migration já aplicada **não deve ser editada**.
3. Se for necessário corrigir uma migration já aplicada, criar uma nova migration.
4. As migrations devem ser aplicadas primeiro em **HML**.
5. Depois dos testes e da aprovação, a mesma migration deve ser aplicada em **PRD**.
6. Uma migration não deve ser aplicada novamente no mesmo ambiente.
7. O controle das migrations aplicadas fica registrado na tabela:

```sql
public.schema_migrations
```

---

## Controle de migrations

Cada banco possui sua própria tabela `schema_migrations`.

Estrutura:

```sql
CREATE TABLE public.schema_migrations (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    version text NOT NULL UNIQUE,
    nome text NOT NULL,
    aplicado_em timestamp with time zone NOT NULL DEFAULT now()
);
```

Exemplo:

| version | nome | aplicado_em |
|---|---|---|
| 001 | criacao_inicial | 2026-09-21 |

Assim, HML e PRD mantêm o histórico independente das migrations que já foram executadas.

---

## Como aplicar uma nova migration

### 1. Criar a migration

Exemplo:

```text
migrations/002_adiciona_campo_marca_produto.sql
```

### 2. Executar em HML

Configure o `.env`:

```env
BD=HML
```

Execute a migration no banco de homologação.

Depois de confirmar que a execução foi concluída corretamente, registre:

```sql
INSERT INTO public.schema_migrations (version, nome)
VALUES ('002', 'adiciona_campo_marca_produto');
```
INSERT INTO public.schema_migrations (version, nome)
VALUES
    ('001', 'criar_banco_dados_PRD'),
    ('002', 'Create_migrations_table'),
    ('003', 'Timezone_BD');

### 3. Testar a aplicação

Com:

```env
BD=HML
```

execute o sistema e valide as funcionalidades afetadas pela alteração.

### 4. Aprovar para produção

Somente depois dos testes e da aprovação a migration deve ser aplicada em PRD.

Configure:

```env
BD=PRD
```

Execute **o mesmo arquivo SQL** da migration.

Depois de confirmar a execução:

```sql
INSERT INTO public.schema_migrations (version, nome)
VALUES ('002', 'adiciona_campo_marca_produto');
```

---

## Importante sobre o registro

A migration deve ser registrada na tabela `schema_migrations` **somente depois que sua execução no banco tiver sido concluída com sucesso**.

Não registrar uma migration como aplicada se o SQL falhou ou foi executado parcialmente.

A coluna `version` é `UNIQUE`, portanto o banco impede o registro duplicado da mesma versão.

---

## Migration 001

A migration inicial corresponde à criação da estrutura atual do banco.

Arquivo:

```text
migrations/001_criacao_inicial.sql
```

Ela deve representar a estrutura inicial do projeto e serve como marco inicial do controle de versões do banco.

O status atual da migration 001 está registrado em:

```text
controle.md
```

---

## Ambientes

A seleção do ambiente é feita pelo `.env`:

### Homologação

```env
BD=HML
```

### Produção

```env
BD=PRD
```

## Objetivo deste controle

A partir da migration 001, toda alteração estrutural do banco deve seguir o histórico:

```text
001 → 002 → 003 → 004 → ...
```

E cada ambiente deve manter seu próprio registro de execução.

Isso permite identificar:

- quais alterações já foram aplicadas;
- quais alterações ainda faltam em HML;
- quais alterações ainda faltam em PRD;
- em que data cada migration foi aplicada.
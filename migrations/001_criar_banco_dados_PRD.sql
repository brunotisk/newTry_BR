-- ============================================================
-- BANCO DE HOMOLOGAÇÃO - ESTRUTURA
-- Baseado exclusivamente no SQL_21_09.txt fornecido.
--
-- Ajuste realizado:
--   1. Tabelas sem dependências primeiro.
--   2. Tabelas com FK depois.
--   3. FKs que formam dependência circular são adicionadas
--      ao final, após todas as tabelas existirem.
--
-- Este script NÃO cria dados.
-- Este script NÃO cria RLS/policies, triggers, functions ou
-- objetos de Storage, pois eles não estão presentes no SQL fornecido.
-- ============================================================


-- ============================================================
-- 1. TABELAS BASE
-- ============================================================

CREATE TABLE public.fornecedores (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  cnpj text NOT NULL UNIQUE,
  razao_social text NOT NULL,
  nome_fantasia text,
  uf text,
  municipio text,
  criado_em timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT fornecedores_pkey PRIMARY KEY (id)
);

CREATE TABLE public.categorias_produtos (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  categoria text NOT NULL,
  banho text NOT NULL,
  ativo boolean NOT NULL DEFAULT true,
  ordem_exibicao integer NOT NULL DEFAULT 0,
  criado_em timestamp with time zone NOT NULL DEFAULT now(),
  atualizado_em timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT categorias_produtos_pkey PRIMARY KEY (id)
);

CREATE TABLE public.canais_venda (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  nome text NOT NULL UNIQUE,
  ativo boolean NOT NULL DEFAULT true,
  criado_em timestamp with time zone NOT NULL DEFAULT now(),
  ordem_exibicao integer,
  CONSTRAINT canais_venda_pkey PRIMARY KEY (id)
);

CREATE TABLE public.status_venda (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  nome text NOT NULL UNIQUE,
  ativo boolean NOT NULL DEFAULT true,
  criado_em timestamp with time zone NOT NULL DEFAULT now(),
  ordem_exibicao integer,
  CONSTRAINT status_venda_pkey PRIMARY KEY (id)
);

CREATE TABLE public.detalhes_feira (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  nome_feira text NOT NULL,
  endereco_feira text,
  pessoa_contato_feira text,
  tel_contato_feira text,
  ativo boolean NOT NULL DEFAULT true,
  ordem_exibicao integer,
  CONSTRAINT detalhes_feira_pkey PRIMARY KEY (id)
);

CREATE TABLE public.formas_pagamento (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  descricao text NOT NULL UNIQUE,
  ativo boolean NOT NULL DEFAULT true,
  criado_em timestamp with time zone NOT NULL DEFAULT now(),
  ordem_exibicao integer,
  CONSTRAINT formas_pagamento_pkey PRIMARY KEY (id)
);


-- ============================================================
-- 2. PRODUTOS
-- Depende de categorias_produtos
-- ============================================================

CREATE TABLE public.produtos (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  codigo_interno text NOT NULL UNIQUE,
  descricao text NOT NULL,
  ncm text,
  unidade text,
  criado_em timestamp with time zone NOT NULL DEFAULT now(),
  atualizado_em timestamp with time zone NOT NULL DEFAULT now(),
  categoria_id bigint,
  ultima_compra date,
  nr_compras integer,
  CONSTRAINT produtos_pkey PRIMARY KEY (id),
  CONSTRAINT produtos_categoria_id_fkey
    FOREIGN KEY (categoria_id)
    REFERENCES public.categorias_produtos(id)
);


-- ============================================================
-- 3. CLIENTES
-- Depende de canais_venda
-- ============================================================

CREATE TABLE public.clientes (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  nome text NOT NULL,
  telefone text,
  canal_id bigint,
  data_nascimento date,
  data_casamento date,
  criado_em timestamp with time zone NOT NULL DEFAULT now(),
  atualizado_em timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT clientes_pkey PRIMARY KEY (id),
  CONSTRAINT clientes_canal_id_fkey
    FOREIGN KEY (canal_id)
    REFERENCES public.canais_venda(id)
);


-- ============================================================
-- 4. COMPRAS
-- Depende de fornecedores
-- ============================================================

CREATE TABLE public.compras (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  chave_acesso text NOT NULL UNIQUE,
  numero_nf text NOT NULL,
  serie text,
  natureza_operacao text,
  fornecedor_id bigint NOT NULL,
  data_emissao timestamp with time zone NOT NULL,
  valor_produtos numeric NOT NULL,
  valor_desconto numeric NOT NULL DEFAULT 0,
  valor_total numeric NOT NULL,
  xml_original text,
  criado_em timestamp with time zone NOT NULL DEFAULT now(),
  compras_desconto_adicional numeric,
  compras_motivo_desconto text,
  CONSTRAINT compras_pkey PRIMARY KEY (id),
  CONSTRAINT compras_fornecedor_id_fkey
    FOREIGN KEY (fornecedor_id)
    REFERENCES public.fornecedores(id)
);


-- ============================================================
-- 5. VENDAS
-- Depende de produtos e cadastros auxiliares.
--
-- A FK venda_id de estoque_movimentos não pode ser criada
-- antes de vendas existir; por isso ela será adicionada depois.
-- ============================================================

CREATE TABLE public.vendas (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  produto_id bigint NOT NULL,
  quantidade numeric NOT NULL,
  valor_lista numeric NOT NULL,
  valor_desconto numeric NOT NULL DEFAULT 0,
  valor_final numeric NOT NULL,
  data_venda date NOT NULL,
  cliente text,
  observacao text,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  canal_venda_id bigint,
  status_id bigint,
  forma_pagamento_id bigint,
  detalhe_feira_id bigint,
  CONSTRAINT vendas_pkey PRIMARY KEY (id),
  CONSTRAINT vendas_produto_id_fkey
    FOREIGN KEY (produto_id)
    REFERENCES public.produtos(id),
  CONSTRAINT vendas_canal_venda_id_fkey
    FOREIGN KEY (canal_venda_id)
    REFERENCES public.canais_venda(id),
  CONSTRAINT vendas_status_id_fkey
    FOREIGN KEY (status_id)
    REFERENCES public.status_venda(id),
  CONSTRAINT vendas_forma_pagamento_id_fkey
    FOREIGN KEY (forma_pagamento_id)
    REFERENCES public.formas_pagamento(id),
  CONSTRAINT vendas_detalhe_feira_id_fkey
    FOREIGN KEY (detalhe_feira_id)
    REFERENCES public.detalhes_feira(id)
);


-- ============================================================
-- 6. ITENS DE COMPRAS
-- Depende de compras e produtos
-- ============================================================

CREATE TABLE public.compras_itens (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  compra_id bigint NOT NULL,
  produto_id bigint NOT NULL,
  numero_item integer NOT NULL,
  quantidade numeric NOT NULL,
  valor_unitario numeric NOT NULL,
  valor_desconto numeric NOT NULL DEFAULT 0,
  valor_total numeric NOT NULL,
  criado_em timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT compras_itens_pkey PRIMARY KEY (id),
  CONSTRAINT compras_itens_produto_id_fkey
    FOREIGN KEY (produto_id)
    REFERENCES public.produtos(id),
  CONSTRAINT compras_itens_compra_id_fkey
    FOREIGN KEY (compra_id)
    REFERENCES public.compras(id)
);


-- ============================================================
-- 7. ESTOQUE
-- Depende de produtos
-- ============================================================

CREATE TABLE public.estoque (
  produto_id bigint NOT NULL,
  quantidade_atual numeric NOT NULL DEFAULT 0,
  atualizado_em timestamp with time zone NOT NULL DEFAULT now(),
  estoque_preco_venda_sugerida numeric,
  estoque_ultima_compra date,
  estoque_num_compras integer,
  estoque_preco_ultima_compra numeric,
  estoque_flag_ajuste_preco_venda boolean NOT NULL DEFAULT false,
  estoque_preco_venda_original numeric,
  CONSTRAINT estoque_pkey PRIMARY KEY (produto_id),
  CONSTRAINT estoque_produto_id_fkey
    FOREIGN KEY (produto_id)
    REFERENCES public.produtos(id)
);


-- ============================================================
-- 8. ARQUIVOS DE COMPRAS
-- Depende de compras
-- ============================================================

CREATE TABLE public.compras_arquivos (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  compra_id bigint NOT NULL,
  tipo_arquivo text NOT NULL,
  nome_arquivo text NOT NULL,
  caminho_storage text NOT NULL,
  mime_type text,
  tamanho_bytes bigint,
  criado_em timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT compras_arquivos_pkey PRIMARY KEY (id),
  CONSTRAINT compras_arquivos_compra_fk
    FOREIGN KEY (compra_id)
    REFERENCES public.compras(id)
);


-- ============================================================
-- 9. MOVIMENTOS DE ESTOQUE
--
-- Depende de:
--   produtos
--   vendas
--   compras
--
-- Todas essas tabelas já existem neste ponto.
-- ============================================================

CREATE TABLE public.estoque_movimentos (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  produto_id bigint NOT NULL,
  tipo text NOT NULL
    CHECK (tipo = ANY (
      ARRAY['entrada'::text, 'saida'::text, 'ajuste'::text]
    )),
  quantidade numeric NOT NULL,
  saldo_apos numeric NOT NULL,
  criado_em timestamp with time zone NOT NULL DEFAULT now(),
  venda_id bigint,
  compra_id bigint,
  data_movimento date,
  motivo text,
  CONSTRAINT estoque_movimentos_pkey PRIMARY KEY (id),
  CONSTRAINT estoque_movimentos_produto_id_fkey
    FOREIGN KEY (produto_id)
    REFERENCES public.produtos(id),
  CONSTRAINT estoque_movimentos_venda_id_fkey
    FOREIGN KEY (venda_id)
    REFERENCES public.vendas(id),
  CONSTRAINT estoque_movimentos_compra_id_fkey
    FOREIGN KEY (compra_id)
    REFERENCES public.compras(id)
);


-- ============================================================
-- FIM
-- ============================================================

-- Tabelas criadas:
--
-- fornecedores
-- categorias_produtos
-- canais_venda
-- status_venda
-- detalhes_feira
-- formas_pagamento
-- produtos
-- clientes
-- compras
-- vendas
-- compras_itens
-- estoque
-- compras_arquivos
-- estoque_movimentos
--
-- Nenhum dado foi inserido.

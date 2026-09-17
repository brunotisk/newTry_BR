-- ============================================================
-- NOVOS CADASTROS AUXILIARES DE VENDAS
-- ============================================================

-- ============================================================
-- 1. DETALHES FEIRA
-- ============================================================

CREATE TABLE public.detalhes_feira (
    id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
    nome_feira text NOT NULL,
    endereco_feira text,
    pessoa_contato_feira text,
    tel_contato_feira text,
    ativo boolean NOT NULL DEFAULT true,
    criado_em timestamp with time zone NOT NULL DEFAULT now(),

    CONSTRAINT detalhes_feira_pkey PRIMARY KEY (id)
);


-- ============================================================
-- 2. FORMAS DE PAGAMENTO
-- ============================================================

CREATE TABLE public.formas_pagamento (
    id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
    descricao text NOT NULL UNIQUE,
    ativo boolean NOT NULL DEFAULT true,
    criado_em timestamp with time zone NOT NULL DEFAULT now(),

    CONSTRAINT formas_pagamento_pkey PRIMARY KEY (id)
);


-- ============================================================
-- ALTERAÇÕES NA TABELA VENDAS
-- ============================================================

-- 3. FORMA DE PAGAMENTO
ALTER TABLE public.vendas
ADD COLUMN forma_pagamento_id bigint;

ALTER TABLE public.vendas
ADD CONSTRAINT vendas_forma_pagamento_id_fkey
FOREIGN KEY (forma_pagamento_id)
REFERENCES public.formas_pagamento(id);


-- 4. FEIRA
ALTER TABLE public.vendas
ADD COLUMN detalhe_feira_id bigint;

ALTER TABLE public.vendas
ADD CONSTRAINT vendas_detalhe_feira_id_fkey
FOREIGN KEY (detalhe_feira_id)
REFERENCES public.detalhes_feira(id);


-- ============================================================
-- ÍNDICES
-- ============================================================

CREATE INDEX idx_vendas_forma_pagamento_id
ON public.vendas (forma_pagamento_id);

CREATE INDEX idx_vendas_detalhe_feira_id
ON public.vendas (detalhe_feira_id);


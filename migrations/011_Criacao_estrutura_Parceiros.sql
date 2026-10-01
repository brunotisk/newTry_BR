-- Migration 010
-- Parcerias: estrutura de parceiros, comissoes e fechamentos.
-- Projeto AC
--
-- Aplicar primeiro em HML e, apos validacao, em PRD.
-- Nao executar novamente em um ambiente onde esta migration ja foi aplicada.

BEGIN;

-- ============================================================
-- 1. Cadastro de parceiros
-- ============================================================

CREATE TABLE public.parceiros (
    id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
    canal_id bigint NOT NULL,
    nome text NOT NULL,
    telefone text,
    email text,
    percentual_comissao numeric(7,4) NOT NULL DEFAULT 0,
    observacao text,
    ativo boolean NOT NULL DEFAULT true,
    criado_em timestamp with time zone NOT NULL DEFAULT now(),
    atualizado_em timestamp with time zone NOT NULL DEFAULT now(),

    CONSTRAINT parceiros_pkey PRIMARY KEY (id),

    CONSTRAINT parceiros_canal_id_fkey
        FOREIGN KEY (canal_id)
        REFERENCES public.canais_venda(id),

    CONSTRAINT parceiros_canal_id_unique
        UNIQUE (canal_id),

    CONSTRAINT parceiros_percentual_comissao_check
        CHECK (
            percentual_comissao >= 0
            AND percentual_comissao <= 100
        )
);

COMMENT ON TABLE public.parceiros IS
'Cadastro de parceiros comerciais vinculados a um canal de venda.';

COMMENT ON COLUMN public.parceiros.canal_id IS
'Canal de venda utilizado para identificar as vendas deste parceiro.';

COMMENT ON COLUMN public.parceiros.percentual_comissao IS
'Percentual padrao de comissao do parceiro.';

-- ============================================================
-- 2. Comissao registrada na venda
-- ============================================================

ALTER TABLE public.vendas
    ADD COLUMN comissao_percentual numeric(7,4),
    ADD COLUMN comissao_valor numeric(15,2);

ALTER TABLE public.vendas
    ADD CONSTRAINT vendas_comissao_percentual_check
        CHECK (
            comissao_percentual IS NULL
            OR (
                comissao_percentual >= 0
                AND comissao_percentual <= 100
            )
        );

ALTER TABLE public.vendas
    ADD CONSTRAINT vendas_comissao_valor_check
        CHECK (
            comissao_valor IS NULL
            OR comissao_valor >= 0
        );

COMMENT ON COLUMN public.vendas.comissao_percentual IS
'Percentual de comissao aplicado a esta venda no momento da venda.';

COMMENT ON COLUMN public.vendas.comissao_valor IS
'Valor monetario da comissao desta venda.';

-- ============================================================
-- 3. Fechamentos / ciclos de parceria
-- ============================================================

CREATE TABLE public.parcerias_fechamentos (
    id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
    parceiro_id bigint NOT NULL,
    numero_ciclo integer NOT NULL,
    data_inicio date NOT NULL,
    data_fim date,
    percentual_comissao numeric(7,4) NOT NULL,
    valor_total_vendas numeric(15,2) NOT NULL DEFAULT 0,
    valor_total_comissao numeric(15,2) NOT NULL DEFAULT 0,
    status text NOT NULL DEFAULT 'Aberto',
    observacao text,
    criado_em timestamp with time zone NOT NULL DEFAULT now(),
    fechado_em timestamp with time zone,

    CONSTRAINT parcerias_fechamentos_pkey PRIMARY KEY (id),

    CONSTRAINT parcerias_fechamentos_parceiro_id_fkey
        FOREIGN KEY (parceiro_id)
        REFERENCES public.parceiros(id),

    CONSTRAINT parcerias_fechamentos_ciclo_unique
        UNIQUE (parceiro_id, numero_ciclo),

    CONSTRAINT parcerias_fechamentos_status_check
        CHECK (
            status IN ('Aberto', 'Finalizado', 'Pago', 'Cancelado')
        ),

    CONSTRAINT parcerias_fechamentos_datas_check
        CHECK (
            data_fim IS NULL
            OR data_fim >= data_inicio
        ),

    CONSTRAINT parcerias_fechamentos_percentual_check
        CHECK (
            percentual_comissao >= 0
            AND percentual_comissao <= 100
        ),

    CONSTRAINT parcerias_fechamentos_valores_check
        CHECK (
            valor_total_vendas >= 0
            AND valor_total_comissao >= 0
        )
);

COMMENT ON TABLE public.parcerias_fechamentos IS
'Ciclos de fechamento de vendas e comissoes de cada parceiro.';

-- ============================================================
-- 4. Vendas pertencentes a cada fechamento
-- ============================================================

CREATE TABLE public.parcerias_fechamento_vendas (
    id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
    fechamento_id bigint NOT NULL,
    venda_id bigint NOT NULL,
    valor_venda numeric(15,2) NOT NULL,
    percentual_comissao numeric(7,4) NOT NULL,
    valor_comissao numeric(15,2) NOT NULL,
    incluido_em timestamp with time zone NOT NULL DEFAULT now(),

    CONSTRAINT parcerias_fechamento_vendas_pkey PRIMARY KEY (id),

    CONSTRAINT parcerias_fechamento_vendas_fechamento_fkey
        FOREIGN KEY (fechamento_id)
        REFERENCES public.parcerias_fechamentos(id),

    CONSTRAINT parcerias_fechamento_vendas_venda_fkey
        FOREIGN KEY (venda_id)
        REFERENCES public.vendas(id),

    CONSTRAINT parcerias_fechamento_vendas_venda_unique
        UNIQUE (venda_id),

    CONSTRAINT parcerias_fechamento_vendas_valores_check
        CHECK (
            valor_venda >= 0
            AND valor_comissao >= 0
        ),

    CONSTRAINT parcerias_fechamento_vendas_percentual_check
        CHECK (
            percentual_comissao >= 0
            AND percentual_comissao <= 100
        )
);

COMMENT ON TABLE public.parcerias_fechamento_vendas IS
'Relaciona as vendas individuais que compoem cada fechamento de parceria.';

-- ============================================================
-- 5. Indices
-- ============================================================

CREATE INDEX idx_parceiros_canal_id
    ON public.parceiros(canal_id);

CREATE INDEX idx_parcerias_fechamentos_parceiro_id
    ON public.parcerias_fechamentos(parceiro_id);

CREATE INDEX idx_parcerias_fechamentos_status
    ON public.parcerias_fechamentos(status);

CREATE INDEX idx_parcerias_fechamento_vendas_fechamento_id
    ON public.parcerias_fechamento_vendas(fechamento_id);

CREATE INDEX idx_vendas_canal_data_comissao
    ON public.vendas(canal_venda_id, data_venda);

COMMIT;

-- ============================================================
-- Controle de migration
-- Execute esta parte conforme o procedimento do projeto,
-- caso o controle ainda nao seja automatizado.
--
-- INSERT INTO public.schema_migrations (version, nome)
-- VALUES ('010', 'Criar estrutura de parcerias e comissoes');
-- ============================================================

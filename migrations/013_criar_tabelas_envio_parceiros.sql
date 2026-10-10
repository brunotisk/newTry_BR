-- =====================================================================
-- Envio de produtos aos parceiros
-- Rode no SQL Editor do Supabase. Pode rodar mais de uma vez (idempotente).
-- Ajustado ao schema atual: parceiros.id e produtos.id são int8.
-- =====================================================================

-- 1) Cabeçalho do envio (um "pacote" de produtos entregue ao parceiro)
create table if not exists public.parcerias_envios (
    id            int8 generated always as identity primary key,
    parceiro_id   int8 not null references public.parceiros(id),
    numero_envio  int4 not null,
    data_envio    date not null default current_date,
    status        text not null default 'Aberto'
                  check (status in ('Aberto', 'Fechado')),
    observacao    text,
    -- Totais gravados no momento do fechamento
    total_itens   int4,
    total_pecas   numeric,
    valor_total   numeric,
    criado_em     timestamptz not null default now(),
    fechado_em    timestamptz,
    unique (parceiro_id, numero_envio)
);

-- No máximo UM envio aberto por parceiro
create unique index if not exists ux_parcerias_envios_um_aberto
    on public.parcerias_envios (parceiro_id)
    where status = 'Aberto';

create index if not exists ix_parcerias_envios_parceiro
    on public.parcerias_envios (parceiro_id, numero_envio desc);

-- 2) Itens (produtos) de cada envio
create table if not exists public.parcerias_envio_itens (
    id              int8 generated always as identity primary key,
    envio_id        int8 not null references public.parcerias_envios(id) on delete cascade,
    produto_id      int8 not null references public.produtos(id),
    quantidade      numeric not null check (quantidade > 0),
    -- Preço sugerido do estoque no momento em que o produto foi adicionado
    valor_unitario  numeric not null default 0,
    criado_em       timestamptz not null default now(),
    unique (envio_id, produto_id)
);

create index if not exists ix_parcerias_envio_itens_envio
    on public.parcerias_envio_itens (envio_id);

create index if not exists ix_parcerias_envio_itens_produto
    on public.parcerias_envio_itens (produto_id);

-- 3) Sem RLS, igual às demais tabelas de parceria (parceiros, parcerias_fechamentos...)
alter table public.parcerias_envios      disable row level security;
alter table public.parcerias_envio_itens disable row level security;

comment on table public.parcerias_envios      is 'Envios de produtos a cada parceiro (Aberto/Fechado).';
comment on table public.parcerias_envio_itens is 'Produtos e quantidades de cada envio a parceiro.';


-- Link da foto de cada produto (preenchido pelo script importar_fotos_lolia.py)
alter table public.produtos
    add column if not exists foto_url text;

comment on column public.produtos.foto_url is
    'URL da foto do produto (loja Lolia). Nula quando não encontrada.';

ALTER TABLE public.produtos
ADD COLUMN IF NOT EXISTS produto_descricao_site text;
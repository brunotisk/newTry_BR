-- =====================================================================
-- Conciliação bancária das parcerias (PIX, cartão e dinheiro)
-- Rode no SQL Editor do Supabase. Revise antes de aplicar em produção.
-- =====================================================================

-- 1. Formas de pagamento: como cada uma se concilia --------------------
alter table formas_pagamento
  add column if not exists tipo_conciliacao text not null default 'pix'
    check (tipo_conciliacao in ('pix', 'cartao', 'dinheiro')),
  add column if not exists taxa_percentual numeric not null default 0,
  add column if not exists prazo_dias int not null default 2;

-- Ajuste conforme suas formas cadastradas, por exemplo:
-- update formas_pagamento set tipo_conciliacao='pix',      prazo_dias=1                      where descricao ilike 'pix%';
-- update formas_pagamento set tipo_conciliacao='cartao',   prazo_dias=30, taxa_percentual=3.5 where descricao ilike '%cart%';
-- update formas_pagamento set tipo_conciliacao='dinheiro', prazo_dias=7                      where descricao ilike 'dinheiro%';

-- 2. Contas e extrato ----------------------------------------------------
create table if not exists contas_bancarias (
  id bigint generated always as identity primary key,
  nome text not null,
  banco text,
  ativo boolean not null default true,
  criado_em timestamptz not null default now()
);

create table if not exists lancamentos_bancarios (
  id bigint generated always as identity primary key,
  conta_id bigint not null references contas_bancarias(id),
  data_lancamento date not null,
  descricao text,
  valor numeric not null,              -- crédito +, débito -
  id_externo text not null,            -- FITID do OFX ou hash da linha do CSV
  importado_em timestamptz not null default now(),
  unique (conta_id, id_externo)
);
create index if not exists idx_lanc_conta_data on lancamentos_bancarios (conta_id, data_lancamento);

-- 3. Ligação venda x lançamento (N:N) -------------------------------------
create table if not exists conciliacao_vendas (
  id bigint generated always as identity primary key,
  venda_id bigint not null references vendas(id),
  lancamento_id bigint references lancamentos_bancarios(id),  -- null = baixa em caixa (dinheiro)
  tipo_baixa text not null default 'banco' check (tipo_baixa in ('banco', 'caixa')),
  valor_bruto_venda numeric not null,
  valor_alocado numeric not null,      -- parte do crédito que cobre esta venda
  taxa_valor numeric not null default 0,
  conciliado_por text,
  conciliado_em timestamptz not null default now(),
  unique (venda_id)
);
create index if not exists idx_conc_lanc on conciliacao_vendas (lancamento_id);

alter table vendas
  add column if not exists status_conciliacao text not null default 'Pendente'
    check (status_conciliacao in ('Pendente', 'Conciliada', 'Justificada'));

-- IMPORTANTE: vendas antigas viram "Pendente". Marque como conciliadas o que
-- veio antes de começar a usar o módulo (ajuste a data):
-- update vendas set status_conciliacao='Justificada' where data_venda < '2026-10-01';

-- 4. Pagamento da comissão ao parceiro ------------------------------------
alter table parcerias_fechamentos
  add column if not exists status_pagamento text not null default 'A pagar'
    check (status_pagamento in ('A pagar', 'Pago')),
  add column if not exists lancamento_pagamento_id bigint references lancamentos_bancarios(id),
  add column if not exists pago_em date;

-- 5. Views de apoio --------------------------------------------------------
create or replace view v_lancamentos_saldo with (security_invoker = true) as
select
  l.*,
  coalesce((select sum(cv.valor_alocado) from conciliacao_vendas cv
            where cv.lancamento_id = l.id), 0) as alocado_vendas,
  exists (select 1 from parcerias_fechamentos f
          where f.lancamento_pagamento_id = l.id) as usado_pagamento
from lancamentos_bancarios l;

create or replace view v_conferencia_fechamentos with (security_invoker = true) as
select
  f.id as fechamento_id,
  f.parceiro_id,
  f.numero_ciclo,
  f.valor_total_vendas,
  coalesce(sum(fv.valor_venda), 0) as soma_vendas,
  f.valor_total_comissao,
  coalesce(sum(fv.valor_comissao), 0) as soma_comissao,
  coalesce(sum(round(fv.valor_venda * fv.percentual_comissao / 100, 2)), 0) as comissao_recalculada
from parcerias_fechamentos f
left join parcerias_fechamento_vendas fv on fv.fechamento_id = f.id
group by f.id;

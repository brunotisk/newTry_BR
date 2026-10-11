-- =====================================================================
-- MIGRAÇÃO: ciclo da maleta do parceiro
-- Parte das tabelas parcerias_envios e parcerias_envio_itens que você JÁ TEM
-- e só acrescenta o que a tela nova precisa. Não apaga nem altera dados.
-- Rode uma vez no SQL Editor do Supabase (tudo ou nada: se algo falhar,
-- nada é aplicado).
-- =====================================================================
begin;

-- 1) parcerias_envios: novas colunas -----------------------------------
alter table public.parcerias_envios add column if not exists enviado_em            timestamptz;  -- quando a maleta saiu
alter table public.parcerias_envios add column if not exists obs_conferencia_saida text;
alter table public.parcerias_envios add column if not exists obs_fechamento        text;
alter table public.parcerias_envios add column if not exists total_vendido         numeric;      -- gravado no fechamento
alter table public.parcerias_envios add column if not exists total_devolvido       numeric;      -- gravado no fechamento
alter table public.parcerias_envios add column if not exists total_divergencia     numeric;      -- soma das diferenças (em peças)

-- 2) Novo status 'Enviado' (maleta com o parceiro) ----------------------
--    Aberto (montagem) -> Enviado -> Fechado
alter table public.parcerias_envios drop constraint if exists parcerias_envios_status_check;
alter table public.parcerias_envios
    add constraint parcerias_envios_status_check
    check (status in ('Aberto', 'Enviado', 'Fechado'));

-- 3) Um único envio ATIVO (Aberto ou Enviado) por parceiro --------------
drop index if exists public.ux_parcerias_envios_um_aberto;
create unique index if not exists ux_parcerias_envios_um_ativo
    on public.parcerias_envios (parceiro_id)
    where status in ('Aberto', 'Enviado');

-- 4) parcerias_envio_itens: conferência e resultado do fechamento -------
alter table public.parcerias_envio_itens add column if not exists conferido_saida boolean not null default false;
alter table public.parcerias_envio_itens add column if not exists qtd_vendida     numeric;  -- vendido confirmado
alter table public.parcerias_envio_itens add column if not exists qtd_devolvida   numeric;  -- recebido de volta
alter table public.parcerias_envio_itens add column if not exists qtd_divergencia numeric;  -- enviado - vendido - devolvido (+ falta / - sobra)

-- 5) Nova tabela: adições/remoções feitas com a maleta já enviada -------
create table if not exists public.parcerias_envio_movimentos (
    id          bigint generated always as identity primary key,
    envio_id    bigint not null references public.parcerias_envios(id) on delete cascade,
    produto_id  bigint not null references public.produtos(id),
    tipo        text   not null check (tipo in ('adicao', 'remocao')),
    quantidade  numeric not null check (quantidade > 0),
    motivo      text,
    criado_em   timestamptz not null default now()
);

create index if not exists ix_parcerias_envio_mov_envio
    on public.parcerias_envio_movimentos (envio_id);

-- 6) Sem RLS, igual às demais tabelas de parceria ------------------------
alter table public.parcerias_envio_movimentos disable row level security;

commit;

-- 7) Faz a API do Supabase enxergar as colunas/tabela novas na hora ------
notify pgrst, 'reload schema';

-- 8) Conferência: deve listar as 6 colunas novas de envios, as 4 de itens
--    e a tabela de movimentos (11 linhas no total)
select table_name, column_name
from information_schema.columns
where table_schema = 'public'
  and (
        (table_name = 'parcerias_envios'
           and column_name in ('enviado_em','obs_conferencia_saida','obs_fechamento',
                               'total_vendido','total_devolvido','total_divergencia'))
     or (table_name = 'parcerias_envio_itens'
           and column_name in ('conferido_saida','qtd_vendida','qtd_devolvida','qtd_divergencia'))
     or (table_name = 'parcerias_envio_movimentos' and column_name = 'id')
      )
order by table_name, column_name;

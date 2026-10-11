-- Retirada pessoal de estoque: uma baixa própria (tipo = 'retirada_pessoal'), que NÃO é venda.
-- Rode no SQL Editor do Supabase em CADA ambiente (HML e PRD). Pode ser executado mais de uma vez.

-- 1) Custo unitário no momento do movimento (preço da última compra), para valorizar as retiradas.
--    Fica nulo nos demais tipos de movimento.
alter table public.estoque_movimentos
  add column if not exists custo_unitario numeric null;

comment on column public.estoque_movimentos.custo_unitario is
  'Custo unitário (preço da última compra) no momento do movimento. Preenchido nas retiradas pessoais e nos seus estornos.';

-- 2) Índice para listar e somar retiradas por período.
create index if not exists idx_estoque_movimentos_tipo_data
  on public.estoque_movimentos (tipo, data_movimento);


-- 4) CONFERÊNCIA (só leitura): se existir um CHECK que limite os valores de "tipo",
--    o valor 'retirada_pessoal' precisa ser incluído nele. Sem resultado = sem restrição.
select conname, pg_get_constraintdef(oid) as definicao
from pg_constraint
where conrelid = 'public.estoque_movimentos'::regclass
  and contype = 'c';


ALTER TABLE public.estoque_movimentos
DROP CONSTRAINT estoque_movimentos_tipo_check;

ALTER TABLE public.estoque_movimentos
ADD CONSTRAINT estoque_movimentos_tipo_check
CHECK (
    tipo = ANY (
        ARRAY[
            'entrada',
            'saida',
            'ajuste',
            'retirada_pessoal'
        ]::text[]
    )
);
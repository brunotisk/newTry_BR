ALTER TABLE public.vendas
ADD COLUMN cliente_id bigint REFERENCES public.clientes(id);

UPDATE public.vendas v
SET cliente_id = c.id
FROM public.clientes c
WHERE v.cliente_id IS NULL
  AND v.cliente IS NOT NULL
  AND lower(trim(v.cliente)) = lower(trim(c.nome));
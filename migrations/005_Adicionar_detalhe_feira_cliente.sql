ALTER TABLE public.clientes
ADD COLUMN detalhe_feira_id bigint REFERENCES public.detalhes_feira(id);
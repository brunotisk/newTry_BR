ALTER TABLE public.compras
ADD COLUMN pct_desconto_item numeric NOT NULL DEFAULT 0;

ALTER TABLE public.compras_itens
ADD COLUMN valor_unit_ajustado numeric NOT NULL DEFAULT 0;

ALTER TABLE public.estoque
ADD COLUMN estoque_flag_pct_desconto_item boolean NOT NULL DEFAULT false;

UPDATE public.estoque
SET estoque_flag_pct_desconto_item = false;
-- Adiciona o campo
ALTER TABLE public.compras
ADD COLUMN compra_origem text;

-- Preenche os registros existentes
UPDATE public.compras
SET compra_origem = 'Importação XML'
WHERE compra_origem IS NULL;
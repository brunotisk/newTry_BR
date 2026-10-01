-- Migration 011
-- Parcerias: regras de comissao e protecao de vendas em fechamento.
-- Projeto AC
--
-- IMPORTANTE:
-- 1) Esta migration calcula a comissao automaticamente para vendas
--    cujo canal esteja cadastrado como parceiro.
-- 2) Vendas sem parceiro permanecem com comissao NULL.
-- 
--
-- Aplicar primeiro em HML e, apos validacao, em PRD.
-- Nao executar novamente em um ambiente onde esta migration ja foi aplicada.

BEGIN;

-- ============================================================
-- 1. Funcao para calcular a comissao de uma venda
-- ============================================================

CREATE OR REPLACE FUNCTION public.aplicar_comissao_venda()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_percentual numeric(7,4);
BEGIN
    SELECT p.percentual_comissao
      INTO v_percentual
      FROM public.parceiros p
     WHERE p.canal_id = NEW.canal_venda_id
       AND p.ativo = true
     LIMIT 1;

    IF v_percentual IS NULL THEN
        NEW.comissao_percentual := NULL;
        NEW.comissao_valor := NULL;
    ELSE
        NEW.comissao_percentual := v_percentual;
        NEW.comissao_valor := ROUND(
            COALESCE(NEW.valor_final, 0) * v_percentual / 100,
            2
        );
    END IF;

    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION public.aplicar_comissao_venda() IS
'Aplica na venda a regra de comissao vigente do parceiro associado ao canal.';

-- ============================================================
-- 2. Trigger de comissao
-- ============================================================

DROP TRIGGER IF EXISTS trg_aplicar_comissao_venda
ON public.vendas;

CREATE TRIGGER trg_aplicar_comissao_venda
BEFORE INSERT OR UPDATE OF canal_venda_id, valor_final
ON public.vendas
FOR EACH ROW
EXECUTE FUNCTION public.aplicar_comissao_venda();

-- ============================================================
-- 3. Preencher comissao das vendas existentes
-- ============================================================
-- Faz o preenchimento apenas das vendas que ainda nao possuem
-- informacao de comissao.

UPDATE public.vendas v
   SET comissao_percentual = p.percentual_comissao,
       comissao_valor = ROUND(
           COALESCE(v.valor_final, 0) * p.percentual_comissao / 100,
           2
       )
  FROM public.parceiros p
 WHERE p.canal_id = v.canal_venda_id
   AND p.ativo = true
   AND v.comissao_percentual IS NULL
   AND v.comissao_valor IS NULL;

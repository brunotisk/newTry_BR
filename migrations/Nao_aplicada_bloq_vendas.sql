-- Migration 011
-- Parcerias: regras de comissao e protecao de vendas em fechamento.
-- Projeto AC
--
-- IMPORTANTE:
-- 1) Esta migration calcula a comissao automaticamente para vendas
--    cujo canal esteja cadastrado como parceiro.
-- 2) Vendas sem parceiro permanecem com comissao NULL.
-- 3) Uma venda vinculada a qualquer fechamento fica protegida contra
--    UPDATE e DELETE pelo banco.
-- 4) A excecao para administrador precisa ser tratada pela aplicacao.
--    O projeto atual usa um client Supabase com service_role e identifica
--    o administrador no Streamlit por e-mail; portanto, nao e seguro
--    presumir que auth.jwt() no PostgreSQL represente o usuario da tela.
--    A migration deixa a trava de banco ativa para todos os usuarios.
--    A liberacao administrativa devera ser feita por um fluxo controlado
--    da aplicacao/RPC em uma etapa posterior.
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

-- ============================================================
-- 4. Funcao para bloquear alteracao de venda ja vinculada
--    a um fechamento.
-- ============================================================

CREATE OR REPLACE FUNCTION public.bloquear_venda_em_fechamento()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM public.parcerias_fechamento_vendas pfv
         WHERE pfv.venda_id = OLD.id
    ) THEN
        RAISE EXCEPTION
            'A venda % pertence a um fechamento de parceria e nao pode ser alterada.',
            OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION public.bloquear_venda_em_fechamento() IS
'Impede alteracao de venda que ja esteja vinculada a um fechamento de parceria.';

DROP TRIGGER IF EXISTS trg_bloquear_venda_em_fechamento_update
ON public.vendas;

CREATE TRIGGER trg_bloquear_venda_em_fechamento_update
BEFORE UPDATE ON public.vendas
FOR EACH ROW
EXECUTE FUNCTION public.bloquear_venda_em_fechamento();

-- ============================================================
-- 5. Funcao para bloquear exclusao de venda ja vinculada
--    a um fechamento.
-- ============================================================

CREATE OR REPLACE FUNCTION public.bloquear_exclusao_venda_em_fechamento()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM public.parcerias_fechamento_vendas pfv
         WHERE pfv.venda_id = OLD.id
    ) THEN
        RAISE EXCEPTION
            'A venda % pertence a um fechamento de parceria e nao pode ser excluida.',
            OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN OLD;
END;
$$;

COMMENT ON FUNCTION public.bloquear_exclusao_venda_em_fechamento() IS
'Impede exclusao de venda que ja esteja vinculada a um fechamento de parceria.';

DROP TRIGGER IF EXISTS trg_bloquear_venda_em_fechamento_delete
ON public.vendas;

CREATE TRIGGER trg_bloquear_venda_em_fechamento_delete
BEFORE DELETE ON public.vendas
FOR EACH ROW
EXECUTE FUNCTION public.bloquear_exclusao_venda_em_fechamento();

-- ============================================================
-- 6. Regras de consistencia do fechamento
-- ============================================================

CREATE OR REPLACE FUNCTION public.validar_venda_fechamento_parceria()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_status text;
    v_parceiro_canal_id bigint;
    v_venda_canal_id bigint;
BEGIN
    SELECT pf.status, p.canal_id
      INTO v_status, v_parceiro_canal_id
      FROM public.parcerias_fechamentos pf
      JOIN public.parceiros p
        ON p.id = pf.parceiro_id
     WHERE pf.id = NEW.fechamento_id;

    IF v_status IS NULL THEN
        RAISE EXCEPTION
            'Fechamento % nao encontrado.',
            NEW.fechamento_id;
    END IF;

    IF v_status <> 'aberto' THEN
        RAISE EXCEPTION
            'Nao e possivel incluir vendas em um fechamento com status "%".',
            v_status;
    END IF;

    SELECT v.canal_venda_id
      INTO v_venda_canal_id
      FROM public.vendas v
     WHERE v.id = NEW.venda_id;

    IF v_venda_canal_id IS NULL THEN
        RAISE EXCEPTION
            'Venda % nao encontrada.',
            NEW.venda_id;
    END IF;

    IF v_venda_canal_id <> v_parceiro_canal_id THEN
        RAISE EXCEPTION
            'A venda % nao pertence ao canal do parceiro deste fechamento.',
            NEW.venda_id;
    END IF;

    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION public.validar_venda_fechamento_parceria() IS
'Valida se a venda pertence ao canal do parceiro e se o fechamento ainda esta aberto.';

DROP TRIGGER IF EXISTS trg_validar_venda_fechamento_parceria
ON public.parcerias_fechamento_vendas;

CREATE TRIGGER trg_validar_venda_fechamento_parceria
BEFORE INSERT OR UPDATE ON public.parcerias_fechamento_vendas
FOR EACH ROW
EXECUTE FUNCTION public.validar_venda_fechamento_parceria();

-- ============================================================
-- 7. Ao finalizar/pagar um fechamento, registrar data.
-- ============================================================

CREATE OR REPLACE FUNCTION public.controlar_status_fechamento_parceria()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.status IN ('finalizado', 'pago')
       AND OLD.status = 'aberto'
       AND NEW.fechado_em IS NULL THEN
        NEW.fechado_em := now();
    END IF;

    IF OLD.status = 'pago'
       AND NEW.status <> OLD.status THEN
        RAISE EXCEPTION
            'O fechamento % ja esta como "pago" e nao pode ter o status alterado.',
            OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF OLD.status = 'cancelado'
       AND NEW.status <> OLD.status THEN
        RAISE EXCEPTION
            'O fechamento % esta cancelado e nao pode ter o status alterado.',
            OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF OLD.status = 'finalizado'
       AND NEW.status NOT IN ('finalizado', 'pago') THEN
        RAISE EXCEPTION
            'O fechamento % esta finalizado. A unica transicao permitida e para "pago".',
            OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION public.controlar_status_fechamento_parceria() IS
'Controla transicoes basicas de status e registra o momento do fechamento.';

DROP TRIGGER IF EXISTS trg_controlar_status_fechamento_parceria
ON public.parcerias_fechamentos;

CREATE TRIGGER trg_controlar_status_fechamento_parceria
BEFORE UPDATE ON public.parcerias_fechamentos
FOR EACH ROW
EXECUTE FUNCTION public.controlar_status_fechamento_parceria();

COMMIT;

-- ============================================================
-- Controle de migration
--
-- INSERT INTO public.schema_migrations (version, nome)
-- VALUES ('011', 'Regras de comissao e protecao de vendas em fechamento');
-- ============================================================

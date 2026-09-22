DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN
        SELECT conname
        FROM pg_constraint
        WHERE conrelid = 'public.clientes'::regclass
          AND contype = 'u'
          AND pg_get_constraintdef(oid) ILIKE '%nome%'
          AND pg_get_constraintdef(oid) NOT ILIKE '%canal_id%'
    LOOP
        EXECUTE format('ALTER TABLE public.clientes DROP CONSTRAINT %I', r.conname);
    END LOOP;
END $$;

DROP INDEX IF EXISTS public.uq_clientes_nome_lower;

CREATE UNIQUE INDEX IF NOT EXISTS uq_clientes_nome_canal
ON public.clientes (lower(trim(nome)), canal_id);
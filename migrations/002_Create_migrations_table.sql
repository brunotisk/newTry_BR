CREATE TABLE public.schema_migrations (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    version text NOT NULL UNIQUE,
    nome text NOT NULL,
    aplicado_em timestamp with time zone NOT NULL DEFAULT now()
);
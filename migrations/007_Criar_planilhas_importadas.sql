-- Tabela de controle usada pela trava de reimportação de planilhas de vendas.
-- Cada linha representa um ID de planilha (gerado em `gerar_planilha_modelo_vendas`
-- e gravado na aba "Dados de Apoio") que já foi efetivamente importado.

create table if not exists planilhas_importadas (
    id bigint generated always as identity primary key,
    id_planilha text not null unique,
    arquivo text,
    total_linhas integer,
    importado_em timestamptz not null default now()
);

create index if not exists idx_planilhas_importadas_id_planilha
    on planilhas_importadas (id_planilha);

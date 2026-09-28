-- ============================================================================
-- logs_automacao
--
-- Log das inserções/ajustes automáticos feitos pelo sistema. Alimentada por
-- servicos/estoque.py (registrar_movimento/registrar_ajuste) e por
-- servicos/compras_import.py (upsert_produto).
--
-- `operacao_id` agrupa todas as etapas de uma mesma execução:
--   Importar compras (por item da NF) -> cadastro_produto, ajusta_estoque,
--                                          ajusta_movimentacao
--   Importar vendas (por linha)       -> ajusta_estoque, ajusta_movimentacao
--   Venda manual                      -> ajusta_estoque, ajusta_movimentacao
--   Edição/Exclusão de venda          -> ajusta_estoque, ajusta_movimentacao
--   Exclusão de compra                -> ajusta_estoque, ajusta_movimentacao
--                                          (um por item estornado)
-- ============================================================================

create table if not exists public.logs_automacao (
    id           bigint generated always as identity primary key,
    operacao_id  uuid        not null,
    criado_em    timestamptz not null default now(),

    -- 'compra_importacao' | 'compra_exclusao' | 'venda_importacao' |
    -- 'venda_manual' | 'venda_edicao' | 'venda_exclusao' | 'ajuste_manual' | 'outro'
    fluxo        text        not null,

    -- 'cadastro_produto' | 'ajusta_estoque' | 'ajusta_movimentacao'
    etapa        text        not null,

    sucesso      boolean     not null default true,

    produto_id   bigint references public.produtos(id) on delete set null,
    compra_id    bigint references public.compras(id)  on delete set null,
    venda_id     bigint references public.vendas(id)   on delete set null,

    quantidade   numeric,
    mensagem     text
);

comment on table public.logs_automacao is
    'Log das inserções/ajustes automáticos do sistema (compras, vendas, estoque). Ver servicos/log_automacao.py.';

create index if not exists idx_logs_automacao_operacao_id on public.logs_automacao (operacao_id);
create index if not exists idx_logs_automacao_criado_em   on public.logs_automacao (criado_em desc);
create index if not exists idx_logs_automacao_compra_id   on public.logs_automacao (compra_id);
create index if not exists idx_logs_automacao_venda_id    on public.logs_automacao (venda_id);
create index if not exists idx_logs_automacao_fluxo       on public.logs_automacao (fluxo);
create index if not exists idx_logs_automacao_sucesso     on public.logs_automacao (sucesso) where sucesso = false;

-- ----------------------------------------------------------------------------
-- RLS: ajuste conforme as policies já usadas nas demais tabelas do projeto.
-- Abaixo, um ponto de partida liberando leitura e escrita para qualquer
-- usuário autenticado (mesmo padrão de acesso via login do auth.py).
-- Se as outras tabelas (compras, vendas, estoque...) NÃO usam RLS, remova
-- este bloco para manter o mesmo comportamento do restante do banco.
-- ----------------------------------------------------------------------------
alter table public.logs_automacao enable row level security;

create policy "logs_automacao_select_authenticated"
    on public.logs_automacao for select
    to authenticated
    using (true);

create policy "logs_automacao_insert_authenticated"
    on public.logs_automacao for insert
    to authenticated
    with check (true);

WITH dados AS (

    SELECT
        'fornecedores' AS tabela,
        MAX(id) AS max_id,
        COUNT(*) AS qtd_registros
    FROM public.fornecedores

    UNION ALL

    SELECT
        'categorias_produtos',
        MAX(id),
        COUNT(*)
    FROM public.categorias_produtos

    UNION ALL

    SELECT
        'produtos',
        MAX(id),
        COUNT(*)
    FROM public.produtos

    UNION ALL

    SELECT
        'compras',
        MAX(id),
        COUNT(*)
    FROM public.compras

    UNION ALL

    SELECT
        'compras_itens',
        MAX(id),
        COUNT(*)
    FROM public.compras_itens

    UNION ALL

    SELECT
        'estoque_movimentos',
        MAX(id),
        COUNT(*)
    FROM public.estoque_movimentos

    UNION ALL

    SELECT
        'vendas',
        MAX(id),
        COUNT(*)
    FROM public.vendas

    UNION ALL

    SELECT
        'canais_venda',
        MAX(id),
        COUNT(*)
    FROM public.canais_venda

    UNION ALL

    SELECT
        'status_venda',
        MAX(id),
        COUNT(*)
    FROM public.status_venda

    UNION ALL

    SELECT
        'detalhes_feira',
        MAX(id),
        COUNT(*)
    FROM public.detalhes_feira

    UNION ALL

    SELECT
        'formas_pagamento',
        MAX(id),
        COUNT(*)
    FROM public.formas_pagamento

    UNION ALL

    SELECT
        'clientes',
        MAX(id),
        COUNT(*)
    FROM public.clientes

    UNION ALL

    SELECT
        'compras_arquivos',
        MAX(id),
        COUNT(*)
    FROM public.compras_arquivos
),

sequencias AS (

    SELECT
        'fornecedores' AS tabela,
        pg_get_serial_sequence('public.fornecedores', 'id') AS sequence_name

    UNION ALL
    SELECT
        'categorias_produtos',
        pg_get_serial_sequence('public.categorias_produtos', 'id')

    UNION ALL
    SELECT
        'produtos',
        pg_get_serial_sequence('public.produtos', 'id')

    UNION ALL
    SELECT
        'compras',
        pg_get_serial_sequence('public.compras', 'id')

    UNION ALL
    SELECT
        'compras_itens',
        pg_get_serial_sequence('public.compras_itens', 'id')

    UNION ALL
    SELECT
        'estoque_movimentos',
        pg_get_serial_sequence('public.estoque_movimentos', 'id')

    UNION ALL
    SELECT
        'vendas',
        pg_get_serial_sequence('public.vendas', 'id')

    UNION ALL
    SELECT
        'canais_venda',
        pg_get_serial_sequence('public.canais_venda', 'id')

    UNION ALL
    SELECT
        'status_venda',
        pg_get_serial_sequence('public.status_venda', 'id')

    UNION ALL
    SELECT
        'detalhes_feira',
        pg_get_serial_sequence('public.detalhes_feira', 'id')

    UNION ALL
    SELECT
        'formas_pagamento',
        pg_get_serial_sequence('public.formas_pagamento', 'id')

    UNION ALL
    SELECT
        'clientes',
        pg_get_serial_sequence('public.clientes', 'id')

    UNION ALL
    SELECT
        'compras_arquivos',
        pg_get_serial_sequence('public.compras_arquivos', 'id')
)

SELECT
    d.tabela,
    d.qtd_registros,
    d.max_id,
    s.sequence_name,

    seq.last_value AS id_setado,

    CASE
        WHEN seq.last_value IS NULL THEN NULL
        ELSE seq.last_value + seq.increment_by
    END AS proximo_id,

    CASE
        WHEN d.max_id IS NULL THEN NULL
        WHEN seq.last_value IS NULL THEN NULL
        ELSE seq.last_value - d.max_id
    END AS diferenca,

    CASE
        WHEN d.max_id IS NULL
             AND seq.last_value IS NULL
            THEN 'VAZIA'

        WHEN d.max_id IS NOT NULL
             AND seq.last_value IS NULL
            THEN 'ERRO - SEQUENCE SEM VALOR'

        WHEN seq.last_value < d.max_id
            THEN 'ERRO - SEQUENCE ATRASADA'

        WHEN seq.last_value = d.max_id
            THEN 'OK - PROXIMO ID SERA MAX+1'

        WHEN seq.last_value > d.max_id
            THEN 'OK - SEQUENCE ADIANTADA'

        ELSE 'VERIFICAR'
    END AS status

FROM dados d
LEFT JOIN sequencias s
    ON s.tabela = d.tabela

LEFT JOIN pg_sequences seq
    ON seq.schemaname = 'public'
   AND seq.sequencename =
       split_part(s.sequence_name, '.', 2)

ORDER BY
    CASE
        WHEN d.max_id IS NOT NULL
         AND seq.last_value IS NOT NULL
         AND seq.last_value < d.max_id
        THEN 0
        ELSE 1
    END,
    d.tabela;

select * from compras order by criado_em desc
select * from compras_itens order by criado_em desc

SELECT setval(
    pg_get_serial_sequence('public.compras', 'id'),
    COALESCE(MAX(id), 0) + 1,
    false
)
FROM public.compras;

SELECT setval(
    pg_get_serial_sequence('public.compras_itens', 'id'),
    COALESCE(MAX(id), 0) + 1,
    false
)
FROM public.compras_itens;

SELECT setval(
    pg_get_serial_sequence('public.produtos', 'id'),
    COALESCE(MAX(id), 0) + 1,
    false
)
FROM public.produtos;

WITH tabelas AS (
    SELECT * FROM (VALUES
        ('fornecedores'),
        ('categorias_produtos'),
        ('canais_venda'),
        ('status_venda'),
        ('detalhes_feira'),
        ('formas_pagamento'),
        ('produtos'),
        ('clientes'),
        ('compras'),
        ('vendas'),
        ('compras_itens'),
        ('compras_arquivos'),
        ('estoque_movimentos'),
        ('schema_migrations'),
        ('planilhas_importadas')
    ) AS t(tabela)
)
SELECT
    tabela,
    pg_get_serial_sequence('public.' || tabela, 'id') AS sequence_name
FROM tabelas
ORDER BY tabela;


-----
# VERIFICA SE TEM INCONSISTÊNCIAS ENTRE COMPRAS, ITENS, PRODUTOS E ESTOQUE

SELECT
    c.id AS compra_id,
    c.numero_nf,
    c.chave_acesso,
    c.data_emissao,

    ci.id AS item_id,
    ci.numero_item,
    ci.produto_id,
    ci.quantidade,
    ci.valor_unitario,
    ci.valor_total,

    p.codigo_interno,
    p.descricao,

    e.quantidade_atual,
    e.atualizado_em AS estoque_atualizado,

    em.id AS movimento_id,
    em.tipo AS movimento_tipo,
    em.quantidade AS movimento_quantidade,
    em.saldo_apos AS movimento_saldo,
    em.criado_em AS movimento_criado_em

FROM public.compras c

LEFT JOIN public.compras_itens ci
    ON ci.compra_id = c.id

LEFT JOIN public.produtos p
    ON p.id = ci.produto_id

LEFT JOIN public.estoque e
    ON e.produto_id = ci.produto_id

LEFT JOIN public.estoque_movimentos em
    ON em.compra_id = c.id

WHERE c.id = ID_PROCURADO

ORDER BY
    ci.numero_item,
    em.id;

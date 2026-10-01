# Mostra tudo sem comissão, mas apenas para parceiros ativos.

SELECT
    v.id AS venda_id,
    v.canal_venda_id,
    c.nome AS canal,
    v.data_venda,
    v.valor_final,
    v.comissao_percentual,
    v.comissao_valor,
    p.id AS parceiro_id,
    p.percentual_comissao,
    p.ativo AS parceiro_ativo
FROM vendas v
LEFT JOIN canais_venda c
       ON c.id = v.canal_venda_id
LEFT JOIN parceiros p
       ON p.canal_id = v.canal_venda_id
WHERE p.ativo = true
  AND (
      v.comissao_percentual IS NULL
      OR v.comissao_valor IS NULL
  )
ORDER BY v.id DESC;


-- ============================================================
-- Atualizar vendas sem comissão
-- ============================================================
-- Preenche apenas vendas que ainda não possuem comissão.
-- Usa o percentual atualmente cadastrado em parceiros.
-- ============================================================

UPDATE public.vendas v
SET
    comissao_percentual = p.percentual_comissao,
    comissao_valor = ROUND(
        COALESCE(v.valor_final, 0) * p.percentual_comissao / 100,
        2
    )
FROM public.parceiros p
WHERE p.canal_id = v.canal_venda_id
  AND p.ativo = true
  AND v.comissao_percentual IS NULL
  AND v.comissao_valor IS NULL;
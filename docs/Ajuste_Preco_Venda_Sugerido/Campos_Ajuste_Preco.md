O diagrama acima mostra as 5 ações que mexem em preço/desconto nas duas telas, e como convergem para a tabela estoque. Detalhando os campos exatos de cada uma:

Compras — popup "Itens da compra"

Ação	Campos alterados
Definir % desconto	compras.pct_desconto_item
Aplicar valor sugerido	compras_itens.valor_unit_ajustado, estoque.estoque_preco_venda_sugerida, estoque.estoque_preco_ultima_compra, estoque.estoque_flag_pct_desconto_item = True
Retornar ao original	compras_itens.valor_unit_ajustado = 0, compras.pct_desconto_item = 0, estoque.estoque_flag_pct_desconto_item = False, estoque.estoque_preco_venda_sugerida = valor_unitario × 2

Estoque Saldo — popup "Ajustar preço de venda"

Ação	Campos alterados
Salvar alteração	estoque.estoque_preco_venda_sugerida, estoque.estoque_flag_ajuste_preco_venda, e no primeiro ajuste também estoque.estoque_preco_venda_original
Retornar ao original	estoque.estoque_preco_venda_sugerida = estoque_preco_venda_original, estoque.estoque_flag_ajuste_preco_venda = False
TRUNCATE TABLE
  parcerias_envio_pdfs,
  parcerias_envio_arquivos,
  parcerias_envio_movimentos,
  parcerias_envio_itens,
  parcerias_envios
RESTART IDENTITY;

SELECT 'parcerias_envios' AS tabela, count(*) FROM parcerias_envios
UNION ALL SELECT 'parcerias_envio_itens', count(*) FROM parcerias_envio_itens
UNION ALL SELECT 'parcerias_envio_movimentos', count(*) FROM parcerias_envio_movimentos
UNION ALL SELECT 'parcerias_envio_arquivos', count(*) FROM parcerias_envio_arquivos
UNION ALL SELECT 'parcerias_envio_pdfs', count(*) FROM parcerias_envio_pdfs;
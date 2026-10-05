# Suíte de testes revisada

A suíte foi reorganizada para refletir a arquitetura atual do projeto.

## Foco

- `test_compras_import.py`: parser e pipeline de NF-e.
- `test_vendas_import.py`: normalização, modelo Excel, validação, clientes, comissão e baixa de estoque.
- `test_estoque.py`: saldo + ledger + logs de automação.
- `test_supabase_admin.py`: seleção segura de HML/PRD.
- `test_db.py`: criação do client e paginação.
- `test_componentes.py`: regras puras dos componentes reutilizáveis.
- `test_auth.py`: autenticação e logout.

## Removidos propositalmente

Os testes antigos de `test_app.py` e grande parte de `test_telas.py` não foram mantidos como uma suíte extensa. Eles eram muito acoplados à ordem dos widgets, índices do `AppTest` e à "última query" executada. Esse tipo de teste gera falso positivo/negativo quando a UI é reorganizada sem mudar a regra de negócio.

A intenção é que a maior parte da proteção fique nos serviços de domínio e que os testes de Streamlit sejam pequenos e semânticos.


## Ajuste desta versão

Os testes foram alinhados ao contrato atual do projeto:
- `NotaFiscal.data_emissao` é `datetime`, portanto o teste valida a data e o horário extraídos do XML.
- `supabase_admin.get_client()` informa as variáveis ausentes pelos nomes `SUPABASE_URL` e `SUPABASE_SERVICE_KEY`.

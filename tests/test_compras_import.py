from datetime import date
from decimal import Decimal

import pytest

from servicos import compras_import as compras


def test_parse_nfe_extrai_cabecalho_totais_e_itens(nota):
    assert nota.chave_acesso
    assert nota.numero_nf == "1234"
    assert nota.serie == "1"
    assert nota.fornecedor_cnpj == "12345678000199"
    assert nota.data_emissao.date() == date(2024, 1, 15)
    assert nota.data_emissao.hour == 10
    assert nota.data_emissao.minute == 30
    assert nota.valor_total == Decimal("90.00")
    assert len(nota.itens) == 2
    assert nota.itens[0].codigo_interno == "A001"
    assert nota.itens[0].valor_total == Decimal("50.00")
    assert nota.itens[1].valor_desconto == Decimal("0")


def test_parse_nfe_invalido_levanta_erro():
    with pytest.raises(Exception):
        compras.parse_nfe_string("<xml quebrado>")


def test_parse_nfe_file_preserva_xml_original(xml_path):
    nota = compras.parse_nfe_file(xml_path)
    assert nota.xml_original.startswith("<?xml")
    assert nota.chave_acesso


def test_upsert_fornecedor_usa_cnpj_como_conflito(sb, nota):
    sb.responses[("fornecedores", "upsert")] = [{"id": 7}]
    fornecedor_id = compras.upsert_fornecedor(sb, nota)
    chamada = sb.last_call("fornecedores", "upsert")
    assert fornecedor_id == 7
    assert chamada.payload["cnpj"] == nota.fornecedor_cnpj
    assert chamada.kwargs["on_conflict"] == "cnpj"


def test_nota_ja_importada_detecta_chave(sb):
    sb.responses[("compras", "select")] = [{"id": 10}]
    assert compras.nota_ja_importada(sb, "abc") is True
    sb.responses[("compras", "select")] = []
    assert compras.nota_ja_importada(sb, "abc") is False


def test_inserir_compra_grava_origem_e_totais(sb, nota):
    sb.responses[("compras", "insert")] = [{"id": 20}]
    compra_id = compras.inserir_compra(sb, nota, fornecedor_id=7)
    chamada = sb.last_call("compras", "insert")
    assert compra_id == 20
    assert chamada.payload["fornecedor_id"] == 7
    assert chamada.payload["compra_origem"] == "Importação XML"
    assert chamada.payload["valor_total"] == 90.0


def test_upsert_produto_novo_cria_com_nr_compras_1(sb, nota):
    sb.responses[("produtos", "select")] = []
    sb.responses[("produtos", "upsert")] = [{"id": 8}]
    produto_id = compras.upsert_produto(sb, nota.itens[0], nota.data_emissao)
    chamada = sb.last_call("produtos", "upsert")
    assert produto_id == 8
    assert chamada.payload["codigo_interno"] == "A001"
    assert chamada.payload["nr_compras"] == 1
    assert chamada.payload["ultima_compra"] == "2024-01-15"


def test_upsert_produto_existente_incrementa_compras_e_mantem_data_mais_recente(sb, nota):
    sb.responses[("produtos", "select")] = [{"id": 8, "nr_compras": 4, "ultima_compra": "2024-05-01"}]
    sb.responses[("produtos", "upsert")] = [{"id": 8}]
    compras.upsert_produto(sb, nota.itens[0], nota.data_emissao)
    chamada = sb.last_call("produtos", "upsert")
    assert chamada.payload["nr_compras"] == 5
    assert chamada.payload["ultima_compra"] == "2024-05-01"


def test_inserir_item_gera_entrada_no_estoque_e_ledger(sb, nota, monkeypatch):
    chamadas = []
    monkeypatch.setattr(compras, "registrar_movimento", lambda *args, **kwargs: chamadas.append(kwargs) or 55)
    monkeypatch.setattr(compras, "_sincronizar_estoque_pos_compra", lambda *args, **kwargs: None)

    compras.inserir_item_e_atualizar_estoque(sb, 20, 8, nota.itens[0], nota.data_emissao)
    movimento = chamadas[0]
    assert movimento["tipo"] == "entrada"
    assert movimento["quantidade"] == 10.0
    assert movimento["compra_id"] == 20
    assert movimento["fluxo"] == "compra_importacao"


def test_validacao_nfe_rejeita_nota_sem_itens(sb, nota):
    nota.itens = []
    with pytest.raises(compras.ErroValidacaoImportacao, match="não possui itens"):
        compras._validar_nota_antes_de_gravar(sb, nota)


def test_importar_nfe_faz_pipeline_completo_sem_acesso_real(service_sb, xml_path, monkeypatch):
    sb = service_sb
    sb.responses[("compras", "select")] = []
    sb.responses[("produtos", "select")] = []
    sb.responses[("estoque", "select")] = []
    sb.responses[("fornecedores", "select")] = []
    sb.responses[("fornecedores", "upsert")] = [{"id": 3}]
    sb.responses[("compras", "insert")] = [{"id": 40}]
    sb.responses[("produtos", "upsert")] = lambda q: [{"id": 101 if q.payload["codigo_interno"] == "A001" else 102}]
    sb.responses[("compras_itens", "insert")] = [{"id": 501}]
    sb.responses[("estoque", "upsert")] = [{"id": 601}]
    sb.responses[("estoque_movimentos", "insert")] = [{"id": 701}]

    resultado = compras.importar_nfe(xml_path)
    assert resultado["status"] == "importado"
    assert resultado["compra_id"] == 40
    assert len(sb.calls_for("compras_itens", "insert")) == 2
    movimentos = sb.calls_for("estoque_movimentos", "insert")
    assert len(movimentos) == 2
    assert all(c.payload["tipo"] == "entrada" for c in movimentos)


def test_importar_nfe_duplicada_nao_grava_compra(service_sb, xml_path):
    sb = service_sb
    sb.responses[("compras", "select")] = [{"id": 99}]
    with pytest.raises(compras.ErroValidacaoImportacao):
        compras.importar_nfe(xml_path)
    assert not sb.calls_for("compras", "insert")
    assert not sb.calls_for("compras_itens", "insert")

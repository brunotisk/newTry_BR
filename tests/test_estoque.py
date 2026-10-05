from servicos import estoque


def test_obter_saldo_sem_linha_retorna_zero(sb):
    sb.responses[("estoque", "select")] = []
    assert estoque.obter_saldo(sb, 10) == 0.0


def test_obter_saldo_existente_converte_para_float(sb):
    sb.responses[("estoque", "select")] = [{"quantidade_atual": "7.5"}]
    assert estoque.obter_saldo(sb, 10) == 7.5


def test_registrar_entrada_atualiza_saldo_e_ledger(sb):
    sb.responses[("estoque", "select")] = [{"quantidade_atual": 5}]
    sb.responses[("estoque", "upsert")] = [{"id": 1}]
    sb.responses[("estoque_movimentos", "insert")] = [{"id": 2}]

    movimento_id = estoque.registrar_movimento(
        sb, produto_id=10, quantidade=3, tipo="entrada",
        data_movimento="2026-10-05", compra_id=20, fluxo="compra_importacao",
    )

    assert movimento_id == 2
    upsert = sb.last_call("estoque", "upsert")
    assert upsert.payload["produto_id"] == 10
    assert upsert.payload["quantidade_atual"] == 8.0
    mov = sb.last_call("estoque_movimentos", "insert")
    assert mov.payload["tipo"] == "entrada"
    assert mov.payload["quantidade"] == 3.0
    assert mov.payload["saldo_apos"] == 8.0
    assert mov.payload["compra_id"] == 20


def test_registrar_saida_reduz_saldo_e_vincula_venda(sb):
    sb.responses[("estoque", "select")] = [{"quantidade_atual": 10}]
    sb.responses[("estoque", "upsert")] = [{"id": 1}]
    sb.responses[("estoque_movimentos", "insert")] = [{"id": 3}]

    estoque.registrar_movimento(
        sb, produto_id=10, quantidade=-4, tipo="saida",
        data_movimento="2026-10-05", venda_id=30, fluxo="venda_importacao",
    )

    assert sb.last_call("estoque", "upsert").payload["quantidade_atual"] == 6.0
    mov = sb.last_call("estoque_movimentos", "insert").payload
    assert mov["tipo"] == "saida"
    assert mov["quantidade"] == -4.0
    assert mov["venda_id"] == 30


def test_registrar_ajuste_delega_para_movimento(sb, monkeypatch):
    capturado = {}
    monkeypatch.setattr(estoque, "registrar_movimento", lambda *a, **kw: capturado.update(kw) or 9)
    assert estoque.registrar_ajuste(sb, 10, -2, "estorno", "2026-10-05", fluxo="venda_exclusao") == 9
    assert capturado["tipo"] == "ajuste"
    assert capturado["quantidade"] == -2
    assert capturado["motivo"] == "estorno"
    assert capturado["fluxo"] == "venda_exclusao"


def test_falha_no_upsert_registra_log_de_falha_e_propaga(sb):
    sb.responses[("estoque", "select")] = [{"quantidade_atual": 1}]
    sb.responses[("estoque", "upsert")] = lambda q: (_ for _ in ()).throw(RuntimeError("falha estoque"))

    import pytest
    with pytest.raises(RuntimeError, match="falha estoque"):
        estoque.registrar_movimento(sb, 10, 1, "entrada", "2026-10-05")

    logs = [c.payload for c in sb.calls_for("logs_automacao", "insert")]
    assert any(log["etapa"] == "ajusta_estoque" and log["sucesso"] is False for log in logs)


def test_falha_no_ledger_registra_log_de_falha_e_nao_esconde_erro(sb):
    sb.responses[("estoque", "select")] = [{"quantidade_atual": 1}]
    sb.responses[("estoque", "upsert")] = [{"id": 1}]
    sb.responses[("estoque_movimentos", "insert")] = lambda q: (_ for _ in ()).throw(RuntimeError("falha ledger"))

    import pytest
    with pytest.raises(RuntimeError, match="falha ledger"):
        estoque.registrar_movimento(sb, 10, 1, "entrada", "2026-10-05")

    logs = [c.payload for c in sb.calls_for("logs_automacao", "insert")]
    assert any(log["etapa"] == "ajusta_movimentacao" and log["sucesso"] is False for log in logs)

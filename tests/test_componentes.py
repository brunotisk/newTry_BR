from componentes.busca_produto import filtrar_por_termo
from componentes.paginacao import get_itens_por_pagina, reset_paginacao


def test_busca_produto_filtra_prefixo_ignorando_zeros():
    produtos = [
        {"id": 1, "codigo_interno": "0024727", "descricao": "Brinco"},
        {"id": 2, "codigo_interno": "0010001", "descricao": "Colar"},
    ]
    encontrados = filtrar_por_termo(produtos, "24727")
    assert [p["id"] for p in encontrados] == [1]


def test_busca_produto_pode_buscar_descricao():
    produtos = [
        {"id": 1, "codigo_interno": "0024727", "descricao": "Brinco Argola"},
        {"id": 2, "codigo_interno": "0010001", "descricao": "Colar Ponto de Luz"},
    ]
    encontrados = filtrar_por_termo(produtos, "ponto de", buscar_descricao=True)
    assert [p["id"] for p in encontrados] == [2]


def test_busca_produto_sem_termo_preserva_lista():
    produtos = [{"id": 1}, {"id": 2}]
    assert filtrar_por_termo(produtos, "") == produtos


def test_reset_paginacao_volta_para_primeira_pagina(monkeypatch):
    import componentes.paginacao as pag
    estado = {}
    monkeypatch.setattr(pag.st, "session_state", estado)
    estado["pagina_atual_produtos"] = 4
    pag.reset_paginacao("produtos")
    assert estado["pagina_atual_produtos"] == 1


def test_get_itens_por_pagina_usa_padrao_quando_estado_invalido(monkeypatch):
    import componentes.paginacao as pag
    estado = {"itens_por_pagina_produtos": 999}
    monkeypatch.setattr(pag.st, "session_state", estado)
    assert get_itens_por_pagina("produtos", padrao=25) == 25

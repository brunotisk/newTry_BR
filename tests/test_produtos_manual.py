"""Testes de servicos/produtos_manual.py: cadastro manual e ajuste de estoque/preços.

Não acessam o Supabase: usam um banco simulado em memória e capturam o log de automação.

    pytest tests/test_produtos_manual.py -v
"""
import copy

import pytest

from servicos import estoque as estoque_mod
from servicos import produtos_manual as pm
from servicos.produtos_manual import (
    MOTIVO_INICIAL,
    MOTIVO_PADRAO_AJUSTE,
    atualizar_estoque_e_precos,
    cadastrar_produto_manual,
    codigo_existe,
    preco_venda_sugerido,
)

DATA = "2026-11-21"


# ----------------------------------------------------------------------
# Banco simulado
# ----------------------------------------------------------------------
class _Resposta:
    def __init__(self, data):
        self.data = data


class _Consulta:
    def __init__(self, banco, tabela):
        self.banco, self.tabela = banco, tabela
        self.op, self.filtros, self.limite = "select", [], None
        self.payload, self.conflito = None, None

    def select(self, colunas="*", **kw):
        self.op = "select"
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def upsert(self, payload, on_conflict=None):
        self.op, self.payload, self.conflito = "upsert", payload, on_conflict
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, coluna, valor):
        self.filtros.append((coluna, valor))
        return self

    def limit(self, n):
        self.limite = n
        return self

    def _casa(self, linha):
        return all(linha.get(c) == v for c, v in self.filtros)

    def execute(self):
        linhas = self.banco.tabelas[self.tabela]
        if self.op == "select":
            achadas = [copy.deepcopy(l) for l in linhas if self._casa(l)]
            return _Resposta(achadas[: self.limite] if self.limite else achadas)

        if self.op == "insert":
            self.banco.enviados[self.tabela].append(copy.deepcopy(self.payload))
            self.banco.proximo_id += 1
            linha = {"id": self.banco.proximo_id, **self.payload}
            linhas.append(linha)
            return _Resposta([copy.deepcopy(linha)])

        if self.op == "delete":
            restantes = [l for l in linhas if not self._casa(l)]
            self.banco.tabelas[self.tabela] = restantes
            return _Resposta([])

        # upsert
        for linha in linhas:
            if linha.get(self.conflito) == self.payload.get(self.conflito):
                linha.update(self.payload)
                return _Resposta([copy.deepcopy(linha)])
        linhas.append(dict(self.payload))
        return _Resposta([copy.deepcopy(self.payload)])


class SupabaseFalso:
    def __init__(self, produtos=None, estoque=None):
        self.tabelas = {
            "produtos": produtos or [],
            "estoque": estoque or [],
            "estoque_movimentos": [],
        }
        self.enviados = {nome: [] for nome in self.tabelas}
        self.proximo_id = 100

    def table(self, nome):
        return _Consulta(self, nome)

    # atalhos
    def produto(self, codigo):
        return next((p for p in self.tabelas["produtos"] if p["codigo_interno"] == codigo), None)

    def estoque(self, produto_id):
        return next((e for e in self.tabelas["estoque"] if e["produto_id"] == produto_id), None)

    @property
    def movimentos(self):
        return self.tabelas["estoque_movimentos"]


@pytest.fixture(autouse=True)
def logs(monkeypatch):
    capturados = []
    monkeypatch.setattr(estoque_mod, "registrar_log", lambda sb, **kw: capturados.append(kw))
    return capturados


@pytest.fixture
def sb():
    return SupabaseFalso(
        produtos=[{"id": 1, "codigo_interno": "0010001", "descricao": "Brinco"}],
        estoque=[{
            "produto_id": 1,
            "quantidade_atual": 10.0,
            "estoque_preco_ultima_compra": 20.0,
            "estoque_preco_venda_sugerida": 40.0,
        }],
    )


# ----------------------------------------------------------------------
# Utilitários
# ----------------------------------------------------------------------
class TestUtilitarios:
    def test_preco_sugerido_e_o_dobro(self):
        assert preco_venda_sugerido(12.5) == 25.0

    @pytest.mark.parametrize("valor", [None, 0, 0.0])
    def test_preco_sugerido_sem_compra_e_none(self, valor):
        assert preco_venda_sugerido(valor) is None

    def test_codigo_existe(self, sb):
        assert codigo_existe(sb, "0010001") is True
        assert codigo_existe(sb, " 0010001 ") is True   # ignora espaços
        assert codigo_existe(sb, "9999") is False


# ----------------------------------------------------------------------
# cadastrar_produto_manual
# ----------------------------------------------------------------------
class TestCadastrar:
    def test_cria_produto_estoque_e_movimento_inicial(self, sb):
        pid = cadastrar_produto_manual(
            sb, "123", "Colar", DATA, categoria_id=3, unidade="UN",
            quantidade=5, preco_compra=10, preco_venda=30,
        )

        prod = sb.produto("123")
        assert prod["id"] == pid
        assert prod["descricao"] == "Colar"
        assert prod["categoria_id"] == 3
        assert prod["unidade"] == "UN"
        assert prod["nr_compras"] == 0

        est = sb.estoque(pid)
        assert est["quantidade_atual"] == 5.0
        assert est["estoque_preco_ultima_compra"] == 10.0
        assert est["estoque_preco_venda_sugerida"] == 30.0

        assert len(sb.movimentos) == 1
        mov = sb.movimentos[0]
        assert mov["produto_id"] == pid
        assert mov["tipo"] == "ajuste"
        assert mov["quantidade"] == 5.0
        assert mov["saldo_apos"] == 5.0
        assert mov["motivo"] == MOTIVO_INICIAL
        assert mov["realizado_por"] == "Manual"
        assert mov["data_movimento"] == DATA
        assert "venda_id" not in mov and "compra_id" not in mov

    def test_sem_preco_de_venda_usa_o_dobro_da_compra(self, sb):
        pid = cadastrar_produto_manual(sb, "123", "Colar", DATA, preco_compra=15)
        assert sb.estoque(pid)["estoque_preco_venda_sugerida"] == 30.0

    def test_preco_de_venda_informado_vence_o_sugerido(self, sb):
        pid = cadastrar_produto_manual(sb, "123", "Colar", DATA, preco_compra=15, preco_venda=99)
        assert sb.estoque(pid)["estoque_preco_venda_sugerida"] == 99.0

    def test_sem_precos_nao_grava_colunas_de_preco(self, sb):
        pid = cadastrar_produto_manual(sb, "123", "Colar", DATA)
        est = sb.estoque(pid)
        assert "estoque_preco_ultima_compra" not in est
        assert "estoque_preco_venda_sugerida" not in est

    def test_quantidade_zero_nao_gera_movimento(self, sb):
        pid = cadastrar_produto_manual(sb, "123", "Colar", DATA, quantidade=0)
        assert sb.movimentos == []
        assert sb.estoque(pid)["quantidade_atual"] == 0

    def test_campos_opcionais_ausentes_nao_sao_enviados(self, sb):
        cadastrar_produto_manual(sb, "123", "Colar", DATA, unidade="  ")
        enviado = sb.enviados["produtos"][0]
        assert "categoria_id" not in enviado
        assert "unidade" not in enviado

    def test_codigo_e_descricao_sao_limpos(self, sb):
        cadastrar_produto_manual(sb, "  123  ", "  Colar  ", DATA)
        prod = sb.produto("123")
        assert prod["descricao"] == "Colar"

    @pytest.mark.parametrize("codigo,descricao", [("", "Colar"), ("123", ""), ("  ", "x"), ("1", "   ")])
    def test_codigo_e_descricao_obrigatorios(self, sb, codigo, descricao):
        with pytest.raises(ValueError, match="código interno e a descrição"):
            cadastrar_produto_manual(sb, codigo, descricao, DATA)
        assert len(sb.tabelas["produtos"]) == 1   # nada gravado

    def test_codigo_duplicado_e_recusado(self, sb):
        with pytest.raises(ValueError, match="Já existe um produto"):
            cadastrar_produto_manual(sb, "0010001", "Outro", DATA)
        assert len(sb.tabelas["produtos"]) == 1
        assert sb.enviados["produtos"] == []

    def test_quantidade_negativa_e_recusada(self, sb):
        with pytest.raises(ValueError, match="negativa"):
            cadastrar_produto_manual(sb, "123", "Colar", DATA, quantidade=-1)
        assert sb.produto("123") is None

    def test_falha_no_movimento_desfaz_o_cadastro(self, sb, monkeypatch):
        def falha(*a, **kw):
            raise RuntimeError("ledger fora do ar")

        monkeypatch.setattr(pm, "registrar_movimento", falha)

        with pytest.raises(RuntimeError, match="ledger fora do ar"):
            cadastrar_produto_manual(sb, "123", "Colar", DATA, quantidade=5, preco_compra=10)

        assert sb.produto("123") is None
        assert all(e["produto_id"] == 1 for e in sb.tabelas["estoque"])  # só o original

    def test_log_registrado_no_fluxo_ajuste_manual(self, sb, logs):
        cadastrar_produto_manual(sb, "123", "Colar", DATA, quantidade=2)
        assert [(l["fluxo"], l["etapa"], l["sucesso"]) for l in logs] == [
            ("ajuste_manual", "ajusta_estoque", True),
            ("ajuste_manual", "ajusta_movimentacao", True),
        ]
        assert len({l["operacao_id"] for l in logs}) == 1


# ----------------------------------------------------------------------
# atualizar_estoque_e_precos — quantidade
# ----------------------------------------------------------------------
class TestAtualizarQuantidade:
    def test_aumentar_gera_ajuste_positivo_com_a_diferenca(self, sb):
        r = atualizar_estoque_e_precos(sb, 1, 15, DATA, None, None)

        assert r["delta"] == 5.0
        assert sb.estoque(1)["quantidade_atual"] == 15.0
        mov = sb.movimentos[0]
        assert mov["tipo"] == "ajuste"
        assert mov["quantidade"] == 5.0
        assert mov["saldo_apos"] == 15.0
        assert mov["motivo"] == MOTIVO_PADRAO_AJUSTE
        assert mov["realizado_por"] == "Manual"

    def test_reduzir_gera_ajuste_negativo(self, sb):
        r = atualizar_estoque_e_precos(sb, 1, 4, DATA, None, None)
        assert r["delta"] == -6.0
        assert sb.estoque(1)["quantidade_atual"] == 4.0
        assert sb.movimentos[0]["quantidade"] == -6.0

    def test_zerar_o_estoque_e_permitido(self, sb):
        atualizar_estoque_e_precos(sb, 1, 0, DATA, None, None)
        assert sb.estoque(1)["quantidade_atual"] == 0.0

    def test_mesma_quantidade_nao_gera_movimento(self, sb):
        r = atualizar_estoque_e_precos(sb, 1, 10, DATA, None, None)
        assert r["delta"] == 0
        assert sb.movimentos == []

    def test_motivo_informado_e_gravado(self, sb):
        atualizar_estoque_e_precos(sb, 1, 12, DATA, None, None, motivo="  contagem física  ")
        assert sb.movimentos[0]["motivo"] == "contagem física"

    def test_quantidade_negativa_e_recusada(self, sb):
        with pytest.raises(ValueError, match="negativa"):
            atualizar_estoque_e_precos(sb, 1, -1, DATA, None, None)
        assert sb.estoque(1)["quantidade_atual"] == 10.0
        assert sb.movimentos == []

    def test_quantidade_fracionada(self, sb):
        r = atualizar_estoque_e_precos(sb, 1, 10.5, DATA, None, None)
        assert r["delta"] == 0.5
        assert sb.estoque(1)["quantidade_atual"] == 10.5

    def test_produto_sem_linha_de_estoque_parte_de_zero(self):
        sb = SupabaseFalso(produtos=[{"id": 7, "codigo_interno": "7", "descricao": "x"}])
        r = atualizar_estoque_e_precos(sb, 7, 3, DATA, 5, None)
        assert r["delta"] == 3.0
        assert sb.estoque(7)["quantidade_atual"] == 3.0
        assert sb.estoque(7)["estoque_preco_ultima_compra"] == 5.0

    def test_log_registra_fluxo_ajuste_manual(self, sb, logs):
        atualizar_estoque_e_precos(sb, 1, 12, DATA, None, None)
        assert {l["fluxo"] for l in logs} == {"ajuste_manual"}
        assert all(l["quantidade"] == 2.0 for l in logs)

    def test_so_afeta_o_produto_informado(self, sb):
        sb.tabelas["estoque"].append({"produto_id": 2, "quantidade_atual": 8.0})
        atualizar_estoque_e_precos(sb, 1, 1, DATA, None, None)
        assert sb.estoque(2)["quantidade_atual"] == 8.0


# ----------------------------------------------------------------------
# atualizar_estoque_e_precos — preço de compra
# ----------------------------------------------------------------------
class TestPrecoCompra:
    def test_altera_preco_de_compra(self, sb):
        r = atualizar_estoque_e_precos(sb, 1, 10, DATA, 25.5, None)
        assert sb.estoque(1)["estoque_preco_ultima_compra"] == 25.5
        assert r["precos_alterados"] == ["estoque_preco_ultima_compra"]

    def test_none_nao_altera(self, sb):
        r = atualizar_estoque_e_precos(sb, 1, 10, DATA, None, None)
        assert sb.estoque(1)["estoque_preco_ultima_compra"] == 20.0
        assert r["precos_alterados"] == []

    def test_mesmo_valor_nao_gera_atualizacao(self, sb):
        r = atualizar_estoque_e_precos(sb, 1, 10, DATA, 20.0, None)
        assert r["precos_alterados"] == []

    def test_arredonda_para_centavos(self, sb):
        atualizar_estoque_e_precos(sb, 1, 10, DATA, 21.456, None)
        assert sb.estoque(1)["estoque_preco_ultima_compra"] == 21.46


# ----------------------------------------------------------------------
# atualizar_estoque_e_precos — preço de venda
# ----------------------------------------------------------------------
class TestPrecoVenda:
    def test_primeiro_ajuste_guarda_original_e_marca_flag(self, sb):
        atualizar_estoque_e_precos(sb, 1, 10, DATA, None, 55)
        est = sb.estoque(1)
        assert est["estoque_preco_venda_sugerida"] == 55.0
        assert est["estoque_preco_venda_original"] == 40.0
        assert est["estoque_flag_ajuste_preco_venda"] is True

    def test_segundo_ajuste_mantem_o_original_e_a_flag(self, sb):
        atualizar_estoque_e_precos(sb, 1, 10, DATA, None, 55)
        atualizar_estoque_e_precos(sb, 1, 10, DATA, None, 60)
        est = sb.estoque(1)
        assert est["estoque_preco_venda_sugerida"] == 60.0
        assert est["estoque_preco_venda_original"] == 40.0
        assert est["estoque_flag_ajuste_preco_venda"] is True

    def test_voltar_ao_original_desmarca_a_flag(self, sb):
        atualizar_estoque_e_precos(sb, 1, 10, DATA, None, 55)
        atualizar_estoque_e_precos(sb, 1, 10, DATA, None, 40)
        est = sb.estoque(1)
        assert est["estoque_preco_venda_sugerida"] == 40.0
        assert est["estoque_flag_ajuste_preco_venda"] is False

    def test_mesmo_valor_nao_altera_nada(self, sb):
        r = atualizar_estoque_e_precos(sb, 1, 10, DATA, None, 40)
        assert r["precos_alterados"] == []
        assert "estoque_preco_venda_original" not in sb.estoque(1)

    def test_zero_ou_none_nao_altera(self, sb):
        atualizar_estoque_e_precos(sb, 1, 10, DATA, None, 0)
        atualizar_estoque_e_precos(sb, 1, 10, DATA, None, None)
        assert sb.estoque(1)["estoque_preco_venda_sugerida"] == 40.0

    def test_produto_sem_preco_anterior_nao_marca_flag(self):
        sb = SupabaseFalso(
            produtos=[{"id": 1, "codigo_interno": "1", "descricao": "x"}],
            estoque=[{"produto_id": 1, "quantidade_atual": 2.0}],
        )
        atualizar_estoque_e_precos(sb, 1, 2, DATA, None, 30)
        est = sb.estoque(1)
        assert est["estoque_preco_venda_sugerida"] == 30.0
        assert "estoque_preco_venda_original" not in est
        assert "estoque_flag_ajuste_preco_venda" not in est


# ----------------------------------------------------------------------
# Tudo junto
# ----------------------------------------------------------------------
def test_quantidade_e_precos_na_mesma_chamada(sb):
    r = atualizar_estoque_e_precos(sb, 1, 20, DATA, 22, 60, motivo="reposição sem NF")

    est = sb.estoque(1)
    assert est["quantidade_atual"] == 20.0
    assert est["estoque_preco_ultima_compra"] == 22.0
    assert est["estoque_preco_venda_sugerida"] == 60.0
    assert r["delta"] == 10.0
    assert set(r["precos_alterados"]) == {
        "estoque_preco_ultima_compra",
        "estoque_preco_venda_sugerida",
        "estoque_preco_venda_original",
        "estoque_flag_ajuste_preco_venda",
    }
    assert sb.movimentos[0]["motivo"] == "reposição sem NF"

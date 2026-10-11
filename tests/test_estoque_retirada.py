"""Testes de servicos/estoque.py: retirada pessoal e estorno.

Não acessam o Supabase: usam um banco simulado em memória (SupabaseFalso) e capturam as
chamadas de log. Cobrem registrar_retirada_pessoal, estornar_retirada_pessoal,
obter_custo_unitario e a compatibilidade de registrar_movimento.

Como rodar (na raiz do projeto, com `pip install pytest`):
    pytest testes/test_estoque_retirada.py -v
"""
import copy
import pathlib
import sys

import pytest

# Garante que a raiz do projeto esteja no sys.path, rode o pytest de onde rodar.
_RAIZ = pathlib.Path(__file__).resolve().parents[1]
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from servicos import estoque as estoque_mod  # noqa: E402
from servicos.estoque import (  # noqa: E402
    PREFIXO_ESTORNO_RETIRADA,
    TIPO_RETIRADA_PESSOAL,
    SaldoInsuficienteError,
    estornar_retirada_pessoal,
    obter_custo_unitario,
    obter_saldo,
    registrar_movimento,
    registrar_retirada_pessoal,
)

DATA = "2026-10-10"


# ----------------------------------------------------------------------
# Banco simulado
# ----------------------------------------------------------------------
class _Resposta:
    def __init__(self, data):
        self.data = data


class _Consulta:
    """Só o que servicos/estoque.py usa: select/eq/limit/insert/upsert/execute."""

    def __init__(self, banco, tabela):
        self.banco, self.tabela = banco, tabela
        self.op, self.filtros, self.limite, self.payload, self.conflito = "select", [], None, None, None

    def select(self, colunas="*"):
        if self.tabela == "estoque_movimentos" and "custo_unitario" in colunas and not self.banco.coluna_custo:
            raise Exception("column estoque_movimentos.custo_unitario does not exist")
        self.op = "select"
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def upsert(self, payload, on_conflict=None):
        self.op, self.payload, self.conflito = "upsert", payload, on_conflict
        return self

    def eq(self, coluna, valor):
        self.filtros.append((coluna, valor))
        return self

    def limit(self, n):
        self.limite = n
        return self

    def execute(self):
        linhas = self.banco.tabelas[self.tabela]
        if self.op == "select":
            achadas = [copy.deepcopy(l) for l in linhas if all(l.get(c) == v for c, v in self.filtros)]
            return _Resposta(achadas[: self.limite] if self.limite else achadas)

        if self.op == "insert":
            self.banco.enviados[self.tabela].append(copy.deepcopy(self.payload))
            if self.tabela == "estoque_movimentos":
                if "custo_unitario" in self.payload and not self.banco.coluna_custo:
                    raise Exception("Could not find the 'custo_unitario' column")
                self.banco.proximo_id += 1
                linha = {"id": self.banco.proximo_id, **self.payload}
            else:
                linha = dict(self.payload)
            linhas.append(linha)
            return _Resposta([copy.deepcopy(linha)])

        # upsert
        for linha in linhas:
            if linha.get(self.conflito) == self.payload.get(self.conflito):
                linha.update(self.payload)
                return _Resposta([copy.deepcopy(linha)])
        linhas.append(dict(self.payload))
        return _Resposta([copy.deepcopy(self.payload)])


class SupabaseFalso:
    def __init__(self, estoque, movimentos=None, coluna_custo=True):
        self.tabelas = {"estoque": estoque, "estoque_movimentos": movimentos or [], "vendas": []}
        self.coluna_custo = coluna_custo
        self.enviados = {nome: [] for nome in self.tabelas}   # payloads de insert, por tabela
        self.tabelas_acessadas = set()
        self.proximo_id = len(self.tabelas["estoque_movimentos"])

    def table(self, nome):
        self.tabelas_acessadas.add(nome)
        return _Consulta(self, nome)

    # atalhos para as asserções
    def saldo(self, produto_id):
        return next(l for l in self.tabelas["estoque"] if l["produto_id"] == produto_id)["quantidade_atual"]

    @property
    def movimentos(self):
        return self.tabelas["estoque_movimentos"]


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------
@pytest.fixture(autouse=True)
def logs(monkeypatch):
    """Captura registrar_log (não grava em logs_automacao) e zera o cache da coluna de custo."""
    capturados = []
    monkeypatch.setattr(estoque_mod, "registrar_log", lambda sb, **kw: capturados.append(kw))
    monkeypatch.setattr(estoque_mod, "_COLUNA_CUSTO_VERIFICADA", False)
    return capturados


@pytest.fixture
def sb():
    return SupabaseFalso(estoque=[
        {"produto_id": 1, "quantidade_atual": 10.0, "estoque_preco_ultima_compra": 20.0},
        {"produto_id": 2, "quantidade_atual": 5.0, "estoque_preco_ultima_compra": None},   # sem custo
        {"produto_id": 3, "quantidade_atual": 2.5, "estoque_preco_ultima_compra": 8.0},
    ])


# ----------------------------------------------------------------------
# registrar_retirada_pessoal — caminho feliz
# ----------------------------------------------------------------------
class TestRegistrarRetiradaPessoal:
    def test_baixa_o_saldo_e_grava_o_movimento(self, sb):
        mov_id = registrar_retirada_pessoal(sb, 1, 3, DATA, motivo="presente")

        assert sb.saldo(1) == 7.0
        assert len(sb.movimentos) == 1
        mov = sb.movimentos[0]
        assert mov["id"] == mov_id
        assert mov["produto_id"] == 1
        assert mov["tipo"] == TIPO_RETIRADA_PESSOAL == "retirada_pessoal"
        assert mov["quantidade"] == -3.0          # sempre negativa no ledger
        assert mov["saldo_apos"] == 7.0
        assert mov["data_movimento"] == DATA
        assert mov["motivo"] == "presente"

    def test_nao_e_venda_nem_compra(self, sb):
        registrar_retirada_pessoal(sb, 1, 1, DATA)

        mov = sb.movimentos[0]
        assert "venda_id" not in mov and "compra_id" not in mov
        assert "vendas" not in sb.tabelas_acessadas
        assert sb.tabelas["vendas"] == []

    def test_realizado_por_padrao_e_manual(self, sb):
        registrar_retirada_pessoal(sb, 1, 1, DATA)
        assert sb.movimentos[0]["realizado_por"] == "Manual"

    def test_realizado_por_pode_ser_informado(self, sb):
        registrar_retirada_pessoal(sb, 1, 1, DATA, realizado_por="Bruno")
        assert sb.movimentos[0]["realizado_por"] == "Bruno"

    def test_motivo_ausente_nao_e_enviado(self, sb):
        registrar_retirada_pessoal(sb, 1, 1, DATA)
        assert "motivo" not in sb.movimentos[0]

    def test_aceita_quantidade_fracionada(self, sb):
        registrar_retirada_pessoal(sb, 3, 0.5, DATA)

        assert sb.saldo(3) == 2.0
        assert sb.movimentos[0]["quantidade"] == -0.5

    def test_retirar_exatamente_o_saldo_zera_o_estoque(self, sb):
        registrar_retirada_pessoal(sb, 1, 10, DATA)

        assert sb.saldo(1) == 0.0
        assert sb.movimentos[0]["saldo_apos"] == 0.0

    def test_retiradas_seguidas_encadeiam_o_saldo(self, sb):
        registrar_retirada_pessoal(sb, 1, 4, DATA)
        registrar_retirada_pessoal(sb, 1, 3, DATA)

        assert [m["saldo_apos"] for m in sb.movimentos] == [6.0, 3.0]
        assert sb.saldo(1) == 3.0

    def test_so_afeta_o_produto_retirado(self, sb):
        registrar_retirada_pessoal(sb, 1, 2, DATA)

        assert sb.saldo(2) == 5.0
        assert sb.saldo(3) == 2.5


# ----------------------------------------------------------------------
# Custo unitário
# ----------------------------------------------------------------------
class TestCustoUnitario:
    def test_usa_o_preco_da_ultima_compra_por_padrao(self, sb):
        registrar_retirada_pessoal(sb, 1, 1, DATA)
        assert sb.movimentos[0]["custo_unitario"] == 20.0

    def test_custo_informado_tem_prioridade(self, sb):
        registrar_retirada_pessoal(sb, 1, 1, DATA, custo_unitario=12.34)
        assert sb.movimentos[0]["custo_unitario"] == 12.34

    def test_produto_sem_custo_fica_nulo(self, sb):
        registrar_retirada_pessoal(sb, 2, 1, DATA)
        assert "custo_unitario" not in sb.movimentos[0]     # ausente = NULL no banco

    def test_obter_custo_unitario(self, sb):
        assert obter_custo_unitario(sb, 1) == 20.0
        assert obter_custo_unitario(sb, 2) is None           # coluna nula
        assert obter_custo_unitario(sb, 999) is None         # sem linha de estoque


# ----------------------------------------------------------------------
# Validações e recusas (nada pode ser gravado)
# ----------------------------------------------------------------------
class TestRecusas:
    @pytest.mark.parametrize("quantidade", [0, -1, -0.5])
    def test_quantidade_precisa_ser_positiva(self, sb, quantidade):
        with pytest.raises(ValueError, match="maior que zero"):
            registrar_retirada_pessoal(sb, 1, quantidade, DATA)

        assert sb.saldo(1) == 10.0
        assert sb.movimentos == []

    def test_saldo_insuficiente(self, sb):
        with pytest.raises(SaldoInsuficienteError, match="disponível 5"):
            registrar_retirada_pessoal(sb, 2, 6, DATA)

        assert sb.saldo(2) == 5.0
        assert sb.movimentos == []

    def test_saldo_insuficiente_e_um_valueerror(self):
        assert issubclass(SaldoInsuficienteError, ValueError)

    def test_produto_sem_linha_de_estoque_tem_saldo_zero(self, sb):
        with pytest.raises(SaldoInsuficienteError, match="disponível 0"):
            registrar_retirada_pessoal(sb, 999, 1, DATA)

        assert sb.movimentos == []

    def test_permitir_saldo_negativo_libera_a_retirada(self, sb):
        registrar_retirada_pessoal(sb, 2, 8, DATA, permitir_saldo_negativo=True)

        assert sb.saldo(2) == -3.0
        assert sb.movimentos[0]["saldo_apos"] == -3.0

    def test_sem_a_coluna_de_custo_falha_antes_de_mexer_no_saldo(self):
        sb = SupabaseFalso(
            estoque=[{"produto_id": 1, "quantidade_atual": 10.0, "estoque_preco_ultima_compra": 20.0}],
            coluna_custo=False,
        )

        with pytest.raises(RuntimeError, match="adicionar_retirada_pessoal.sql"):
            registrar_retirada_pessoal(sb, 1, 1, DATA)

        assert sb.saldo(1) == 10.0          # saldo intacto
        assert sb.movimentos == []

    def test_a_checagem_da_coluna_fica_em_cache(self, sb):
        registrar_retirada_pessoal(sb, 1, 1, DATA)
        assert estoque_mod._COLUNA_CUSTO_VERIFICADA is True

        # Se a checagem rodasse de novo, a consulta à coluna falharia. O produto 2 não tem
        # custo (o insert não envia a coluna), então só passa se a verificação estiver em cache.
        sb.coluna_custo = False
        registrar_retirada_pessoal(sb, 2, 1, DATA)

        assert sb.saldo(2) == 4.0


# ----------------------------------------------------------------------
# Log de automação
# ----------------------------------------------------------------------
class TestLog:
    def test_registra_as_duas_etapas_no_fluxo_retirada_pessoal(self, sb, logs):
        registrar_retirada_pessoal(sb, 1, 2, DATA)

        assert [(l["fluxo"], l["etapa"], l["sucesso"]) for l in logs] == [
            ("retirada_pessoal", "ajusta_estoque", True),
            ("retirada_pessoal", "ajusta_movimentacao", True),
        ]
        assert all(l["produto_id"] == 1 and l["quantidade"] == -2.0 for l in logs)

    def test_operacao_id_informado_e_propagado(self, sb, logs):
        registrar_retirada_pessoal(sb, 1, 1, DATA, operacao_id="op-123")
        assert {l["operacao_id"] for l in logs} == {"op-123"}

    def test_sem_operacao_id_as_etapas_compartilham_um_novo(self, sb, logs):
        registrar_retirada_pessoal(sb, 1, 1, DATA)
        ids = {l["operacao_id"] for l in logs}
        assert len(ids) == 1 and None not in ids

    def test_recusa_nao_gera_log(self, sb, logs):
        with pytest.raises(SaldoInsuficienteError):
            registrar_retirada_pessoal(sb, 2, 99, DATA)
        assert logs == []


# ----------------------------------------------------------------------
# estornar_retirada_pessoal
# ----------------------------------------------------------------------
class TestEstorno:
    def test_devolve_o_saldo_com_ajuste_positivo_automatico(self, sb):
        retirada_id = registrar_retirada_pessoal(sb, 1, 3, DATA)

        estorno_id = estornar_retirada_pessoal(sb, retirada_id, "2026-10-11")

        assert sb.saldo(1) == 10.0
        est = next(m for m in sb.movimentos if m["id"] == estorno_id)
        assert est["tipo"] == "ajuste"
        assert est["quantidade"] == 3.0                               # positivo
        assert est["motivo"] == f"{PREFIXO_ESTORNO_RETIRADA}{retirada_id}"
        assert est["realizado_por"] == "Automático"
        assert est["data_movimento"] == "2026-10-11"
        assert est["custo_unitario"] == 20.0                          # mesmo custo da retirada

    def test_a_retirada_original_permanece_no_historico(self, sb):
        retirada_id = registrar_retirada_pessoal(sb, 1, 3, DATA)
        estornar_retirada_pessoal(sb, retirada_id, DATA)

        original = next(m for m in sb.movimentos if m["id"] == retirada_id)
        assert original["tipo"] == "retirada_pessoal" and original["quantidade"] == -3.0

    def test_so_pode_ser_estornada_uma_vez(self, sb):
        retirada_id = registrar_retirada_pessoal(sb, 1, 3, DATA)
        estornar_retirada_pessoal(sb, retirada_id, DATA)

        with pytest.raises(ValueError, match="já foi estornada"):
            estornar_retirada_pessoal(sb, retirada_id, DATA)

        assert sb.saldo(1) == 10.0          # não devolveu duas vezes

    def test_estorno_de_retirada_sem_custo_nao_envia_custo(self, sb):
        retirada_id = registrar_retirada_pessoal(sb, 2, 1, DATA)
        estorno_id = estornar_retirada_pessoal(sb, retirada_id, DATA)

        est = next(m for m in sb.movimentos if m["id"] == estorno_id)
        assert "custo_unitario" not in est

    def test_retirada_inexistente(self, sb):
        with pytest.raises(ValueError, match="não encontrada"):
            estornar_retirada_pessoal(sb, 9999, DATA)

    def test_so_estorna_retirada_pessoal(self, sb):
        sb.movimentos.append({"id": 50, "produto_id": 1, "tipo": "saida", "quantidade": -1.0})

        with pytest.raises(ValueError, match="não é uma retirada pessoal"):
            estornar_retirada_pessoal(sb, 50, DATA)

        assert sb.saldo(1) == 10.0

    def test_estorno_de_uma_nao_afeta_a_outra(self, sb):
        r1 = registrar_retirada_pessoal(sb, 1, 2, DATA)
        r2 = registrar_retirada_pessoal(sb, 1, 3, DATA)

        estornar_retirada_pessoal(sb, r1, DATA)

        assert sb.saldo(1) == 7.0           # 10 - 2 - 3 + 2
        estornar_retirada_pessoal(sb, r2, DATA)   # a outra ainda pode ser estornada
        assert sb.saldo(1) == 10.0

    def test_registra_log_no_fluxo_de_estorno(self, sb, logs):
        retirada_id = registrar_retirada_pessoal(sb, 1, 1, DATA)
        logs.clear()

        estornar_retirada_pessoal(sb, retirada_id, DATA, operacao_id="op-est")

        assert {l["fluxo"] for l in logs} == {"retirada_pessoal_estorno"}
        assert {l["operacao_id"] for l in logs} == {"op-est"}


# ----------------------------------------------------------------------
# Compatibilidade de registrar_movimento
# ----------------------------------------------------------------------
class TestCompatibilidade:
    def test_sem_custo_nem_realizado_por_nada_disso_e_enviado(self, sb):
        registrar_movimento(sb, 1, 5, "entrada", DATA, compra_id=7)

        enviado = sb.enviados["estoque_movimentos"][0]
        assert "custo_unitario" not in enviado and "realizado_por" not in enviado
        assert enviado["compra_id"] == 7
        assert sb.saldo(1) == 15.0

    def test_fluxo_padrao_para_o_tipo_retirada_pessoal(self, sb, logs):
        registrar_movimento(sb, 1, -1, "retirada_pessoal", DATA)
        assert {l["fluxo"] for l in logs} == {"retirada_pessoal"}

    def test_obter_saldo_sem_linha_e_zero(self, sb):
        assert obter_saldo(sb, 999) == 0.0

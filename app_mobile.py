"""Interface simplificada para celular: vendas, clientes e histórico de vendas.

Ponto de entrada separado do app.py (mesmo repositório, mesmo banco, mesmo login):

    streamlit run app_mobile.py

Estrutura:
    app_mobile.py                      roteamento (este arquivo)
    telas_mobile/estilo.py             visual: tira o "cara de Streamlit" e estiliza
    telas_mobile/navegacao.py          cabeçalho (título + Sair) e barra de navegação inferior
    telas_mobile/comum.py              utilidades, leituras em cache e estado compartilhado
    telas_mobile/vendas.py             nova venda
    telas_mobile/clientes.py           consulta e cadastro de clientes
    telas_mobile/historico_vendas.py   histórico de vendas

Requer Streamlit >= 1.39 (container com key, usado para fixar a barra inferior).
"""
import streamlit as st

# set_page_config precisa ser o primeiro comando do Streamlit.
st.set_page_config(
    page_title="Semijoias",
    page_icon="💎",
    layout="centered",
    initial_sidebar_state="collapsed",
)

from auth import autenticar_usuario  # noqa: E402
from telas_mobile.clientes import tela_clientes_mobile  # noqa: E402
from telas_mobile.comum import manter_estado, processar_resets  # noqa: E402
from telas_mobile.estilo import aplicar_estilo  # noqa: E402
from telas_mobile.historico_vendas import tela_historico_vendas_mobile  # noqa: E402
from telas_mobile.navegacao import aba_atual, barra_inferior, cabecalho  # noqa: E402
from telas_mobile.vendas import tela_vendas_mobile  # noqa: E402

# nome da aba -> (ícone, título da tela, função). A ordem aqui é a ordem da barra.
ROTAS = {
    "Venda": ("💰", "Nova venda", tela_vendas_mobile),
    "Clientes": ("👤", "Clientes", tela_clientes_mobile),
    "Histórico": ("🧾", "Histórico de vendas", tela_historico_vendas_mobile),
}


def main() -> None:
    autenticar_usuario()
    aplicar_estilo()
    processar_resets()
    manter_estado()

    atual = aba_atual(list(ROTAS))
    _icone, titulo, tela = ROTAS[atual]
    cabecalho(titulo)
    tela()
    barra_inferior({nome: icone for nome, (icone, _t, _f) in ROTAS.items()}, atual)


main()

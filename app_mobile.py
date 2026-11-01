"""Interface simplificada para celular: vendas, clientes, histórico e configurações.

Ponto de entrada separado do app.py (mesmo repositório, mesmo banco, mesmo login):

    streamlit run app_mobile.py

Estrutura:
    app_mobile.py                      roteamento (este arquivo)
    tema.py                            tema claro/escuro (compartilhado com app.py)
    telas_mobile/estilo.py             visual: tira o "cara de Streamlit" e estiliza
    telas_mobile/cabecalho.py          cabeçalho: marca e título da tela
    telas_mobile/navegacao.py          barra de navegação inferior
    telas_mobile/comum.py              utilidades, leituras em cache e estado compartilhado
    telas_mobile/vendas.py             nova venda
    telas_mobile/clientes.py           consulta e cadastro de clientes
    telas_mobile/historico_vendas.py   histórico de vendas
    telas_mobile/configuracoes.py      tema, sair da conta e ambiente

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
from tema import aplicar_tema  # noqa: E402
from telas_mobile.cabecalho import cabecalho  # noqa: E402
from telas_mobile.clientes import tela_clientes_mobile  # noqa: E402
from telas_mobile.comum import manter_estado, processar_resets  # noqa: E402
from telas_mobile.configuracoes import tela_configuracoes_mobile  # noqa: E402
from telas_mobile.estilo import aplicar_estilo  # noqa: E402
from telas_mobile.historico_vendas import tela_historico_vendas_mobile  # noqa: E402
from telas_mobile.navegacao import aba_atual, barra_inferior  # noqa: E402
from telas_mobile.vendas import tela_vendas_mobile  # noqa: E402

# nome da aba -> (ícone, título da tela, função). A ordem aqui é a ordem da barra.
ROTAS = {
    "Venda": ("💰", "Nova venda", tela_vendas_mobile),
    "Clientes": ("👤", "Clientes", tela_clientes_mobile),
    "Histórico": ("🧾", "Histórico de vendas", tela_historico_vendas_mobile),
    "Config": ("⚙️", "Configurações", tela_configuracoes_mobile),
}


def main() -> None:
    # Ativa persistência de autenticação por cookie apenas na interface mobile.
    st.session_state["mobile_auth"] = True
    autenticar_usuario()
    aplicar_estilo()
    aplicar_tema()  # depois de aplicar_estilo(), para o tema prevalecer
    processar_resets()
    manter_estado()

    atual = aba_atual(list(ROTAS))
    _icone, titulo, tela = ROTAS[atual]
    cabecalho(titulo)
    tela()
    barra_inferior({nome: icone for nome, (icone, _t, _f) in ROTAS.items()}, atual)


main()

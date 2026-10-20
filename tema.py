import streamlit as st

OPCOES = ["🌗 Automático", "☀️ Claro", "🌙 Escuro"]

# Botões "secundários" apenas: os primários mantêm a cor de destaque do app.
_BTN = (
    ':not([kind^="primary"]):not([data-testid^="stBaseButton-primary"])'
)

CSS_ESCURO = """
<style>
:root {
    color-scheme: dark;
    --m-bar-bg: #0e1117;        /* barra de navegação do app mobile */
    --m-accent-text: #c9a46b;
    --m-nav-text: #fafafa;
}
.stApp, [data-testid="stHeader"] { background-color: #0e1117 !important; }
[data-testid="stSidebar"] { background-color: #161a23 !important; }
.stApp, .stApp p, .stApp span, .stApp label, .stApp li,
.stApp h1, .stApp h2, .stApp h3, .stApp h4,
.stApp [data-testid="stMarkdownContainer"],
.stApp [data-testid="stCaptionContainer"],
.stApp [data-testid="stMetricValue"],
.stApp [data-testid="stMetricLabel"] { color: #fafafa !important; }
input, textarea, [data-baseweb="select"] > div,
[data-baseweb="input"], [data-baseweb="textarea"] {
    background-color: #262730 !important; color: #fafafa !important;
}
[data-baseweb="popover"] *, [data-baseweb="menu"] {
    background-color: #262730 !important; color: #fafafa !important;
}
[data-testid="stVerticalBlockBorderWrapper"] { border-color: #3a3f4b !important; }
hr { border-color: #3a3f4b !important; }
.stButton > button__BTN__, .stFormSubmitButton > button__BTN__ {
    background-color: #262730 !important; color: #fafafa !important;
    border-color: #3a3f4b !important;
}
.stApp button[kind^="primary"] p, .stApp button[kind^="primary"] span,
.stApp button[data-testid^="stBaseButton-primary"] p,
.stApp button[data-testid^="stBaseButton-primary"] span { color: #ffffff !important; }
[data-testid="stAlert"] { background-color: #1e2a3a !important; }
[data-testid="stAlert"] * { color: #fafafa !important; }
</style>
""".replace("__BTN__", _BTN)

CSS_CLARO = """
<style>
:root {
    color-scheme: light;
    --m-bar-bg: #ffffff;        /* barra de navegação do app mobile */
    --m-accent-text: #8a6a35;
    --m-nav-text: #1f2430;
    --m-line: #c8cdd6;          /* bordas de cartões, métricas e barra (app mobile) */
}
.stApp, [data-testid="stHeader"] { background-color: #ffffff !important; }
[data-testid="stSidebar"] { background-color: #f0f2f6 !important; }
.stApp, .stApp p, .stApp span, .stApp label, .stApp li,
.stApp h1, .stApp h2, .stApp h3, .stApp h4,
.stApp [data-testid="stMarkdownContainer"],
.stApp [data-testid="stCaptionContainer"],
.stApp [data-testid="stMetricValue"],
.stApp [data-testid="stMetricLabel"] { color: #1f2430 !important; }
input, textarea, [data-baseweb="select"] > div,
[data-baseweb="input"], [data-baseweb="textarea"] {
    background-color: #ffffff !important; color: #1f2430 !important;
}
[data-baseweb="popover"] *, [data-baseweb="menu"] {
    background-color: #ffffff !important; color: #1f2430 !important;
}
/* bordas visíveis: campos brancos sobre fundo branco precisam de contorno */
[data-baseweb="input"], [data-baseweb="textarea"], [data-baseweb="select"] > div {
    border: 1px solid #c8cdd6 !important;
}
[data-testid="stVerticalBlockBorderWrapper"] { border-color: #c8cdd6 !important; }
hr { border-color: #c8cdd6 !important; }
.stButton > button__BTN__, .stFormSubmitButton > button__BTN__ {
    background-color: #ffffff !important; color: #1f2430 !important;
    border-color: #c8cdd6 !important;
}
.stApp button[kind^="primary"] p, .stApp button[kind^="primary"] span,
.stApp button[data-testid^="stBaseButton-primary"] p,
.stApp button[data-testid^="stBaseButton-primary"] span { color: #ffffff !important; }
[data-testid="stAlert"] { background-color: #e8f0fe !important; }
[data-testid="stAlert"] * { color: #1f2430 !important; }
</style>
""".replace("__BTN__", _BTN)


def aplicar_tema():
    """Aplica o CSS do tema escolhido. 'Automático' não injeta nada
    e deixa o Streamlit seguir o tema do dispositivo."""
    escolha = st.session_state.get("tema", OPCOES[0])
    if escolha == "🌙 Escuro":
        st.markdown(CSS_ESCURO, unsafe_allow_html=True)
    elif escolha == "☀️ Claro":
        st.markdown(CSS_CLARO, unsafe_allow_html=True)


def seletor_tema_sidebar():
    with st.sidebar:
        st.radio("Tema", OPCOES, key="tema", horizontal=False)
    aplicar_tema()


def seletor_tema_persistente():
    """Seletor de tema para telas que não ficam sempre visíveis (app mobile).

    O valor fica em st.session_state["tema"], uma chave comum, e não na chave do
    widget. Assim a escolha não se perde quando o usuário sai da tela de
    configurações (o Streamlit apaga o estado de widgets que deixam de ser
    desenhados)."""
    atual = st.session_state.get("tema", OPCOES[0])
    if atual not in OPCOES:
        atual = OPCOES[0]

    def _salvar():
        st.session_state["tema"] = st.session_state["_tema_radio"]

    st.radio(
        "Tema",
        OPCOES,
        index=OPCOES.index(atual),
        key="_tema_radio",
        on_change=_salvar,
    )

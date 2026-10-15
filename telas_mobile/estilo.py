"""Visual do app mobile: esconde o "cara de Streamlit" (barra lateral, cabeçalho, rodapé)
e dá acabamento de app (cartões, botões grandes, barra de navegação fixa embaixo).

As cores de fundo e de texto continuam as do tema do Streamlit (claro/escuro do aparelho);
aqui só entram o destaque (dourado) e os acabamentos.
"""
import streamlit as st

CSS = """
<style>
:root {
  --m-accent: #b08d57;
  --m-accent-soft: rgba(176, 141, 87, .16);
  --m-line: rgba(128, 128, 128, .28);
  --m-bar-bg: #ffffff;
}
@media (prefers-color-scheme: dark) { :root { --m-bar-bg: #0e1117; } }

/* --- some o que denuncia o Streamlit --- */
header[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"],
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"],
[data-testid="collapsedControl"], [data-testid="stStatusWidget"],
footer, #MainMenu { display: none !important; }

/* --- página: coluna estreita, com espaço embaixo para a barra fixa --- */
.block-container {
  max-width: 640px;
  padding: .9rem .9rem calc(6.2rem + env(safe-area-inset-bottom)) !important;
}
h1, h2, h3, h4, h5 { letter-spacing: -.01em; }

/* --- colunas: o Streamlit empilha no celular; aqui elas ficam lado a lado --- */
[data-testid="stHorizontalBlock"] { flex-direction: row !important; flex-wrap: nowrap !important; gap: .6rem !important; }
[data-testid="stColumn"] { min-width: 0 !important; }

/* --- cartões e campos --- */
[data-testid="stVerticalBlockBorderWrapper"] { border-radius: 14px; border-color: var(--m-line); }
[data-testid="stVerticalBlockBorderWrapper"] [data-testid="stVerticalBlock"] { gap: .35rem; }
[data-testid="stMetric"] {
  border: 1px solid var(--m-line); border-radius: 14px; padding: .7rem .9rem;
}
[data-testid="stMetricValue"] { font-size: 1.45rem; }
[data-testid="stMetricLabel"] { font-size: .85rem; }
input, textarea { font-size: 16px !important; }          /* evita o zoom automático no iOS */
[data-baseweb="select"] > div, [data-baseweb="input"], [data-baseweb="base-input"] { border-radius: 10px; }

/* --- botões: alvo de toque grande, cantos arredondados, destaque dourado --- */
.stButton > button, .stLinkButton > a {
  min-height: 3rem; font-size: 1rem; border-radius: 12px;
}
button[kind="primary"], button[data-testid="stBaseButton-primary"] {
  background: var(--m-accent) !important; border-color: var(--m-accent) !important; color: #fff !important;
}

/* --- cabeçalho: título da tela + Sair, sempre na mesma linha --- */
.st-key-m_topo [data-testid="stHorizontalBlock"] { flex-direction: row !important; flex-wrap: nowrap !important; align-items: center; }
.st-key-m_topo [data-testid="stColumn"] { min-width: 0 !important; width: auto !important; }
.st-key-m_topo [data-testid="stColumn"]:first-child { flex: 1 1 auto !important; }
.st-key-m_topo [data-testid="stColumn"]:last-child { flex: 0 0 auto !important; }
.st-key-m_topo h3 { margin: 0; padding: 0; font-size: 1.35rem; }
.st-key-m_topo button { min-height: 2.4rem; padding: .1rem .8rem; background: transparent; border: 1px solid var(--m-line); }

/* --- barra de navegação fixa embaixo --- */
.st-key-m_nav {
  position: fixed; left: 0; right: 0; bottom: 0; z-index: 1000;
  background: var(--m-bar-bg);
  border-top: 1px solid var(--m-line);
  box-shadow: 0 -4px 16px rgba(0, 0, 0, .07);
  padding: .35rem .6rem calc(.35rem + env(safe-area-inset-bottom));
}
.st-key-m_nav [data-testid="stHorizontalBlock"] {
  flex-direction: row !important; flex-wrap: nowrap !important; gap: .3rem !important;
  max-width: 640px; margin: 0 auto;
}
.st-key-m_nav [data-testid="stColumn"] { min-width: 0 !important; width: auto !important; flex: 1 1 0 !important; }
.st-key-m_nav button {
  min-height: 3.6rem; padding: .25rem .1rem; border: none !important; box-shadow: none !important;
  background: transparent !important; color: inherit !important; border-radius: 12px; opacity: .65;
}
.st-key-m_nav button:hover { opacity: 1; }
/* ícone em cima, rótulo embaixo */
.st-key-m_nav button > div > span { flex-direction: column; gap: .05rem; }
.st-key-m_nav [data-testid="stIconEmoji"] { font-size: 1.35rem; line-height: 1.2; }
.st-key-m_nav [data-testid="stMarkdownContainer"] p { font-size: .72rem; line-height: 1.1; margin: 0; }
.st-key-m_nav button[kind="primary"], .st-key-m_nav button[data-testid="stBaseButton-primary"] {
  background: var(--m-accent-soft) !important; color: var(--m-accent) !important; opacity: 1; font-weight: 700;
}
</style>
"""


def aplicar_estilo() -> None:
    st.markdown(CSS, unsafe_allow_html=True)

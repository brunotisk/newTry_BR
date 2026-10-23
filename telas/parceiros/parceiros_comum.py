"""Telas de Parceiros: utilitários compartilhados (formatação, menu, mensagens, painel).

Sem dependência de parceiros_maleta.py nem de parceiros_fechamento.py."""
from datetime import date, datetime
import html
import unicodedata

import streamlit as st

from db import supabase


def _fragmento(func):
    """@st.fragment quando disponível (Streamlit >= 1.37; antes, experimental_fragment).

    Um fragmento reexecuta SÓ a si mesmo quando um widget dentro dele muda, sem
    refazer o resto da página nem suas consultas ao banco. Em versões sem suporte
    a função roda normalmente (apenas mais lenta)."""
    deco = getattr(st, "fragment", None) or getattr(st, "experimental_fragment", None)
    return deco(func) if deco else func

TAM_FOTO = 56             # px de exibição

_CSS_TABELA_FOTOS = (
    "<style>"
    ".tab-fotos{width:100%;border-collapse:collapse;font-size:0.9rem}"
    ".tab-fotos th{text-align:left;padding:6px 8px;border-bottom:1px solid rgba(128,128,128,.45);font-weight:600}"
    ".tab-fotos td{padding:6px 8px;border-bottom:1px solid rgba(128,128,128,.2);vertical-align:middle}"
    ".tab-fotos .num{text-align:right;white-space:nowrap}"
    f".tab-fotos img{{width:{TAM_FOTO}px;height:{TAM_FOTO}px;object-fit:cover;border-radius:6px;display:block}}"
    f".tab-fotos .sem-foto{{width:{TAM_FOTO}px;height:{TAM_FOTO}px;border-radius:6px;display:flex;"
    "align-items:center;justify-content:center;font-size:0.7rem;opacity:.6;"
    "border:1px dashed rgba(128,128,128,.6)}"
    "</style>"
)

_CSS_NAVEGACAO = (
    "<style>"
    ".nav-chips{display:flex;flex-wrap:wrap;gap:.4rem;margin:.1rem 0 .7rem}"
    ".nav-chip{display:inline-flex;align-items:center;padding:.2rem .7rem;border-radius:999px;"
    "font-size:.82rem;border:1px solid rgba(128,128,128,.35);background:rgba(128,128,128,.08)}"
    ".nav-chip.ok{border-color:rgba(40,167,69,.55);background:rgba(40,167,69,.10)}"
    ".nav-chip.atencao{border-color:rgba(255,160,0,.65);background:rgba(255,193,7,.14)}"
    ".nav-passos{display:flex;gap:.3rem;margin:.2rem 0 .9rem;flex-wrap:wrap}"
    ".nav-passo{flex:1 1 0;min-width:8rem;text-align:center;padding:.35rem .5rem;border-radius:8px;"
    "font-size:.8rem;border:1px solid rgba(128,128,128,.3);opacity:.55}"
    ".nav-passo.feito{opacity:.9;border-color:rgba(40,167,69,.55);background:rgba(40,167,69,.10)}"
    ".nav-passo.atual{opacity:1;font-weight:700;border-color:rgba(0,123,255,.75);background:rgba(0,123,255,.14)}"
    "</style>"
)

def _injetar_css() -> None:
    """CSS das tabelas com foto e da navegação, enviado UMA vez por execução (antes cada
    tabela HTML repetia o bloco <style> inteiro)."""
    st.markdown(_CSS_TABELA_FOTOS + _CSS_NAVEGACAO, unsafe_allow_html=True)

def _html_chips(chips: list) -> str:
    """chips: lista de (texto, classe) com classe '', 'ok' ou 'atencao'."""
    itens = "".join(
        f'<span class="nav-chip {classe}">{html.escape(texto)}</span>' for texto, classe in chips
    )
    return f'<div class="nav-chips">{itens}</div>'

def _html_passos(passos: list, atual: int) -> str:
    """Indicador de etapas: as anteriores à atual aparecem concluídas (atual=-1: nenhuma)."""
    itens = []
    for i, nome in enumerate(passos):
        classe = "atual" if i == atual else ("feito" if 0 <= atual and i < atual else "")
        marca = "✓ " if classe == "feito" else f"{i + 1}. "
        itens.append(f'<div class="nav-passo {classe}">{marca}{html.escape(nome)}</div>')
    return f'<div class="nav-passos">{"".join(itens)}</div>'

def _menu(rotulo: str, opcoes: list, key: str, formatar=None) -> str:
    """Menu de navegação: controle segmentado (Streamlit >= 1.40) ou, se não existir,
    radio horizontal. Sempre devolve uma opção válida (não deixa 'desmarcar')."""
    formatar = formatar or str
    ultimo = f"{key}__ultimo"
    padrao = st.session_state.get(ultimo, opcoes[0])
    segmentado = getattr(st, "segmented_control", None)
    if segmentado is None:
        if st.session_state.get(key) not in opcoes:
            st.session_state[key] = padrao if padrao in opcoes else opcoes[0]
        escolhida = st.radio(
            rotulo, opcoes, horizontal=True, key=key,
            format_func=formatar, label_visibility="collapsed",
        )
    else:
        if st.session_state.get(key) not in opcoes:
            st.session_state[key] = padrao if padrao in opcoes else opcoes[0]

        def _nao_desmarcar():
            if st.session_state.get(key) is None:  # clicar na opção ativa a desmarcaria
                st.session_state[key] = st.session_state.get(ultimo, opcoes[0])

        escolhida = segmentado(
            rotulo, opcoes, selection_mode="single", key=key, format_func=formatar,
            on_change=_nao_desmarcar, label_visibility="collapsed",
        )
        if escolhida not in opcoes:
            escolhida = padrao if padrao in opcoes else opcoes[0]
    st.session_state[ultimo] = escolhida
    return escolhida

def _fmt_moeda(valor) -> str:
    return (
        f"R$ {float(valor or 0):,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )

def _parse_data_segura(valor):
    """Converte 'YYYY-MM-DD...' (ou vazio/None/inválido) em date, sem lançar exceção."""
    texto = str(valor or "")[:10]
    if not texto:
        return None
    try:
        return datetime.strptime(texto, "%Y-%m-%d").date()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Produtos enviados (envios de produtos ao parceiro)
# ---------------------------------------------------------------------------

def _fmt_data(valor) -> str:
    d = _parse_data_segura(valor)
    return d.strftime("%d/%m/%Y") if d else "-"

def _fmt_qtd(valor) -> str:
    return f"{float(valor or 0):g}"

def _fmt_qtd_ou_traco(valor) -> str:
    """'-' quando não há valor gravado (ex.: envios fechados na versão anterior)."""
    return "-" if valor is None else _fmt_qtd(valor)

def _iguais(a: float, b: float) -> bool:
    return abs(float(a) - float(b)) < 1e-9

# Ciclo de vida de um envio (parcerias_envios.status):
#   Aberto   -> montagem da maleta: escolher produtos/quantidades e conferir a saída.
#   Enviado  -> maleta com o parceiro; dá para adicionar/remover produtos (cada mudança
#               vira uma movimentação) até a maleta voltar.
#   Fechado  -> maleta recebida e conferida (enviado x vendido x devolvido).
# Existe no máximo UM envio ativo (Aberto ou Enviado) por parceiro.
STATUS_ATIVOS = ("Aberto", "Enviado")

def _flash(tipo: str, mensagem: str) -> None:
    """Mensagem que sobrevive ao st.rerun() (aparece no topo da aba)."""
    st.session_state["parceiros_flash"] = (tipo, mensagem)

def _mostrar_flash() -> None:
    flash = st.session_state.pop("parceiros_flash", None)
    if flash:
        tipo, mensagem = flash
        getattr(st, tipo, st.info)(mensagem)

def _norm(texto) -> str:
    """Minúsculas e sem acentos: 'Coração' e 'coracao' passam a ser a mesma busca."""
    base = unicodedata.normalize("NFKD", str(texto or ""))
    return "".join(c for c in base if not unicodedata.combining(c)).casefold().strip()

# ---------------------------------------------------------------------------
# Leituras da navegação (em cache)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=30, show_spinner=False)
def _buscar_parceiros_ativos() -> list:
    return (
        supabase.table("parceiros")
        .select(
            "id, nome, canal_id, telefone, email, percentual_comissao, "
            "observacao, ativo, canais_venda(nome)"
        )
        .eq("ativo", True)
        .order("nome")
        .execute()
        .data
        or []
    )

@st.cache_data(ttl=60, show_spinner=False)
def _panorama_parceiros() -> dict:
    """{parceiro_id: {'ciclo': ciclo de comissão aberto, 'envio': maleta ativa}}.
    Duas consultas para TODOS os parceiros; alimenta o painel inicial, os chips do
    cabeçalho e os rótulos do menu sem consultar nada por parceiro."""
    try:
        ciclos = (
            supabase.table("parcerias_fechamentos")
            .select("parceiro_id, numero_ciclo, data_inicio")
            .eq("status", "Aberto")
            .execute().data or []
        )
        envios = (
            supabase.table("parcerias_envios")
            .select("parceiro_id, numero_envio, status, data_envio")
            .in_("status", list(STATUS_ATIVOS))
            .execute().data or []
        )
    except Exception:
        return {}
    panorama: dict = {}
    for c in ciclos:
        panorama.setdefault(c["parceiro_id"], {})["ciclo"] = c
    for e in envios:
        panorama.setdefault(e["parceiro_id"], {})["envio"] = e
    return panorama

def _dias_desde(valor) -> int:
    d = _parse_data_segura(valor)
    return max((date.today() - d).days, 0) if d else 0

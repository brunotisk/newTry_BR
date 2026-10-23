"""Tela de Parceiros: escolha do parceiro e navegação entre Fechamento e Maleta.

Ponto de entrada (usado por vendas.py): secao_parceiros()."""
import streamlit as st

from telas.parceiros.parceiros_comum import (
    _buscar_parceiros_ativos,
    _dias_desde,
    _fmt_data,
    _html_chips,
    _injetar_css,
    _menu,
    _panorama_parceiros,
)
from telas.parceiros.parceiros_fechamento import (
    _invalidar_cache_fechamento,
    secao_fechamento,
)
from telas.parceiros.parceiros_maleta import (
    _invalidar_cache_envios,
    _tem_pdfs_maleta,
    _vendido_por_produto,
    secao_maleta,
)


def _atualizar_dados() -> None:
    """Botão 🔄 do cabeçalho: descarta TODAS as leituras em cache (inclusive o que foi
    alterado em outras telas: parceiros, vendas, estoque)."""
    _invalidar_cache_envios()
    _invalidar_cache_fechamento()
    _vendido_por_produto.clear()
    _buscar_parceiros_ativos.clear()
    _tem_pdfs_maleta.clear()

def _limpar_estado_parceiro() -> None:
    """Remove do session_state tudo que pertence ao parceiro atualmente selecionado."""
    prefixos = ("parceiros_", "historico_ciclos_", "retirada_auto_obs_")
    for chave in list(st.session_state.keys()):
        if isinstance(chave, str) and chave.startswith(prefixos):
            del st.session_state[chave]

def _selecionar_parceiro(nome) -> None:
    _limpar_estado_parceiro()
    if nome:
        st.session_state["parceiros_selecionado"] = nome

def _desmarcar_parceiro() -> None:
    _selecionar_parceiro(None)

def _cb_escolher_parceiro(chave: str) -> None:
    """on_change dos seletores de parceiro. Roda ANTES do rerun: a tela já abre no parceiro
    novo, sem o 'rerun extra' que existia (selectbox -> st.rerun)."""
    _selecionar_parceiro(st.session_state.get(chave))

def _cb_escolher_do_painel() -> None:
    estado = st.session_state.get("parceiros_painel")
    nomes = st.session_state.get("parceiros_painel_nomes") or []
    try:
        linhas = estado["selection"]["rows"]
    except Exception:
        linhas = []
    if linhas and linhas[0] < len(nomes):
        _selecionar_parceiro(nomes[linhas[0]])

def _txt_ciclo(info: dict) -> str:
    c = info.get("ciclo")
    return f"#{c['numero_ciclo']} aberto desde {_fmt_data(c.get('data_inicio'))}" if c else "sem ciclo aberto"

def _txt_maleta(info: dict) -> str:
    e = info.get("envio")
    if not e:
        return "sem maleta ativa"
    if e.get("status") == "Enviado":
        dias = _dias_desde(e.get("data_envio"))
        return f"#{e['numero_envio']} com o parceiro há {dias} dia(s)"
    return f"#{e['numero_envio']} em montagem"

# ---------------------------------------------------------------------------
# Telas
# ---------------------------------------------------------------------------

def _render_escolha_parceiro(parceiros: list, panorama: dict) -> None:
    st.markdown("### 🤝 Parceiros")
    nomes = [p["nome"] for p in parceiros]
    st.session_state["parceiros_painel_nomes"] = nomes

    st.selectbox(
        "Buscar parceiro",
        options=nomes,
        index=None,
        placeholder="Digite ou escolha um parceiro...",
        key="parceiros_selecao_dropdown",
        on_change=_cb_escolher_parceiro,
        args=("parceiros_selecao_dropdown",),
    )

    linhas = []
    for p in parceiros:
        info = panorama.get(p["id"], {})
        comissao = p.get("percentual_comissao")
        linhas.append({
            "Parceiro": p["nome"],
            "Comissão": f"{float(comissao):g}%" if comissao is not None else "-",
            "Ciclo de comissão": _txt_ciclo(info),
            "Maleta": _txt_maleta(info),
        })

    argumentos = dict(
        use_container_width=True, hide_index=True,
        selection_mode="single-row", key="parceiros_painel",
    )
    try:
        st.dataframe(linhas, on_select=_cb_escolher_do_painel, **argumentos)
    except Exception:  # Streamlit sem callback em on_select: usa o rerun (uma execução a mais)
        selecao = st.dataframe(linhas, on_select="rerun", **argumentos)
        if selecao.selection.rows:
            _selecionar_parceiro(nomes[selecao.selection.rows[0]])
            st.rerun()
    st.caption("Clique em uma linha para abrir o parceiro.")

def _render_cabecalho_nav(parceiro: dict, nomes: list, info: dict) -> None:
    """Seletor para trocar de parceiro sem voltar à lista + situação atual em chips."""
    col_sel, col_todos, col_atualizar = st.columns([6, 1.6, 0.9], vertical_alignment="center")
    with col_sel:
        st.selectbox(
            "Parceiro",
            options=nomes,
            index=nomes.index(parceiro["nome"]),
            key="parceiros_trocar",
            on_change=_cb_escolher_parceiro,
            args=("parceiros_trocar",),
            label_visibility="collapsed",
        )
    with col_todos:
        st.button(
            "← Todos", key="parceiros_desmarcar", on_click=_desmarcar_parceiro,
            help="Voltar ao painel com todos os parceiros",
        )
    with col_atualizar:
        st.button(
            "🔄", key="parceiros_atualizar_tudo", on_click=_atualizar_dados,
            help="Recarregar os dados. Eles ficam guardados por até 1 minuto para a tela ser "
                 "rápida; use após vendas, estoque ou cadastros alterados em outras telas.",
        )

    comissao = parceiro.get("percentual_comissao")
    envio = info.get("envio") or {}
    st.markdown(
        _html_chips([
            (f"💰 Comissão {float(comissao):g}%" if comissao is not None else "💰 Comissão -", ""),
            (f"📑 Ciclo {_txt_ciclo(info)}", "ok" if info.get("ciclo") else ""),
            (f"📦 Maleta {_txt_maleta(info)}", "ok" if envio.get("status") == "Enviado" else ""),
        ]),
        unsafe_allow_html=True,
    )

def secao_parceiros():
    try:
        parceiros = _buscar_parceiros_ativos()
    except Exception as e:
        st.markdown("### 🤝 Parceiros")
        st.error(f"Não foi possível carregar os parceiros: {e}")
        return

    if not parceiros:
        st.markdown("### 🤝 Parceiros")
        st.info(
            "Nenhum parceiro cadastrado ainda. Cadastre um parceiro na aba "
            "**Cadastros Auxiliares** para começar."
        )
        return

    panorama = _panorama_parceiros()
    nomes = [p["nome"] for p in parceiros]
    nome_selecionado = st.session_state.get("parceiros_selecionado")
    parceiro = next((p for p in parceiros if p["nome"] == nome_selecionado), None)

    # Nada selecionado (ou o parceiro salvo não existe mais): painel com todos.
    if parceiro is None:
        if nome_selecionado:
            _limpar_estado_parceiro()
        _render_escolha_parceiro(parceiros, panorama)
        return

    _injetar_css()
    info = panorama.get(parceiro["id"], {})
    _render_cabecalho_nav(parceiro, nomes, info)

    # st.tabs executa o conteúdo de TODAS as abas a cada interação (e suas consultas).
    # Com este menu só o grupo escolhido é executado.
    secoes = ["fechamento", "maleta"]
    rotulos = {
        "fechamento": "💰 Fechamento" + (
            f" · ciclo #{info['ciclo']['numero_ciclo']}" if info.get("ciclo") else ""
        ),
        "maleta": "📦 Maleta" + (
            f" · #{info['envio']['numero_envio']}" if info.get("envio") else ""
        ),
    }
    secao = _menu("Seção", secoes, key="parceiros_aba", formatar=rotulos.get)

    if secao == "fechamento":
        secao_fechamento(parceiro)
    else:
        secao_maleta(parceiro)

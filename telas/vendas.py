import streamlit as st
import tempfile
import math
from datetime import date, datetime
from typing import Optional

from db import supabase
from vendas_import import ler_planilha, importar_vendas_excel
from telas.vendas_manual import tela_vendas_manual
from telas.cadastros_auxiliares import tela_cadastros_auxiliares

MESES_PT = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
    5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
    9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
}


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


def _injetar_estilo_kpi():
    """Evita que o valor dos st.metric seja cortado com reticências quando
    o card fica estreito (ex.: 4 cartões numa linha)."""
    st.markdown(
        """
        <style>
        div[data-testid="stMetricValue"] {
            overflow: visible;
            white-space: normal;
            word-break: break-word;
            font-size: 1.35rem;
            line-height: 1.2;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _kpi_card(titulo: str, qtd: int, valor: float, subtitulo: Optional[str] = None):
    with st.container(border=True):
        st.markdown(
            f"<div style='text-align:center; font-weight:700; margin-bottom:0.1rem;'>{titulo}</div>",
            unsafe_allow_html=True,
        )
        if subtitulo:
            st.markdown(
                f"<div style='text-align:center; font-size:0.75rem; color:#9aa0ab; margin-bottom:0.3rem;'>{subtitulo}</div>",
                unsafe_allow_html=True,
            )
        col_qtd, col_valor = st.columns(2)
        with col_qtd:
            st.metric("Qtd. Vendas", qtd)
        with col_valor:
            st.metric("Valor", _fmt_moeda(valor))


def _secao_importar():
    st.subheader("📥 Importar vendas de planilha Excel")
    st.caption(
        "Formato esperado: Canal | Código Interno | Quantidade | Produto | "
        "Valor Unitário | Desconto | Valor Total | Status | Data | Cliente"
    )

    arquivo = st.file_uploader("Selecione o arquivo .xlsx", type=["xlsx"], key="upload_vendas")

    if arquivo is None:
        return

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp.write(arquivo.getvalue())
        tmp_path = tmp.name

    try:
        df_preview = ler_planilha(tmp_path)
    except Exception as e:
        st.error(f"Não foi possível ler a planilha: {e}")
        return

    st.write(f"**{len(df_preview)} linhas** encontradas na planilha.")
    st.dataframe(df_preview, use_container_width=True)

    if st.button("Confirmar e importar vendas", type="primary"):
        with st.spinner("Importando vendas..."):
            resultado = importar_vendas_excel(tmp_path)

        st.success(
            f"{resultado['importadas']} vendas importadas de {resultado['total_linhas']} linhas."
        )
        if resultado["duplicadas"]:
            st.warning(f"{resultado['duplicadas']} linha(s) ignorada(s) por já existirem.")
        if resultado["erros"]:
            with st.expander(f"⚠️ {len(resultado['erros'])} linha(s) com erro"):
                for erro in resultado["erros"]:
                    st.write(f"- {erro}")


def _resetar_filtros_vendas():
    """Callback do botão 'Limpar filtros'. Precisa ser um on_click (e não um
    'if st.button(...)' com atribuição direta), porque alterar
    st.session_state de uma key que já foi usada por um widget nessa MESMA
    execução do script dispara StreamlitWidgetAlreadyInstantiatedError. Um
    callback roda ANTES do script ser reexecutado do zero, então é seguro."""
    st.session_state["vendas_filtro_mes"] = "Todos"
    st.session_state["vendas_filtro_canal"] = "Todos"
    st.session_state["pagina_atual_vendas"] = 1


def _secao_listagem():
    # ------------------------------------------------------------------
    # 1) Dataset leve com TODAS as vendas (valor_total, data_venda e o nome
    #    do canal), usado para os KPIs fixos (Total/Ano/Mês atual), para o
    #    KPI "Filtros" e para montar as opções do filtro de mês/ano.
    # ------------------------------------------------------------------
    try:
        response_kpi = (
            supabase
            .table("vendas")
            .select("valor_total, data_venda, canais_venda(nome)")
            .execute()
        )
        vendas_kpi = response_kpi.data or []
    except Exception as e:
        st.error(f"Erro ao carregar vendas: {e}")
        return

    hoje = date.today()
    ano_atual, mes_atual = hoje.year, hoje.month

    def _acumula(filtro):
        qtd, soma = 0, 0.0
        for v in vendas_kpi:
            dt = _parse_data_segura(v.get("data_venda"))
            if dt is None or not filtro(v, dt):
                continue
            qtd += 1
            soma += float(v.get("valor_total") or 0)
        return qtd, soma

    qtd_total = len(vendas_kpi)
    soma_total = sum(float(v.get("valor_total") or 0) for v in vendas_kpi)
    qtd_ano, soma_ano = _acumula(lambda v, dt: dt.year == ano_atual)
    qtd_mes_atual, soma_mes_atual = _acumula(lambda v, dt: dt.year == ano_atual and dt.month == mes_atual)

    # ------------------------------------------------------------------
    # 2) Opções de filtro (mês/ano e canal), calculadas antes dos cards
    #    para que o card "Filtros" já reflita a seleção atual.
    # ------------------------------------------------------------------
    meses_disponiveis = sorted(
        {
            (dt.year, dt.month)
            for v in vendas_kpi
            if (dt := _parse_data_segura(v.get("data_venda"))) is not None
        },
        reverse=True,
    )
    opcoes_mes = ["Todos"] + [f"{MESES_PT[m]}/{a}" for (a, m) in meses_disponiveis]

    try:
        response_canais = (
            supabase.table("canais_venda")
            .select("id, nome")
            .order("nome")
            .execute()
        )
        canais_disponiveis = response_canais.data or []
    except Exception:
        canais_disponiveis = []  # filtro de canal fica indisponível se a consulta falhar

    opcoes_canal = ["Todos"] + [c["nome"] for c in canais_disponiveis]

    # Seleção atual dos filtros (lida do session_state, com fallback seguro
    # caso a lista de opções tenha mudado desde a última execução)
    mes_selecionado_atual = st.session_state.get("vendas_filtro_mes", "Todos")
    if mes_selecionado_atual not in opcoes_mes:
        mes_selecionado_atual = "Todos"
    canal_selecionado_atual = st.session_state.get("vendas_filtro_canal", "Todos")
    if canal_selecionado_atual not in opcoes_canal:
        canal_selecionado_atual = "Todos"

    def _bate_filtro_atual(v, dt):
        if mes_selecionado_atual != "Todos":
            ano_f, mes_f = meses_disponiveis[opcoes_mes.index(mes_selecionado_atual) - 1]
            if dt.year != ano_f or dt.month != mes_f:
                return False
        if canal_selecionado_atual != "Todos":
            if ((v.get("canais_venda") or {}).get("nome")) != canal_selecionado_atual:
                return False
        return True

    qtd_filtro, soma_filtro = _acumula(_bate_filtro_atual)
    subtitulo_filtro = (
        f"{mes_selecionado_atual if mes_selecionado_atual != 'Todos' else 'Todo período'} · "
        f"{canal_selecionado_atual if canal_selecionado_atual != 'Todos' else 'Todos canais'}"
    )

    # ------------------------------------------------------------------
    # 3) Os 4 cartões de KPI
    # ------------------------------------------------------------------
    _injetar_estilo_kpi()
    col_kpi1, col_kpi2, col_kpi3, col_kpi4 = st.columns(4)
    with col_kpi1:
        _kpi_card("Total", qtd_total, soma_total)
    with col_kpi2:
        _kpi_card("Ano Atual", qtd_ano, soma_ano)
    with col_kpi3:
        _kpi_card(f"Mês Atual ({MESES_PT[mes_atual]})", qtd_mes_atual, soma_mes_atual)
    with col_kpi4:
        _kpi_card("Filtros", qtd_filtro, soma_filtro, subtitulo=subtitulo_filtro)

    st.markdown("---")

    if not vendas_kpi:
        st.info("Nenhuma venda registrada.")
        return

    # ------------------------------------------------------------------
    # 4) Widgets de filtro (Mês/Ano da venda e Canal de venda) + botão
    #    para limpar os filtros
    # ------------------------------------------------------------------
    col_filtro_mes, col_filtro_canal, col_limpar = st.columns([2, 2, 1])
    with col_filtro_mes:
        mes_selecionado = st.selectbox("Mês da venda", opcoes_mes, key="vendas_filtro_mes")
    with col_filtro_canal:
        canal_selecionado = st.selectbox("Canal", opcoes_canal, key="vendas_filtro_canal")
    with col_limpar:
        # Espaçador para alinhar o botão com a altura dos selects (que têm label acima)
        st.markdown("<div style='margin-top:1.85rem;'></div>", unsafe_allow_html=True)
        st.button(
            "🔄 Limpar filtros",
            use_container_width=True,
            key="vendas_btn_limpar_filtros",
            on_click=_resetar_filtros_vendas,
        )

    # Reseta a página para 1 sempre que algum filtro mudar
    assinatura_filtros = f"{mes_selecionado}|{canal_selecionado}"
    if st.session_state.get("vendas_assinatura_filtros") != assinatura_filtros:
        st.session_state.pagina_atual_vendas = 1
        st.session_state.vendas_assinatura_filtros = assinatura_filtros

    # ------------------------------------------------------------------
    # 5) Paginação (50 por página) + consulta da página filtrada
    # ------------------------------------------------------------------
    ITENS_POR_PAGINA = 50
    if "pagina_atual_vendas" not in st.session_state:
        st.session_state.pagina_atual_vendas = 1

    # Embed do canal como inner join só quando o filtro de canal está ativo,
    # para não excluir vendas sem canal preenchido quando não há filtro.
    canal_embed = "canais_venda!inner(nome)" if canal_selecionado != "Todos" else "canais_venda(nome)"

    def _monta_query():
        query = (
            supabase
            .table("vendas")
            .select(
                "id, quantidade, valor_unitario, valor_desconto,"
                " valor_total, data_venda, cliente,"
                f" produtos(descricao, codigo_interno), {canal_embed}, status_venda(nome)",
                count="exact",
            )
        )

        if mes_selecionado != "Todos":
            ano_f, mes_f = meses_disponiveis[opcoes_mes.index(mes_selecionado) - 1]
            inicio = date(ano_f, mes_f, 1)
            fim = date(ano_f + 1, 1, 1) if mes_f == 12 else date(ano_f, mes_f + 1, 1)
            query = query.gte("data_venda", inicio.isoformat()).lt("data_venda", fim.isoformat())

        if canal_selecionado != "Todos":
            query = query.eq("canais_venda.nome", canal_selecionado)

        return query

    try:
        total_resp = _monta_query().order("data_venda", desc=True).range(0, 0).execute()
        total_filtrado = total_resp.count or 0
    except Exception as e:
        st.error(f"Erro ao carregar vendas: {e}")
        return

    total_paginas = math.ceil(total_filtrado / ITENS_POR_PAGINA) if total_filtrado > 0 else 1
    if st.session_state.pagina_atual_vendas > total_paginas:
        st.session_state.pagina_atual_vendas = total_paginas

    offset = (st.session_state.pagina_atual_vendas - 1) * ITENS_POR_PAGINA

    if total_filtrado == 0:
        st.info("Nenhuma venda encontrada para os filtros selecionados.")
        return

    try:
        response = (
            _monta_query()
            .order("data_venda", desc=True)
            .range(offset, offset + ITENS_POR_PAGINA - 1)
            .execute()
        )
        vendas = response.data or []
    except Exception as e:
        st.error(f"Erro ao carregar a página de vendas: {e}")
        return

    with st.container(border=True):
        c_data, c_canal, c_prod, c_qtd, c_total, c_status, c_cliente = st.columns(
            [1.5, 1.5, 3, 1, 1.5, 1.5, 2]
        )
        c_data.markdown("**Data**")
        c_canal.markdown("**Canal**")
        c_prod.markdown("**Produto**")
        c_qtd.markdown("**Qtd**")
        c_total.markdown("**Valor Total**")
        c_status.markdown("**Status**")
        c_cliente.markdown("**Cliente**")

        st.divider()

        for v in vendas:
            col_data, col_canal, col_prod, col_qtd, col_total, col_status, col_cliente = (
                st.columns([1.5, 1.5, 3, 1, 1.5, 1.5, 2])
            )

            col_data.write(str(v.get("data_venda") or "-")[:10])
            col_canal.write((v.get("canais_venda") or {}).get("nome") or "-")

            produto = v.get("produtos") or {}
            col_prod.write(produto.get("descricao") or "-")

            col_qtd.write(v.get("quantidade"))
            col_total.write(_fmt_moeda(v.get("valor_total")))
            col_status.write((v.get("status_venda") or {}).get("nome") or "-")
            col_cliente.write(v.get("cliente") or "-")

    # Navegação de páginas
    if total_paginas > 1:
        col_espaco, col_paginacao = st.columns([2, 1])
        with col_paginacao:
            nova_pagina = st.number_input(
                f"Página (1 de {total_paginas})",
                min_value=1,
                max_value=total_paginas,
                value=st.session_state.pagina_atual_vendas,
                step=1,
                key="input_pagina_vendas",
            )
            if nova_pagina != st.session_state.pagina_atual_vendas:
                st.session_state.pagina_atual_vendas = nova_pagina
                st.rerun()

    st.caption(
        f"Exibindo página {st.session_state.pagina_atual_vendas} de {total_paginas} "
        f"({total_filtrado} venda(s) no total para o filtro selecionado)."
    )


def tela_vendas():
    st.header("💰 Gestão de Vendas")

    aba_listagem, aba_importar, aba_manual, aba_auxiliares = st.tabs(
        ["Vendas registradas", "Importar planilha", "Cadastrar venda", "Cadastros Auxiliares"]
    )

    with aba_listagem:
        _secao_listagem()

    with aba_importar:
        _secao_importar()

    with aba_manual:
        tela_vendas_manual()

    with aba_auxiliares:
        tela_cadastros_auxiliares()

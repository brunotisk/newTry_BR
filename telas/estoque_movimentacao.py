import streamlit as st
from datetime import date, datetime, timedelta
import calendar

from db import supabase
from componentes.paginacao import render_paginacao, reset_paginacao, get_itens_por_pagina

def _fmt_qtd(valor) -> str:
    """Mostra quantidade sem casas decimais desnecessárias (ex.: 3 em vez de 3.0)."""
    valor = float(valor or 0)
    return f"{valor:g}"


def _texto_celula(valor, largura="100%") -> str:
    """Renderiza uma célula em uma única linha, com reticências e tooltip."""
    texto = "-" if valor is None or str(valor).strip() == "" else str(valor)
    import html
    texto_html = html.escape(texto)
    return (
        f'<div title="{texto_html}" style="width:{largura}; height:28px; display:flex; '
        'align-items:center; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; '
        'line-height:28px; margin:0; padding:0;">'
        f'{texto_html}</div>'
    )


def _estilo_links_origem():
    """Deixa os botões de origem visualmente parecidos com hyperlinks."""
    st.markdown(
        """
        <style>
        /*
         * A tabela é montada com um bloco horizontal por linha.
         * O Streamlit aplica espaçamento vertical entre esses blocos, e
         * esse espaçamento acaba variando conforme o tipo de conteúdo.
         * Dentro do container com borda, zeramos esse espaçamento para que
         * todas as linhas tenham exatamente a mesma altura visual.
         */
        div[data-testid="stVerticalBlockBorderWrapper"] div[data-testid="stHorizontalBlock"] {
            margin-bottom: 0 !important;
            padding-top: 0 !important;
            padding-bottom: 0 !important;
            min-height: 28px !important;
            align-items: center !important;
        }

        div[data-testid="stVerticalBlockBorderWrapper"] div[data-testid="stHorizontalBlock"] > div[data-testid="column"] {
            padding-top: 0 !important;
            padding-bottom: 0 !important;
        }

        /* Links de origem da tabela de movimentações */
        div[data-testid="stButton"] button[kind="tertiary"] {
            color: #8ab4f8 !important;
            text-decoration: underline !important;
            text-underline-offset: 2px;
            font-weight: 500 !important;
            padding: 0 !important;
            min-height: 28px !important;
            height: 28px !important;
            line-height: 28px !important;
            margin: 0 !important;
            white-space: nowrap !important;
        }
        div[data-testid="stVerticalBlockBorderWrapper"] div[data-testid="stMarkdownContainer"] {
            margin: 0 !important;
            padding: 0 !important;
        }

        div[data-testid="stButton"] button[kind="tertiary"] p {
            color: #8ab4f8 !important;
            text-decoration: underline !important;
            text-underline-offset: 2px;
            white-space: nowrap !important;
            margin: 0 !important;
        }
        div[data-testid="stButton"] button[kind="tertiary"]:hover {
            color: #ffffff !important;
            background: transparent !important;
        }
        div[data-testid="stButton"] button[kind="tertiary"]:hover p {
            color: #ffffff !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

def _secao_movimentacoes():
    # Filtros: por padrão, mostra todo o histórico. O usuário pode optar por
    # filtrar por mês/ano ou por um período específico.
    col_data, col_tipo, col_busca = st.columns([2.2, 1.5, 2])

    with col_data:
        modo_data = st.selectbox(
            "Filtro de data",
            ["Todas as datas", "Mês/Ano", "Período"],
            index=0,
            key="movimentos_modo_data",
        )

    with col_tipo:
        tipo_filtro = st.selectbox("Tipo", ["Todos", "Entrada", "Saída", "Ajuste"], key="movimentos_tipo")

    with col_busca:
        busca_codigo = st.text_input("Código do produto", placeholder="Ex.: 11389", key="movimentos_busca_codigo")

    data_inicio = None
    data_fim = None
    mes_ano_filtro = None

    if modo_data == "Mês/Ano":
        # Busca somente as datas existentes para montar um único dropdown
        # com os meses/anos que realmente possuem movimentações.
        try:
            datas_resp = (
                supabase.table("estoque_movimentos")
                .select("data_movimento")
                .not_.is_("data_movimento", "null")
                .order("data_movimento", desc=True)
                .limit(10000)
                .execute()
            )
            meses_existentes = set()
            for row in datas_resp.data or []:
                valor = row.get("data_movimento")
                if not valor:
                    continue
                texto_data = str(valor)[:10]
                try:
                    dt = datetime.strptime(texto_data, "%Y-%m-%d")
                    meses_existentes.add((dt.year, dt.month))
                except ValueError:
                    continue

            meses_existentes = sorted(meses_existentes, reverse=True)
            nomes_meses = [
                "janeiro", "fevereiro", "março", "abril", "maio", "junho",
                "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"
            ]
            opcoes_mes_ano = [
                (ano, mes, f"{ano}/{nomes_meses[mes - 1]}")
                for ano, mes in meses_existentes
            ]

            if not opcoes_mes_ano:
                st.info("Não existem movimentações com data registrada para filtrar por mês/ano.")
            else:
                escolha_mes_ano = st.selectbox(
                    "Mês/Ano",
                    opcoes_mes_ano,
                    format_func=lambda item: item[2],
                    key="movimentos_mes_ano",
                )
                ano_filtro, mes_filtro, mes_ano_filtro = escolha_mes_ano
                data_inicio = date(int(ano_filtro), int(mes_filtro), 1)
                ultimo_dia = calendar.monthrange(int(ano_filtro), int(mes_filtro))[1]
                data_fim = date(int(ano_filtro), int(mes_filtro), ultimo_dia)
        except Exception as e:
            st.error(f"Erro ao carregar meses com movimentações: {e}")
            return

    elif modo_data == "Período":
        col_dt_ini, col_dt_fim, _ = st.columns([1, 1, 2])
        with col_dt_ini:
            data_inicio = st.date_input(
                "De",
                value=date.today() - timedelta(days=30),
                key="movimentos_data_inicio",
            )
        with col_dt_fim:
            data_fim = st.date_input(
                "Até",
                value=date.today(),
                key="movimentos_data_fim",
            )

    tipo_map = {"Entrada": "entrada", "Saída": "saida", "Ajuste": "ajuste"}

    try:
        query = (
            supabase.table("estoque_movimentos")
            .select(
                "id, produto_id, tipo, quantidade, saldo_apos, data_movimento, compra_id, venda_id, motivo,"
                " produtos(id, codigo_interno, descricao)"
            )
        )

        # IMPORTANTE: o filtro por código precisa ser aplicado NO BANCO, antes
        # do limite/paginação do histórico. Se filtrarmos depois de buscar os
        # primeiros 500/5000 movimentos gerais, uma entrada antiga do produto
        # pode ficar escondida por outros movimentos mais recentes.
        produto_ids = None
        if busca_codigo.strip():
            codigo_busca = busca_codigo.strip()
            resp_produto = (
                supabase.table("produtos")
                .select("id, codigo_interno")
                .eq("codigo_interno", codigo_busca)
                .execute()
            )
            produto_ids = [p["id"] for p in (resp_produto.data or [])]
            if produto_ids:
                query = query.in_("produto_id", produto_ids)

        # Sem filtro de data: traz todo o histórico.
        # Quando houver filtro, usamos limite superior exclusivo para não perder
        # registros que tenham horário no último dia do período.
        if data_inicio is not None and data_fim is not None:
            if data_fim < data_inicio:
                st.error("A data inicial não pode ser maior que a data final.")
                return
            query = query.gte("data_movimento", data_inicio.isoformat())
            query = query.lt("data_movimento", (data_fim + timedelta(days=1)).isoformat())

        if tipo_filtro != "Todos":
            query = query.eq("tipo", tipo_map[tipo_filtro])

        if busca_codigo.strip() and not produto_ids:
            movimentos = []
        else:
            response = (
                query
                .order("data_movimento", desc=True)
                .order("id", desc=True)
                .limit(5000)
                .execute()
            )
            movimentos = response.data or []
    except Exception as e:
        st.error(f"Erro ao carregar movimentações: {e}")
        return

    filtro_atual = (
        modo_data,
        data_inicio.isoformat() if data_inicio else None,
        data_fim.isoformat() if data_fim else None,
        tipo_filtro,
        busca_codigo.strip().lower(),
    )
    if st.session_state.get("movimentos_filtro_atual") != filtro_atual:
        st.session_state["movimentos_filtro_atual"] = filtro_atual
        reset_paginacao("estoque_movimentacoes")

    total_itens = len(movimentos)
    itens_por_pagina = get_itens_por_pagina("estoque_movimentacoes", 50)
    total_paginas = max(1, (total_itens + itens_por_pagina - 1) // itens_por_pagina)
    pagina_atual = min(int(st.session_state.get("pagina_atual_estoque_movimentacoes", 1)), total_paginas)
    st.session_state["pagina_atual_estoque_movimentacoes"] = max(1, pagina_atual)

    st.caption(
        f"Exibindo página {st.session_state['pagina_atual_estoque_movimentacoes']} de {total_paginas} "
        f"({total_itens} registros no total)."
    )

    inicio = (st.session_state["pagina_atual_estoque_movimentacoes"] - 1) * itens_por_pagina
    movimentos_pagina = movimentos[inicio:inicio + itens_por_pagina]

    if not movimentos_pagina:
        st.info("Nenhuma movimentação encontrada para o filtro selecionado.")
        return

    icone_tipo = {"entrada": "🟢 Entrada", "saida": "🔴 Saída", "ajuste": "🟡 Ajuste"}

    def _navegar_para_transacao(tipo: str, registro_id: int, produto_id: int | None = None):
        """Prepara a navegação para Compras/Vendas e abre o detalhe após o rerun."""
        if tipo == "compra":
            st.session_state["pagina_atual"] = "🛍️ Compras"
            st.session_state["compras_abrir_id"] = int(registro_id)
            if produto_id is not None:
                st.session_state["compras_abrir_produto_id"] = int(produto_id)
        elif tipo == "venda":
            st.session_state["pagina_atual"] = "💰 Vendas"
            st.session_state["vendas_abrir_id"] = int(registro_id)
        st.rerun()

    _estilo_links_origem()

    with st.container(border=True):
        c_dt, c_tipo, c_cod, c_prod, c_qtd, c_saldo, c_origem, c_obs = st.columns(
            [1.2, 1.2, 1.4, 2.8, 1.0, 1.1, 1.7, 1.8]
        )
        c_dt.markdown("**Data**")
        c_tipo.markdown("**Tipo**")
        c_cod.markdown("**Código**")
        c_prod.markdown("**Produto**")
        c_qtd.markdown("**Qtd**")
        c_saldo.markdown("**Saldo após**")
        c_origem.markdown("**Origem**")
        c_obs.markdown("**Observação**")

        st.divider()

        for m in movimentos_pagina:
            col_dt, col_tipo, col_cod, col_prod, col_qtd, col_saldo, col_origem, col_obs = st.columns(
                [1.2, 1.2, 1.4, 2.8, 1.0, 1.1, 1.7, 1.8]
            )

            if m.get("data_movimento"):
                dt = datetime.fromisoformat(str(m["data_movimento"])[:10])
                col_dt.markdown(_texto_celula(dt.strftime("%d/%m/%Y")), unsafe_allow_html=True)
            else:
                col_dt.markdown(_texto_celula("-"), unsafe_allow_html=True)

            col_tipo.markdown(_texto_celula(icone_tipo.get(m.get("tipo"), m.get("tipo") or "-")), unsafe_allow_html=True)

            produto = m.get("produtos") or {}
            col_cod.markdown(_texto_celula(produto.get("codigo_interno") or "-"), unsafe_allow_html=True)
            col_prod.markdown(_texto_celula(produto.get("descricao") or "-"), unsafe_allow_html=True)
            col_qtd.markdown(_texto_celula(_fmt_qtd(m.get("quantidade"))), unsafe_allow_html=True)
            col_saldo.markdown(_texto_celula(_fmt_qtd(m.get("saldo_apos"))), unsafe_allow_html=True)

            # Origem é sempre a transação de origem. Observação fica com o
            # texto livre/motivo quando existir.
            if m.get("tipo") == "saida":
                venda_id = m.get("venda_id")
                if venda_id:
                    if col_origem.button(
                        f"Venda #{venda_id}",
                        key=f"mov_venda_{m['id']}",
                        type="tertiary",
                    ):
                        _navegar_para_transacao("venda", venda_id)
                else:
                    col_origem.write("-")
                col_obs.markdown(_texto_celula(m.get("motivo") or "-"), unsafe_allow_html=True)
            elif m.get("tipo") == "entrada":
                compra_id = m.get("compra_id")
                if compra_id:
                    if col_origem.button(
                        f"Compra #{compra_id}",
                        key=f"mov_compra_{m['id']}",
                        type="tertiary",
                    ):
                        _navegar_para_transacao("compra", compra_id, produto.get("id"))
                else:
                    col_origem.write("-")
                # Compras não precisam de observação nesta visão.
                col_obs.markdown(_texto_celula("-"), unsafe_allow_html=True)
            elif m.get("tipo") == "ajuste":
                # Ainda não há navegação para a tela de ajustes. O próprio
                # movimento identifica o ajuste pelo seu ID.
                col_origem.markdown(_texto_celula(f"Ajuste #{m['id']}"), unsafe_allow_html=True)
                col_obs.markdown(_texto_celula(m.get("motivo") or "-"), unsafe_allow_html=True)
            else:
                col_origem.markdown(_texto_celula("-"), unsafe_allow_html=True)
                col_obs.markdown(_texto_celula("-"), unsafe_allow_html=True)

    render_paginacao(
        "estoque_movimentacoes",
        total_itens,
        itens_por_pagina=itens_por_pagina,
        mostrar_contagem_superior=False,
        mostrar_contagem_inferior=True,
        permitir_seletor=True,
    )


def render_estoque_movimentacao():
    _secao_movimentacoes()

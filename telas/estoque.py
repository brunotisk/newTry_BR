import streamlit as st
from datetime import date, datetime, timedelta
import calendar

from db import supabase
from estoque_ajuste import get_client, registrar_ajuste
from componentes.paginacao import render_paginacao, reset_paginacao, get_itens_por_pagina


def _fmt_moeda(valor) -> str:
    return (
        f"R$ {float(valor or 0):,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


def _fmt_qtd(valor) -> str:
    """Mostra quantidade sem casas decimais desnecessárias (ex.: 3 em vez de 3.0)."""
    valor = float(valor or 0)
    return f"{valor:g}"


# ---------------------------------------------------------------------------
# Aba 1: Estoque atual
# ---------------------------------------------------------------------------
def _secao_estoque_atual():
    try:
        # Categorias, para exibir o nome em vez do id (mesmo padrão de telas/produtos.py)
        response_cat = (
            supabase.table("categorias_produtos")
            .select("id, categoria")
            .execute()
        )
        cat_dict = {c["id"]: c["categoria"] for c in (response_cat.data or [])}

        # Estoque + produto embutido (join via FK estoque.produto_id -> produtos.id)
        response = (
            supabase.table("estoque")
            .select("quantidade_atual, produtos(id, codigo_interno, descricao, categoria_id)")
            .execute()
        )
        linhas_brutas = response.data or []
    except Exception as e:
        st.error(f"Erro ao carregar estoque: {e}")
        return

    # Achata a estrutura (produto embutido) e ignora linhas órfãs
    itens = []
    for linha in linhas_brutas:
        prod = linha.get("produtos")
        if not prod:
            continue
        itens.append({
            "id": prod["id"],
            "codigo_interno": prod.get("codigo_interno") or "-",
            "descricao": prod.get("descricao") or "-",
            "categoria": cat_dict.get(prod.get("categoria_id"), "Sem Categoria"),
            "saldo": float(linha.get("quantidade_atual") or 0),
        })

    # Custo unitário estimado = valor_unitario da compra mais recente daquele produto
    produto_ids = [item["id"] for item in itens]
    custo_unitario = {}
    if produto_ids:
        try:
            custos_resp = (
                supabase.table("compras_itens")
                .select("produto_id, valor_unitario")
                .in_("produto_id", produto_ids)
                .order("id", desc=True)
                .execute()
            )
            for row in custos_resp.data or []:
                pid = row["produto_id"]
                if pid not in custo_unitario:
                    custo_unitario[pid] = float(row["valor_unitario"] or 0)
        except Exception:
            pass  # KPI de valor fica sem essa parcela se a consulta falhar

    # Classificação de status: sem estoque mínimo configurável, só zerado/negativo vs. OK
    for item in itens:
        if item["saldo"] <= 0:
            item["status"] = "🔴 Zerado" if item["saldo"] == 0 else "🔴 Negativo"
        else:
            item["status"] = "🟢 OK"

    # KPIs
    total_produtos = len(itens)
    qtd_zerado_ou_negativo = sum(1 for i in itens if i["status"] != "🟢 OK")
    valor_total_estoque = sum(i["saldo"] * custo_unitario.get(i["id"], 0) for i in itens)

    col_kpi1, col_kpi2, col_kpi3 = st.columns(3)
    with col_kpi1:
        with st.container(border=True):
            st.caption("Produtos com Estoque")
            st.title(f"{total_produtos}")
    with col_kpi2:
        with st.container(border=True):
            st.caption("Zerados ou Negativos")
            st.title(f"{qtd_zerado_ou_negativo}")
    with col_kpi3:
        with st.container(border=True):
            st.caption("Valor em Estoque (estimado)")
            st.title(_fmt_moeda(valor_total_estoque))

    st.caption(
        "Valor estimado com base no valor unitário da última compra registrada de cada produto."
    )
    st.markdown("---")

    # Filtros
    col_busca, col_toggle = st.columns([3, 1])
    with col_busca:
        busca = st.text_input(
            "Filtrar por código ou descrição",
            placeholder="Digite para buscar...",
            label_visibility="collapsed",
        )
    with col_toggle:
        mostrar_negativo = st.toggle("Mostrar zerado/negativo", value=False)

    if busca:
        termo = busca.strip().lower()
        itens = [
            i for i in itens
            if termo in i["codigo_interno"].lower() or termo in i["descricao"].lower()
        ]

    if mostrar_negativo:
        itens = [i for i in itens if i["status"] != "🟢 OK"]
    else:
        itens = [i for i in itens if i["status"] == "🟢 OK"]

    # Ordena: problemas primeiro (negativo, depois zerado, depois ok), por saldo crescente
    ordem_status = {"🔴 Negativo": 0, "🔴 Zerado": 1, "🟢 OK": 2}
    itens.sort(key=lambda i: (ordem_status[i["status"]], i["saldo"]))

    # Detecta mudança nos filtros e volta para a primeira página.
    filtro_atual = (busca.strip().lower(), bool(mostrar_negativo))
    if st.session_state.get("estoque_filtro_atual") != filtro_atual:
        st.session_state["estoque_filtro_atual"] = filtro_atual
        reset_paginacao("estoque_atual")

    total_itens = len(itens)
    itens_por_pagina = get_itens_por_pagina("estoque_atual", 50)
    total_paginas = max(1, (total_itens + itens_por_pagina - 1) // itens_por_pagina)
    pagina_atual = min(int(st.session_state.get("pagina_atual_estoque_atual", 1)), total_paginas)
    st.session_state["pagina_atual_estoque_atual"] = max(1, pagina_atual)

    st.caption(
        f"Exibindo página {st.session_state['pagina_atual_estoque_atual']} de {total_paginas} "
        f"({total_itens} registros no total)."
    )

    inicio = (st.session_state["pagina_atual_estoque_atual"] - 1) * itens_por_pagina
    itens_pagina = itens[inicio:inicio + itens_por_pagina]

    if not itens_pagina:
        st.info("Nenhum produto encontrado para o filtro selecionado.")
        return

    with st.container(border=True):
        c_cod, c_desc, c_cat, c_saldo, c_status = st.columns([1.5, 3.5, 2, 1.5, 1.5])
        c_cod.markdown("**Código**")
        c_desc.markdown("**Descrição**")
        c_cat.markdown("**Categoria**")
        c_saldo.markdown("**Saldo**")
        c_status.markdown("**Status**")

        st.divider()

        for item in itens_pagina:
            col_cod, col_desc, col_cat, col_saldo, col_status = st.columns([1.5, 3.5, 2, 1.5, 1.5])
            col_cod.write(item["codigo_interno"])
            col_desc.write(item["descricao"])
            col_cat.write(item["categoria"])
            col_saldo.write(_fmt_qtd(item["saldo"]))
            col_status.write(item["status"])

    render_paginacao(
        "estoque_atual",
        total_itens,
        itens_por_pagina=itens_por_pagina,
        mostrar_contagem_superior=False,
        mostrar_contagem_inferior=True,
        permitir_seletor=True,
    )


# ---------------------------------------------------------------------------
# Aba 2: Movimentações (ledger)
# ---------------------------------------------------------------------------
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
                "id, tipo, quantidade, saldo_apos, data_movimento, compra_id, venda_id, motivo,"
                " produtos(codigo_interno, descricao)"
            )
        )

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

        response = (
            query
            .order("data_movimento", desc=True)
            .order("id", desc=True)
            .limit(500)
            .execute()
        )
        movimentos = response.data or []
    except Exception as e:
        st.error(f"Erro ao carregar movimentações: {e}")
        return

    if busca_codigo:
        termo = busca_codigo.strip().lower()
        movimentos = [
            m for m in movimentos
            if termo in ((m.get("produtos") or {}).get("codigo_interno") or "").lower()
        ]

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

    with st.container(border=True):
        c_dt, c_tipo, c_cod, c_prod, c_qtd, c_saldo, c_origem = st.columns([1.3, 1.3, 1.5, 3, 1.2, 1.2, 2.5])
        c_dt.markdown("**Data**")
        c_tipo.markdown("**Tipo**")
        c_cod.markdown("**Código**")
        c_prod.markdown("**Produto**")
        c_qtd.markdown("**Qtd**")
        c_saldo.markdown("**Saldo após**")
        c_origem.markdown("**Origem**")

        st.divider()

        for m in movimentos_pagina:
            col_dt, col_tipo, col_cod, col_prod, col_qtd, col_saldo, col_origem = st.columns([1.3, 1.3, 1.5, 3, 1.2, 1.2, 2.5])

            if m.get("data_movimento"):
                dt = datetime.fromisoformat(str(m["data_movimento"])[:10])
                col_dt.write(dt.strftime("%d/%m/%Y"))
            else:
                col_dt.write("-")

            col_tipo.write(icone_tipo.get(m.get("tipo"), m.get("tipo") or "-"))

            produto = m.get("produtos") or {}
            col_cod.write(produto.get("codigo_interno") or "-")
            col_prod.write(produto.get("descricao") or "-")
            col_qtd.write(_fmt_qtd(m.get("quantidade")))
            col_saldo.write(_fmt_qtd(m.get("saldo_apos")))

            if m.get("tipo") == "entrada" and m.get("compra_id"):
                col_origem.write(f"Compra #{m['compra_id']}")
            elif m.get("tipo") == "saida" and m.get("venda_id"):
                col_origem.write(f"Venda #{m['venda_id']}")
            elif m.get("tipo") == "ajuste":
                col_origem.write(m.get("motivo") or "-")
            else:
                col_origem.write("-")

    render_paginacao(
        "estoque_movimentacoes",
        total_itens,
        itens_por_pagina=itens_por_pagina,
        mostrar_contagem_superior=False,
        mostrar_contagem_inferior=True,
        permitir_seletor=True,
    )


# ---------------------------------------------------------------------------
# Aba 3: Ajuste manual
# ---------------------------------------------------------------------------
def tela_estoque_ajuste():
    st.subheader("🛠️ Ajuste manual de estoque")
    st.caption(
        "Use para perdas, quebras, extravios ou correções após contagem física — "
        "situações que não vêm de uma compra ou venda registrada."
    )

    codigo = st.text_input(
        "Código interno do produto",
        placeholder="Ex.: 11389",
        key="ajuste_estoque_codigo",
    )

    produto = None
    saldo_atual = None
    if codigo:
        try:
            resp_prod = (
                supabase.table("produtos")
                .select("id, codigo_interno, descricao")
                .eq("codigo_interno", codigo.strip())
                .execute()
            )
            produto = resp_prod.data[0] if resp_prod.data else None

            if produto:
                resp_estoque = (
                    supabase.table("estoque")
                    .select("quantidade_atual")
                    .eq("produto_id", produto["id"])
                    .execute()
                )
                saldo_atual = (
                    float(resp_estoque.data[0]["quantidade_atual"])
                    if resp_estoque.data else 0.0
                )
        except Exception as e:
            st.error(f"Erro ao buscar produto: {e}")
            produto = None

        if produto:
            st.success(
                f"Produto encontrado: **{produto['descricao']}** — "
                f"saldo atual: **{_fmt_qtd(saldo_atual)}**"
            )
        else:
            st.warning("Produto não encontrado para esse código interno.")

    with st.form("form_ajuste_estoque"):
        direcao = st.selectbox(
            "Tipo de ajuste",
            ["Retirar do estoque (perda, quebra, extravio)", "Adicionar ao estoque (correção positiva)"],
        )
        quantidade = st.number_input("Quantidade", min_value=0.01, step=1.0, format="%.2f")
        motivo = st.text_area("Motivo", placeholder="Ex.: Quebra durante o transporte")
        data_movimento = st.date_input("Data do ajuste", value=date.today())

        salvar = st.form_submit_button("💾 Registrar ajuste", type="primary")

    if salvar:
        if not codigo or not produto:
            st.error("Informe um código interno de produto válido antes de salvar.")
            return
        if not motivo.strip():
            st.error("Informe o motivo do ajuste.")
            return

        quantidade_sinalizada = (
            -quantidade if direcao.startswith("Retirar") else quantidade
        )

        try:
            sb = get_client()
            registrar_ajuste(
                sb,
                produto_id=produto["id"],
                quantidade=quantidade_sinalizada,
                motivo=motivo.strip(),
                data_movimento=data_movimento.isoformat(),
            )
            st.success("Ajuste registrado com sucesso e estoque atualizado!")
            st.rerun()
        except Exception as e:
            st.error(f"Erro ao registrar ajuste: {e}")


# ---------------------------------------------------------------------------
def tela_estoque():
    st.header("📊 Controle de Estoque")

    aba_atual, aba_mov, aba_ajuste = st.tabs(
        ["Estoque atual", "Movimentações", "Ajuste manual"]
    )

    with aba_atual:
        _secao_estoque_atual()

    with aba_mov:
        _secao_movimentacoes()

    with aba_ajuste:
        tela_estoque_ajuste()

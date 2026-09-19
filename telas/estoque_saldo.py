import streamlit as st
from db import supabase
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


def render_estoque_saldo():
    _secao_estoque_atual()


def tela_estoque_saldo():
    """Página principal de Estoque. Mantém as três visões como abas internas."""
    st.header("📊 Controle de Estoque")

    aba_atual, aba_mov, aba_ajuste = st.tabs(
        ["Estoque atual", "Movimentações", "Ajuste manual"]
    )

    with aba_atual:
        render_estoque_saldo()

    with aba_mov:
        from telas.estoque_movimentacao import render_estoque_movimentacao
        render_estoque_movimentacao()

    with aba_ajuste:
        from telas.estoque_ajuste import tela_estoque_ajuste
        tela_estoque_ajuste()

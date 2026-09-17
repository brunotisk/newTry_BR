import streamlit as st
from datetime import date, datetime, timedelta

from db import supabase
from estoque_ajuste import get_client, registrar_ajuste


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
        apenas_alerta = st.toggle("Apenas zerado/negativo", value=False)

    if busca:
        termo = busca.strip().lower()
        itens = [
            i for i in itens
            if termo in i["codigo_interno"].lower() or termo in i["descricao"].lower()
        ]
    if apenas_alerta:
        itens = [i for i in itens if i["status"] != "🟢 OK"]

    # Ordena: problemas primeiro (negativo, depois zerado, depois ok), por saldo crescente
    ordem_status = {"🔴 Negativo": 0, "🔴 Zerado": 1, "🟢 OK": 2}
    itens.sort(key=lambda i: (ordem_status[i["status"]], i["saldo"]))

    if not itens:
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

        for item in itens:
            col_cod, col_desc, col_cat, col_saldo, col_status = st.columns(
                [1.5, 3.5, 2, 1.5, 1.5]
            )
            col_cod.write(item["codigo_interno"])
            col_desc.write(item["descricao"])
            col_cat.write(item["categoria"])
            col_saldo.write(_fmt_qtd(item["saldo"]))
            col_status.write(item["status"])


# ---------------------------------------------------------------------------
# Aba 2: Movimentações (ledger)
# ---------------------------------------------------------------------------
def _secao_movimentacoes():
    col_dt_ini, col_dt_fim, col_tipo, col_busca = st.columns([1.5, 1.5, 1.5, 2])

    with col_dt_ini:
        data_inicio = st.date_input("De", value=date.today() - timedelta(days=30))
    with col_dt_fim:
        data_fim = st.date_input("Até", value=date.today())
    with col_tipo:
        tipo_filtro = st.selectbox("Tipo", ["Todos", "Entrada", "Saída", "Ajuste"])
    with col_busca:
        busca_codigo = st.text_input("Código do produto", placeholder="Ex.: 11389")

    tipo_map = {"Entrada": "entrada", "Saída": "saida", "Ajuste": "ajuste"}

    try:
        query = (
            supabase.table("estoque_movimentos")
            .select(
                "id, tipo, quantidade, saldo_apos, data_movimento, compra_id, venda_id, motivo,"
                " produtos(codigo_interno, descricao)"
            )
            .gte("data_movimento", data_inicio.isoformat())
            .lte("data_movimento", data_fim.isoformat())
        )
        if tipo_filtro != "Todos":
            query = query.eq("tipo", tipo_map[tipo_filtro])

        response = query.order("data_movimento", desc=True).order("id", desc=True).limit(500).execute()
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

    st.caption(f"{len(movimentos)} movimento(s) no período (máximo de 500 exibidos).")
    st.markdown("---")

    if not movimentos:
        st.info("Nenhuma movimentação encontrada para o filtro selecionado.")
        return

    icone_tipo = {"entrada": "🟢 Entrada", "saida": "🔴 Saída", "ajuste": "🟡 Ajuste"}

    with st.container(border=True):
        c_dt, c_tipo, c_prod, c_qtd, c_saldo, c_origem = st.columns(
            [1.3, 1.3, 3, 1.2, 1.2, 2.5]
        )
        c_dt.markdown("**Data**")
        c_tipo.markdown("**Tipo**")
        c_prod.markdown("**Produto**")
        c_qtd.markdown("**Qtd**")
        c_saldo.markdown("**Saldo após**")
        c_origem.markdown("**Origem**")

        st.divider()

        for m in movimentos:
            col_dt, col_tipo, col_prod, col_qtd, col_saldo, col_origem = st.columns(
                [1.3, 1.3, 3, 1.2, 1.2, 2.5]
            )

            if m.get("data_movimento"):
                dt = datetime.fromisoformat(str(m["data_movimento"])[:10])
                col_dt.write(dt.strftime("%d/%m/%Y"))
            else:
                col_dt.write("-")

            col_tipo.write(icone_tipo.get(m.get("tipo"), m.get("tipo") or "-"))

            produto = m.get("produtos") or {}
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

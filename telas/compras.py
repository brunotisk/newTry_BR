import streamlit as st
from datetime import datetime
from db import supabase
from telas.importar_nf import tela_importar_nf
from componentes.paginacao import render_paginacao, get_itens_por_pagina, reset_paginacao

# Compatibilidade: st.dialog é o nome estável (Streamlit >= 1.31); versões
# um pouco mais antigas ainda expõem a mesma coisa como st.experimental_dialog.
_dialog = getattr(st, "dialog", None) or st.experimental_dialog


def _fmt_moeda(valor) -> str:
    return (
        f"R$ {float(valor or 0):,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


def _fmt_qtd(valor) -> str:
    """Mostra quantidade sem casas decimais desnecessárias (ex.: 1 em vez de 1.000)."""
    return f"{float(valor or 0):g}"


@_dialog("📦 Itens da compra", width="large")
def _dialog_itens_compra(compra: dict, produto_id: int | None = None):
    # Alarga ainda mais o popup (o width="large" do Streamlit já ajuda,
    # mas aqui forçamos um valor maior e fixo em pixels/vw).
    st.markdown(
        """
        <style>
        div[data-testid="stDialog"] > div {
            max-width: 1100px !important;
            width: 92vw !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.caption(
        f"NF nº {compra.get('numero_nf') or '-'} — "
        f"Chave: {compra.get('chave_acesso') or '-'}"
    )

    try:
        response = (
            supabase.table("compras_itens")
            .select(
                "numero_item, produto_id, quantidade, valor_unitario, valor_desconto, valor_total,"
                " produtos(id, codigo_interno, descricao)"
            )
            .eq("compra_id", compra["id"])
            .order("numero_item")
            .execute()
        )
        itens = response.data or []
    except Exception as e:
        st.error(f"Erro ao carregar itens da compra: {e}")
        return

    if not itens:
        st.info("Nenhum item encontrado para essa compra.")
        return

    if produto_id is not None:
        itens_relacionados = [
            item for item in itens
            if (item.get("produtos") or {}).get("id") == produto_id
        ]
        # O embed acima não traz o id do produto em todas as versões do schema;
        # se não for possível identificar, mantemos o detalhe completo da compra.
        if itens_relacionados:
            itens = itens_relacionados
            st.caption("Item relacionado à movimentação de estoque")

    with st.container(border=True):
        c_cod, c_desc, c_qtd, c_unit, c_desc_v, c_tot = st.columns(
            [1, 4, 0.8, 1.2, 1.2, 1.2]
        )
        c_cod.markdown("**Código**")
        c_desc.markdown("**Produto**")
        c_qtd.markdown("**Qtd**")
        c_unit.markdown("**Vlr. Unit.**")
        c_desc_v.markdown("**Desconto**")
        c_tot.markdown("**Vlr. Total**")

        st.divider()

        for item in itens:
            produto = item.get("produtos") or {}
            col_cod, col_desc, col_qtd, col_unit, col_desc_v, col_tot = st.columns(
                [1, 4, 0.8, 1.2, 1.2, 1.2]
            )
            col_cod.write(produto.get("codigo_interno") or "-")
            col_desc.write(produto.get("descricao") or "-")
            col_qtd.write(_fmt_qtd(item.get("quantidade")))
            col_unit.write(_fmt_moeda(item.get("valor_unitario")))
            col_desc_v.write(_fmt_moeda(item.get("valor_desconto")))
            col_tot.write(_fmt_moeda(item.get("valor_total")))


def _secao_listagem():
    # Abre automaticamente o detalhe solicitado pela tela de movimentações.
    compra_abrir_id = st.session_state.pop("compras_abrir_id", None)
    if compra_abrir_id:
        try:
            compra_resp = (
                supabase.table("compras")
                .select("id, numero_nf, data_emissao, valor_produtos, valor_desconto, valor_total, chave_acesso")
                .eq("id", int(compra_abrir_id))
                .single()
                .execute()
            )
            compra = compra_resp.data
            if compra:
                produto_id = st.session_state.pop("compras_abrir_produto_id", None)
                _dialog_itens_compra(compra, produto_id=produto_id)
        except Exception as e:
            st.error(f"Erro ao abrir a compra #{compra_abrir_id}: {e}")

    try:
        # 1. Consulta dos dados na tabela 'compras' (agora incluindo o id,
        #    necessário para buscar os itens e a contagem por compra)
        response = (
            supabase.table("compras")
            .select(
                "id, numero_nf, data_emissao, valor_produtos, valor_desconto,"
                " valor_total, chave_acesso"
            )
            .order("data_emissao", desc=True)
            .execute()
        )
        compras = response.data or []

        # 2. Contagem de itens por compra numa única consulta a compras_itens
        qtd_itens_por_compra: dict[int, int] = {}
        compra_ids = [c["id"] for c in compras]
        if compra_ids:
            try:
                itens_resp = (
                    supabase.table("compras_itens")
                    .select("compra_id")
                    .in_("compra_id", compra_ids)
                    .execute()
                )
                for row in itens_resp.data or []:
                    cid = row["compra_id"]
                    qtd_itens_por_compra[cid] = qtd_itens_por_compra.get(cid, 0) + 1
            except Exception:
                pass  # coluna de itens fica "-" se essa consulta falhar

        # 3. Cálculo dos Cards (KPIs)
        total_compras = len(compras)
        soma_valor_total = sum(
            float(item.get("valor_total") or 0) for item in compras
        )

        data_ultima_compra = "-"
        if compras and compras[0].get("data_emissao"):
            dt_ultima = datetime.fromisoformat(
                compras[0]["data_emissao"].replace("Z", "+00:00")
            )
            data_ultima_compra = dt_ultima.strftime("%d/%m/%y")

        valor_comprado_fmt = (
            f"R$ {soma_valor_total:,.2f}"
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )

        # Ajuste CSS para igualar a altura exata dos cards
        st.markdown(
            """
            <style>
            div[data-testid="stColumn"] > div[data-testid="stVerticalBlock"] > div[data-testid="stElementContainer"] > div[data-testid="stContainer"] {
                min-height: 125px;
                display: flex;
                flex-direction: column;
                justify-content: center;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )

        # 4. Exibição dos Cards no Topo (KPIs)
        col_kpi1, col_kpi2, col_kpi3 = st.columns(3)

        with col_kpi1:
            with st.container(border=True):
                st.caption("Total de Compras")
                st.title(f"{total_compras}")

        with col_kpi2:
            with st.container(border=True):
                st.caption("Valor Comprado")
                st.title(valor_comprado_fmt)

        with col_kpi3:
            with st.container(border=True):
                st.caption("Última Compra")
                st.title(data_ultima_compra)

        st.markdown("---")

        if not compras:
            st.info("Nenhuma compra registrada.")
            return

        # 5. Paginação da listagem
        filtro_atual = len(compras)
        if st.session_state.get("compras_total_anterior") != filtro_atual:
            st.session_state["compras_total_anterior"] = filtro_atual
            reset_paginacao("compras")

        itens_por_pagina = get_itens_por_pagina("compras", 50)
        total_compras_registros = len(compras)
        total_paginas = max(1, (total_compras_registros + itens_por_pagina - 1) // itens_por_pagina)
        pagina_atual = min(int(st.session_state.get("pagina_atual_compras", 1)), total_paginas)
        st.session_state["pagina_atual_compras"] = max(1, pagina_atual)
        inicio = (st.session_state["pagina_atual_compras"] - 1) * itens_por_pagina
        compras_pagina = compras[inicio:inicio + itens_por_pagina]

        # Paginação informativa no topo da tabela.
        render_paginacao(
            "compras",
            total_compras_registros,
            itens_por_pagina=itens_por_pagina,
            mostrar_contagem_superior=True,
            mostrar_contagem_inferior=False,
            permitir_seletor=True,
        )

        # 6. Tabela de Compras
        with st.container(border=True):
            c_nf, c_dt, c_prod, c_desc, c_tot, c_itens, c_chave, c_acao = st.columns(
                [1.3, 1.7, 1.7, 1.7, 1.7, 0.9, 3.2, 0.9]
            )

            c_nf.markdown("**Número NF**")
            c_dt.markdown("**Data Compra**")
            c_prod.markdown("**Valor Produto**")
            c_desc.markdown("**Desconto (-)**")
            c_tot.markdown("**Valor Total**")
            c_itens.markdown("**Itens**")
            c_chave.markdown("**Chave Acesso**")
            c_acao.markdown("**Ações**")

            st.divider()

            for item in compras_pagina:
                col_nf, col_dt, col_prod, col_desc, col_tot, col_itens, col_chave, col_acao = (
                    st.columns([1.3, 1.7, 1.7, 1.7, 1.7, 0.9, 3.2, 0.9])
                )

                col_nf.write(item.get("numero_nf") or "-")

                if item.get("data_emissao"):
                    dt_item = datetime.fromisoformat(
                        item["data_emissao"].replace("Z", "+00:00")
                    )
                    col_dt.write(dt_item.strftime("%d/%m/%Y"))
                else:
                    col_dt.write("-")

                v_prod = float(item.get("valor_produtos") or 0)
                v_desc = float(item.get("valor_desconto") or 0)
                v_tot = float(item.get("valor_total") or 0)

                col_prod.write(
                    f"{v_prod:,.2f}"
                    .replace(",", "X")
                    .replace(".", ",")
                    .replace("X", ".")
                )
                col_desc.write(
                    f"{v_desc:,.2f}"
                    .replace(",", "X")
                    .replace(".", ",")
                    .replace("X", ".")
                )
                col_tot.write(
                    f"{v_tot:,.2f}"
                    .replace(",", "X")
                    .replace(".", ",")
                    .replace("X", ".")
                )

                col_itens.write(str(qtd_itens_por_compra.get(item["id"], 0)))
                col_chave.write(item.get("chave_acesso") or "-")

                if col_acao.button(
                    "🔎",
                    key=f"ver_itens_compra_{item['id']}",
                    help="Ver itens da compra",
                    use_container_width=True,
                ):
                    _dialog_itens_compra(item)

        render_paginacao(
            "compras",
            total_compras_registros,
            itens_por_pagina=itens_por_pagina,
            mostrar_contagem_superior=False,
            mostrar_contagem_inferior=True,
            permitir_seletor=True,
        )

    except Exception as e:
        st.error(f"Erro ao carregar dados de compras: {e}")


def tela_compras():
    st.header("🛍️ Compras")

    aba_listagem, aba_importar = st.tabs(["Compras registradas", "Importar NF-e (XML)"])

    with aba_listagem:
        _secao_listagem()

    with aba_importar:
        tela_importar_nf()

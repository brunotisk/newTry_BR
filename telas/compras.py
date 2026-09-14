import streamlit as st
from datetime import datetime
from db import supabase


def tela_compras():
    st.header("Compras")

    try:
        # 1. Consulta dos dados na tabela 'compras'
        response = (
            supabase.table("compras")
            .select(
                "numero_nf, data_emissao, valor_produtos, valor_desconto,"
                " valor_total, chave_acesso"
            )
            .order("data_emissao", desc=True)
            .execute()
        )
        compras = response.data or []

        # 2. Cálculo dos Cards (KPIs)
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

        # 3. Exibição dos Cards no Topo (KPIs)
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

        # 4. Tabela de Compras
        with st.container(border=True):
            c_nf, c_dt, c_prod, c_desc, c_tot, c_chave = st.columns(
                [1.5, 2, 2, 2, 2, 4]
            )

            c_nf.markdown("**numero_nf**")
            c_dt.markdown("**Data Compra**")
            c_prod.markdown("**valor_produto_HD**")
            c_desc.markdown("**valor_desconto_HD**")
            c_tot.markdown("**valor_total_HD**")
            c_chave.markdown("**chave_acesso**")

            st.divider()

            for item in compras:
                col_nf, col_dt, col_prod, col_desc, col_tot, col_chave = (
                    st.columns([1.5, 2, 2, 2, 2, 4])
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
                col_chave.write(item.get("chave_acesso") or "-")

    except Exception as e:
        st.error(f"Erro ao carregar dados de compras: {e}")
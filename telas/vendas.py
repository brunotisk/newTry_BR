import streamlit as st
import tempfile
from datetime import datetime

from db import supabase
from vendas_import import ler_planilha, importar_vendas_excel
from telas.vendas_manual import tela_vendas_manual


def _fmt_moeda(valor) -> str:
    return (
        f"R$ {float(valor or 0):,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


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


def _secao_listagem():
    try:
        response = (
            supabase
            .table("vendas")
            .select(
                "id, canal_venda, quantidade, valor_unitario, valor_desconto,"
                " valor_total, status, data_venda, cliente,"
                " produtos(descricao, codigo_interno)"
            )
            .order("data_venda", desc=True)
            .execute()
        )
        vendas = response.data or []
    except Exception as e:
        st.error(f"Erro ao carregar vendas: {e}")
        return

    total_vendas = len(vendas)
    soma_total = sum(float(v.get("valor_total") or 0) for v in vendas)

    data_ultima = "-"
    if vendas and vendas[0].get("data_venda"):
        dt_ultima = datetime.fromisoformat(str(vendas[0]["data_venda"])[:10])
        data_ultima = dt_ultima.strftime("%d/%m/%y")

    col_kpi1, col_kpi2, col_kpi3 = st.columns(3)

    with col_kpi1:
        with st.container(border=True):
            st.caption("Total de Vendas")
            st.title(f"{total_vendas}")

    with col_kpi2:
        with st.container(border=True):
            st.caption("Valor Vendido")
            st.title(_fmt_moeda(soma_total))

    with col_kpi3:
        with st.container(border=True):
            st.caption("Última Venda")
            st.title(data_ultima)

    st.markdown("---")

    if not vendas:
        st.info("Nenhuma venda registrada.")
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
            col_canal.write(v.get("canal_venda") or "-")

            produto = v.get("produtos") or {}
            col_prod.write(produto.get("descricao") or "-")

            col_qtd.write(v.get("quantidade"))
            col_total.write(_fmt_moeda(v.get("valor_total")))
            col_status.write(v.get("status") or "-")
            col_cliente.write(v.get("cliente") or "-")


def tela_vendas():
    st.header("💰 Gestão de Vendas")

    aba_listagem, aba_importar, aba_manual = st.tabs(
        ["Vendas registradas", "Importar planilha", "Cadastrar venda"]
    )

    with aba_listagem:
        _secao_listagem()

    with aba_importar:
        _secao_importar()

    with aba_manual:
        tela_vendas_manual()

import streamlit as st
from datetime import date

from db import supabase
from vendas_import import get_client, buscar_produto_id, inserir_venda_e_baixar_estoque


def _fmt_moeda(valor) -> str:
    return (
        f"R$ {float(valor or 0):,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


def tela_vendas_manual():
    st.subheader("✍️ Cadastrar venda manualmente")

    codigo = st.text_input(
        "Código interno do produto",
        placeholder="Ex.: 11389",
        key="vendas_manual_codigo",
    )

    produto = None
    if codigo:
        try:
            resp = (
                supabase
                .table("produtos")
                .select("id, codigo_interno, descricao")
                .eq("codigo_interno", codigo.strip())
                .execute()
            )
            produto = resp.data[0] if resp.data else None
        except Exception as e:
            st.error(f"Erro ao buscar produto: {e}")
            produto = None

        if produto:
            st.success(f"Produto encontrado: **{produto['descricao']}**")
        else:
            st.warning("Produto não encontrado para esse código interno.")

    with st.form("form_venda_manual"):
        canal_venda = st.selectbox("Canal de venda", ["Salão", "Feira", "Online", "Outro"])
        quantidade = st.number_input("Quantidade", min_value=1, step=1, value=1)
        valor_unitario = st.number_input(
            "Valor unitário (R$)", min_value=0.0, step=0.01, format="%.2f"
        )
        valor_desconto = st.number_input(
            "Desconto (R$)", min_value=0.0, step=0.01, format="%.2f"
        )
        status = st.selectbox("Status", ["Pendente", "Pago", "Cancelado"])
        data_venda = st.date_input("Data da venda", value=date.today())
        cliente = st.text_input("Cliente")

        valor_total = max(quantidade * valor_unitario - valor_desconto, 0)
        st.caption(f"Valor total calculado: {_fmt_moeda(valor_total)}")

        salvar = st.form_submit_button("💾 Registrar venda", type="primary")

    if salvar:
        if not codigo or not produto:
            st.error("Informe um código interno de produto válido antes de salvar.")
            return
        if not cliente.strip():
            st.error("Informe o cliente.")
            return

        try:
            sb = get_client()
            produto_id = buscar_produto_id(sb, codigo)
            if produto_id is None:
                st.error("Produto não encontrado. Verifique o código interno.")
                return

            linha = {
                "canal_venda": canal_venda,
                "quantidade": quantidade,
                "valor_unitario": valor_unitario,
                "valor_desconto": valor_desconto,
                "valor_total": valor_total,
                "status": status,
                "data_venda": data_venda.isoformat(),
                "cliente": cliente.strip(),
            }
            inserir_venda_e_baixar_estoque(sb, linha, produto_id)
            st.success("Venda registrada com sucesso e estoque atualizado!")
            st.rerun()
        except Exception as e:
            st.error(f"Erro ao registrar venda: {e}")

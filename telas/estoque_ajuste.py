import streamlit as st
from datetime import date

from db import supabase
from estoque_ajuste import get_client, registrar_ajuste

def _fmt_qtd(valor) -> str:
    """Mostra quantidade sem casas decimais desnecessárias (ex.: 3 em vez de 3.0)."""
    valor = float(valor or 0)
    return f"{valor:g}"

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


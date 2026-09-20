import streamlit as st
from servicos.compras_import import parse_nfe_string, importar_nfe
import tempfile


def tela_importar_nf():
    arquivo = st.file_uploader("Selecione o XML da nota fiscal", type=["xml"])

    if arquivo is not None:
        conteudo = arquivo.read().decode("utf-8")
        nota = parse_nfe_string(conteudo)

        st.subheader("Pré-visualização")
        col1, col2 = st.columns(2)
        col1.metric("Fornecedor", nota.fornecedor_razao_social)
        col2.metric("Valor total", f"R$ {nota.valor_total}")
        st.write(f"NF nº **{nota.numero_nf}** série {nota.serie} — {len(nota.itens)} itens")

        st.dataframe(
            [{"Código": i.codigo_interno, "Produto": i.descricao, "Qtd": float(i.quantidade),
              "Valor unit.": float(i.valor_unitario)} for i in nota.itens],
            use_container_width=True,   
        )

        if st.button("Confirmar e importar", type="primary"):
            with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as tmp:
                tmp.write(conteudo)
                tmp_path = tmp.name

            with st.spinner("Importando..."):
                resultado = importar_nfe(tmp_path)

            if resultado["status"] == "ja_importada":
                st.warning(f"Essa nota (chave {resultado['chave_acesso']}) já tinha sido importada antes. Nada foi duplicado.")
            else:
                st.success(f"Importado! {resultado['itens_processados']} itens processados, "
                           f"compra #{resultado['compra_id']} registrada, estoque atualizado.")

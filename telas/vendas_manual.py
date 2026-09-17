import streamlit as st
from datetime import date

from db import supabase
from vendas_import import get_client, inserir_venda_e_baixar_estoque
from listas_venda import listar


def _fmt_moeda(valor) -> str:
    return (
        f"R$ {float(valor or 0):,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


def _fmt_qtd(valor) -> str:
    valor = float(valor or 0)
    return f"{valor:g}"


def _opcoes_produtos_em_estoque() -> dict:
    """Produtos com saldo em estoque > 0, prontos para a busca."""
    try:
        resp = (
            supabase
            .table("estoque")
            .select("quantidade_atual, produtos(id, codigo_interno, descricao)")
            .gt("quantidade_atual", 0)
            .execute()
        )
    except Exception as e:
        st.error(f"Erro ao carregar produtos em estoque: {e}")
        return {}

    opcoes = {}
    for linha in resp.data or []:
        prod = linha.get("produtos")
        if not prod:
            continue
        rotulo = (
            f"{prod['codigo_interno']} — {prod['descricao']} "
            f"(saldo: {_fmt_qtd(linha.get('quantidade_atual'))})"
        )
        opcoes[rotulo] = {"id": prod["id"], "descricao": prod["descricao"]}

    return dict(sorted(opcoes.items()))


def _campo_lista(tabela: str, label: str, key: str):
    """Selectbox simples de uma lista de apoio (canal de venda ou status).
    O cadastro/edição dessas listas fica na tela 'Cadastros Auxiliares'."""
    try:
        itens_ativos = listar(tabela, apenas_ativos=True)
    except Exception as e:
        st.error(f"Erro ao carregar {label.lower()}: {e}")
        return None, None

    if not itens_ativos:
        st.warning(
            f"Nenhum {label.lower()} ativo cadastrado. "
            f"Cadastre em Vendas → Cadastros Auxiliares."
        )
        return None, None

    opcoes = {item["nome"]: item["id"] for item in itens_ativos}
    nome_selecionado = st.selectbox(label, options=list(opcoes.keys()), key=key)
    return opcoes[nome_selecionado], nome_selecionado


def tela_vendas_manual():
    st.subheader("✍️ Cadastrar venda manualmente")

    opcoes_produtos = _opcoes_produtos_em_estoque()
    if not opcoes_produtos:
        st.warning("Nenhum produto com saldo em estoque no momento.")
        return

    MAX_RESULTADOS = 15

    st.markdown("**Produto**")
    termo_busca = st.text_input(
        "Buscar produto",
        placeholder="Digite o código ou o nome do produto...",
        key="busca_produto_manual",
        label_visibility="collapsed",
    )

    produto = None

    if not termo_busca.strip():
        st.caption("Digite para buscar um produto com saldo em estoque.")
    else:
        termo = termo_busca.strip().lower()
        opcoes_filtradas = {
            rotulo: dados for rotulo, dados in opcoes_produtos.items()
            if termo in rotulo.lower()
        }

        if not opcoes_filtradas:
            st.info("Nenhum produto encontrado para essa busca.")
        else:
            rotulos = list(opcoes_filtradas.keys())
            if len(rotulos) > MAX_RESULTADOS:
                st.caption(
                    f"{len(rotulos)} resultados encontrados, mostrando os primeiros "
                    f"{MAX_RESULTADOS}. Refine a busca para ver outros."
                )
                rotulos = rotulos[:MAX_RESULTADOS]

            rotulo_selecionado = st.radio(
                "Resultados",
                rotulos,
                label_visibility="collapsed",
            )
            produto = opcoes_filtradas.get(rotulo_selecionado)

    canal_id, _ = _campo_lista("canais_venda", "Canal de venda", key="select_canal_venda")
    status_id, _ = _campo_lista("status_venda", "Status", key="select_status_venda")

    quantidade = st.number_input("Quantidade", min_value=1, step=1, value=1)
    valor_unitario = st.number_input(
        "Valor unitário (R$)", min_value=0.0, step=0.01, format="%.2f"
    )
    valor_desconto = st.number_input(
        "Desconto (R$)", min_value=0.0, step=0.01, format="%.2f"
    )
    data_venda = st.date_input("Data da venda", value=date.today())
    cliente = st.text_input("Cliente")

    valor_total = max(quantidade * valor_unitario - valor_desconto, 0)
    st.caption(f"Valor total calculado: {_fmt_moeda(valor_total)}")

    if st.button("💾 Registrar venda", type="primary"):
        if not produto:
            st.error("Selecione um produto.")
            return
        if not canal_id or not status_id:
            st.error("Selecione o canal de venda e o status.")
            return
        if not cliente.strip():
            st.error("Informe o cliente.")
            return

        try:
            sb = get_client()
            linha = {
                "canal_venda_id": canal_id,
                "status_id": status_id,
                "quantidade": quantidade,
                "valor_unitario": valor_unitario,
                "valor_desconto": valor_desconto,
                "valor_total": valor_total,
                "data_venda": data_venda.isoformat(),
                "cliente": cliente.strip(),
            }
            inserir_venda_e_baixar_estoque(sb, linha, produto["id"])
            st.success("Venda registrada com sucesso e estoque atualizado!")
            st.rerun()
        except Exception as e:
            st.error(f"Erro ao registrar venda: {e}")

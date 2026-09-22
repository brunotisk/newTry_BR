import streamlit as st
from datetime import datetime
from db import supabase
from auth import usuario_e_admin
from servicos.compras_import import excluir_compra


def _fmt_moeda(valor) -> str:
    try:
        v = float(valor or 0)
        return (
            f"R$ {v:,.2f}"
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )
    except Exception:
        return "R$ 0,00"


def _fmt_data(data_iso) -> str:
    if not data_iso:
        return "-"
    try:
        s = str(data_iso)[:10]
        ano, mes, dia = s.split("-")
        return f"{dia}/{mes}/{ano}"
    except Exception:
        return str(data_iso)


def _secao_exclusao_compras():
    st.subheader("🗑️ Exclusão de Compras (XML)")
    st.caption(
        "Esta rotina estorna as quantidades dos itens comprados no estoque (com registro no ledger de movimentações) "
        "e exclui a compra, liberando a chave de acesso para eventual reimportação."
    )

    try:
        resp_compras = (
            supabase.table("compras")
            .select(
                "id, numero_nf, data_emissao, valor_total, chave_acesso, "
                "compra_origem, fornecedor_id, fornecedores(razao_social, nome_fantasia)"
            )
            .order("data_emissao", desc=True)
            .execute()
        )
        compras = resp_compras.data or []
    except Exception as e:
        st.error(f"Erro ao carregar lista de compras: {e}")
        return

    if not compras:
        st.info("Nenhuma compra encontrada no banco de dados.")
        return

    def _rotulo_compra(c: dict) -> str:
        nf = c.get("numero_nf") or "Sem NF"
        forn = (c.get("fornecedores") or {}).get("nome_fantasia") or (c.get("fornecedores") or {}).get("razao_social") or "Fornecedor não identificado"
        dt = _fmt_data(c.get("data_emissao"))
        vt = _fmt_moeda(c.get("valor_total"))
        return f"NF {nf} | {forn} | {dt} | {vt} (ID #{c['id']})"

    opcoes = {c["id"]: c for c in compras}
    compra_id_selecionado = st.selectbox(
        "Selecione a compra que deseja excluir",
        options=list(opcoes.keys()),
        format_func=lambda cid: _rotulo_compra(opcoes[cid]),
        key="admin_select_compra_excluir",
    )

    if not compra_id_selecionado:
        return

    compra = opcoes[compra_id_selecionado]
    forn_info = compra.get("fornecedores") or {}
    nome_fornecedor = forn_info.get("nome_fantasia") or forn_info.get("razao_social") or "-"

    # Card com resumo da compra selecionada
    with st.container(border=True):
        c1, c2, c3, c4 = st.columns(4)
        c1.markdown(f"**Número NF:** {compra.get('numero_nf') or '-'}")
        c2.markdown(f"**Data Emissão:** {_fmt_data(compra.get('data_emissao'))}")
        c3.markdown(f"**Valor Total:** {_fmt_moeda(compra.get('valor_total'))}")
        c4.markdown(f"**Origem:** {compra.get('compra_origem') or 'Importação XML'}")

        st.caption(f"**Fornecedor:** {nome_fornecedor} | **Chave:** `{compra.get('chave_acesso') or '-'}`")

    # Carrega itens da compra e consulta estoque atual de cada um
    try:
        resp_itens = (
            supabase.table("compras_itens")
            .select(
                "id, produto_id, quantidade, valor_unitario, valor_total, "
                "produtos(codigo_interno, descricao)"
            )
            .eq("compra_id", compra_id_selecionado)
            .execute()
        )
        itens = resp_itens.data or []
    except Exception as e:
        st.error(f"Erro ao consultar itens da compra: {e}")
        return

    if not itens:
        st.warning("Esta compra não possui itens vinculados.")
        return

    # Consulta saldos em estoque dos produtos afetados
    produto_ids = [it["produto_id"] for it in itens]
    saldos_atuais = {}
    if produto_ids:
        try:
            resp_est = (
                supabase.table("estoque")
                .select("produto_id, quantidade_atual")
                .in_("produto_id", produto_ids)
                .execute()
            )
            for row in (resp_est.data or []):
                saldos_atuais[row["produto_id"]] = float(row.get("quantidade_atual") or 0)
        except Exception as e:
            st.error(f"Erro ao consultar saldos atuais de estoque: {e}")

    # Monta lista para a tabela de impacto
    dados_tabela = []
    tem_negativo = False

    for it in itens:
        prod = it.get("produtos") or {}
        pid = it["produto_id"]
        qtd_comprada = float(it.get("quantidade") or 0)
        saldo_atual = saldos_atuais.get(pid, 0.0)
        saldo_pos = saldo_atual - qtd_comprada

        if saldo_pos < 0:
            tem_negativo = True
            status = "🔴 Ficará negativo"
        elif saldo_pos == 0:
            status = "🟡 Ficará zerado"
        else:
            status = "🟢 Positivo"

        dados_tabela.append({
            "Código": prod.get("codigo_interno") or "-",
            "Descrição": prod.get("descricao") or "-",
            "Qtd na NF (Estorno)": f"-{qtd_comprada:g}",
            "Saldo Atual": f"{saldo_atual:g}",
            "Saldo Projetado": f"{saldo_pos:g}",
            "Status Pós-Exclusão": status,
        })

    st.markdown("#### Prévia de Impacto no Estoque")

    if tem_negativo:
        st.warning(
            "⚠️ **Atenção:** Um ou mais produtos ficarão com **saldo de estoque negativo**. "
            "Isso significa que já houve saídas ou vendas registradas para esses itens após a importação desta compra."
        )

    st.dataframe(dados_tabela, use_container_width=True, hide_index=True)

    st.divider()

    # Confirmação e ação de exclusão
    st.markdown("#### Confirmar Exclusão Definitiva")
    st.write(
        "Ao confirmar, o sistema irá:  \n"
        "1. Gravar movimentos de ajuste no ledger estornando a quantidade de cada produto.  \n"
        "2. Recalcular os dados de última compra e número de compras dos produtos afetados.  \n"
        "3. Excluir anexos vinculados e remover o registro da compra e de seus itens."
    )

    nf_esperada = str(compra.get("numero_nf") or "").strip()
    col_conf, col_btn = st.columns([1.5, 1])

    with col_conf:
        confirmacao_texto = st.text_input(
            f"Digite o número da NF ({nf_esperada}) para habilitar o botão de exclusão:",
            key=f"admin_confirm_nf_{compra_id_selecionado}",
        )

    pode_excluir = confirmacao_texto.strip() == nf_esperada

    with col_btn:
        st.write("")  # Espaçamento vertical
        st.write("")
        if st.button(
            "🗑️ Excluir Compra e Estornar Estoque",
            type="primary",
            disabled=not pode_excluir,
            use_container_width=True,
            key=f"btn_admin_excluir_{compra_id_selecionado}",
        ):
            with st.spinner("Excluindo compra e ajustando estoque..."):
                try:
                    resultado = excluir_compra(supabase, compra_id_selecionado)
                    st.success(
                        f"✅ Compra #{resultado['compra_id']} (NF {resultado['numero_nf']}) excluída com sucesso! "
                        f"{resultado['itens_estornados']} itens estornados no estoque e ledger."
                    )
                    st.rerun()
                except Exception as e:
                    st.error(f"Erro ao excluir compra: {e}")


def _secao_info_sistema():
    st.subheader("ℹ️ Informações Administrativas")
    st.write(
        "Área destinada a módulos operacionais e de manutenção avançada do sistema.  \n"
        "Acesso restrito ao administrador (`bruno.ishikawa@gmail.com`) ou ambiente de desenvolvimento (`SKIP_AUTH=True`)."
    )


def tela_admin():
    if not usuario_e_admin():
        st.error("⛔ Acesso não autorizado. Esta área é restrita a administradores.")
        st.stop()

    st.header("⚙️ Painel de Administração")

    aba_compras, aba_info = st.tabs([
        "🗑️ Exclusão de Compras (XML)",
        "ℹ️ Informações do Sistema",
    ])

    with aba_compras:
        _secao_exclusao_compras()

    with aba_info:
        _secao_info_sistema()

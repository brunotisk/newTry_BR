import streamlit as st
import tempfile
from datetime import date, datetime

from db import supabase
from vendas_import import (
    ler_planilha,
    importar_vendas_excel,
    buscar_produto_id,
    normalizar_codigo_interno,
    editar_venda,
    excluir_venda,
    get_client,
)
from telas.vendas_manual import tela_vendas_manual

# Compatibilidade: st.dialog é o nome estável (Streamlit >= 1.31); versões
# um pouco mais antigas ainda expõem a mesma coisa como st.experimental_dialog.
_dialog = getattr(st, "dialog", None) or st.experimental_dialog

MESES_PT = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
    5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
    9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
}


def _parse_data_segura(valor):
    """Converte 'YYYY-MM-DD...' (ou vazio/None/inválido) em date, sem lançar exceção."""
    texto = str(valor or "")[:10]
    if not texto:
        return None
    try:
        return datetime.strptime(texto, "%Y-%m-%d").date()
    except ValueError:
        return None


def _fmt_moeda(valor) -> str:
    return (
        f"R$ {float(valor or 0):,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


@_dialog("✏️ Editar venda")
def _dialog_editar_venda(venda: dict):
    produto = venda.get("produtos") or {}
    codigo_atual = produto.get("codigo_interno") or ""

    data_atual = datetime.fromisoformat(str(venda["data_venda"])[:10]).date()

    st.caption(f"Venda #{venda['id']}")

    with st.form(f"form_editar_venda_{venda['id']}"):
        codigo_interno = st.text_input("Código interno do produto", value=codigo_atual)
        canal_venda = st.text_input("Canal de venda", value=venda.get("canal_venda") or "")
        quantidade = st.number_input(
            "Quantidade", min_value=0.01, step=1.0, format="%.2f",
            value=float(venda.get("quantidade") or 0.01),
        )
        valor_unitario = st.number_input(
            "Valor unitário", min_value=0.0, step=0.01, format="%.2f",
            value=float(venda.get("valor_unitario") or 0),
        )
        valor_desconto = st.number_input(
            "Desconto", min_value=0.0, step=0.01, format="%.2f",
            value=float(venda.get("valor_desconto") or 0),
        )
        valor_total = st.number_input(
            "Valor total", min_value=0.0, step=0.01, format="%.2f",
            value=float(venda.get("valor_total") or 0),
        )
        status = st.text_input("Status", value=venda.get("status") or "Pendente")
        data_venda = st.date_input("Data da venda", value=data_atual)
        cliente = st.text_input("Cliente", value=venda.get("cliente") or "")

        col_salvar, col_cancelar = st.columns(2)
        salvar = col_salvar.form_submit_button("💾 Salvar alterações", type="primary", use_container_width=True)
        cancelar = col_cancelar.form_submit_button("Cancelar", use_container_width=True)

    if cancelar:
        st.rerun()

    if salvar:
        try:
            sb = get_client()
            codigo_normalizado = normalizar_codigo_interno(codigo_interno)
            novo_produto_id = buscar_produto_id(sb, codigo_normalizado)
            if novo_produto_id is None:
                st.error(f"Produto com código '{codigo_interno}' não encontrado.")
                return

            dados_novos = {
                "produto_id": novo_produto_id,
                "canal_venda": canal_venda.strip(),
                "quantidade": float(quantidade),
                "valor_unitario": float(valor_unitario),
                "valor_desconto": float(valor_desconto),
                "valor_total": float(valor_total),
                "status": status.strip() or "Pendente",
                "data_venda": data_venda.isoformat(),
                "cliente": cliente.strip(),
            }

            editar_venda(sb, venda, dados_novos)
            st.success("Venda atualizada e estoque ajustado com sucesso!")
            st.rerun()
        except Exception as e:
            st.error(f"Erro ao salvar alterações: {e}")


@_dialog("🗑️ Excluir venda")
def _dialog_excluir_venda(venda: dict):
    produto = venda.get("produtos") or {}
    st.warning(
        f"Tem certeza que deseja excluir a venda **#{venda['id']}** "
        f"({produto.get('descricao') or '-'} — cliente: {venda.get('cliente') or '-'})?\n\n"
        "Essa ação não pode ser desfeita. A quantidade vendida será devolvida ao estoque."
    )
    col_confirmar, col_cancelar = st.columns(2)
    if col_confirmar.button("Sim, excluir", type="primary", use_container_width=True):
        try:
            sb = get_client()
            excluir_venda(sb, venda)
            st.success("Venda excluída e estoque ajustado.")
            st.rerun()
        except Exception as e:
            st.error(f"Erro ao excluir venda: {e}")
    if col_cancelar.button("Cancelar", use_container_width=True):
        st.rerun()


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
                "id, produto_id, canal_venda, quantidade, valor_unitario, valor_desconto,"
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

    hoje = date.today()
    mes_atual, ano_atual = hoje.month, hoje.year
    if mes_atual == 1:
        mes_anterior, ano_anterior = 12, ano_atual - 1
    else:
        mes_anterior, ano_anterior = mes_atual - 1, ano_atual

    soma_mes_atual = 0.0
    soma_mes_anterior = 0.0
    for v in vendas:
        dt_venda = _parse_data_segura(v.get("data_venda"))
        if dt_venda is None:
            continue

        valor = float(v.get("valor_total") or 0)
        if dt_venda.year == ano_atual and dt_venda.month == mes_atual:
            soma_mes_atual += valor
        elif dt_venda.year == ano_anterior and dt_venda.month == mes_anterior:
            soma_mes_anterior += valor

    label_mes_atual = f"Vendas Mês ({MESES_PT[mes_atual]})"
    label_mes_anterior = f"Vendas Mês Anterior ({MESES_PT[mes_anterior]})"

    col_kpi1, col_kpi2, col_kpi3, col_kpi4 = st.columns(4)

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
            st.caption(label_mes_atual)
            st.title(_fmt_moeda(soma_mes_atual))

    with col_kpi4:
        with st.container(border=True):
            st.caption(label_mes_anterior)
            st.title(_fmt_moeda(soma_mes_anterior))

    st.markdown("---")

    if not vendas:
        st.info("Nenhuma venda registrada.")
        return

    # Opções de filtro construídas a partir dos dados carregados
    meses_disponiveis = sorted(
        {
            (dt.year, dt.month)
            for v in vendas
            if (dt := _parse_data_segura(v.get("data_venda"))) is not None
        },
        reverse=True,
    )
    opcoes_mes = ["Todos"] + [f"{MESES_PT[m]}/{a}" for (a, m) in meses_disponiveis]
    opcoes_canal = ["Todos"] + sorted(
        {(v.get("canal_venda") or "").strip() for v in vendas if (v.get("canal_venda") or "").strip()}
    )

    col_filtro_mes, col_filtro_canal = st.columns(2)
    with col_filtro_mes:
        mes_selecionado = st.selectbox("Mês da venda", opcoes_mes)
    with col_filtro_canal:
        canal_selecionado = st.selectbox("Canal", opcoes_canal)

    vendas_filtradas = vendas
    if mes_selecionado != "Todos":
        ano_f, mes_f = meses_disponiveis[opcoes_mes.index(mes_selecionado) - 1]
        vendas_filtradas = [
            v for v in vendas_filtradas
            if (dt := _parse_data_segura(v.get("data_venda"))) and dt.year == ano_f and dt.month == mes_f
        ]
    if canal_selecionado != "Todos":
        vendas_filtradas = [
            v for v in vendas_filtradas if (v.get("canal_venda") or "").strip() == canal_selecionado
        ]

    if not vendas_filtradas:
        st.info("Nenhuma venda encontrada para os filtros selecionados.")
        return

    with st.container(border=True):
        c_data, c_canal, c_prod, c_qtd, c_total, c_status, c_cliente, c_acoes = st.columns(
            [1.4, 1.3, 2.8, 0.9, 1.4, 1.3, 1.7, 1.2]
        )
        c_data.markdown("**Data**")
        c_canal.markdown("**Canal**")
        c_prod.markdown("**Produto**")
        c_qtd.markdown("**Qtd**")
        c_total.markdown("**Valor Total**")
        c_status.markdown("**Status**")
        c_cliente.markdown("**Cliente**")
        c_acoes.markdown("**Ações**")

        st.divider()

        for v in vendas_filtradas:
            (
                col_data, col_canal, col_prod, col_qtd,
                col_total, col_status, col_cliente, col_acoes,
            ) = st.columns([1.4, 1.3, 2.8, 0.9, 1.4, 1.3, 1.7, 1.2])

            col_data.write(str(v.get("data_venda") or "-")[:10])
            col_canal.write(v.get("canal_venda") or "-")

            produto = v.get("produtos") or {}
            col_prod.write(produto.get("descricao") or "-")

            col_qtd.write(v.get("quantidade"))
            col_total.write(_fmt_moeda(v.get("valor_total")))
            col_status.write(v.get("status") or "-")
            col_cliente.write(v.get("cliente") or "-")

            with col_acoes:
                col_editar, col_excluir = st.columns(2)
                if col_editar.button("✏️", key=f"editar_venda_{v['id']}", help="Editar venda", use_container_width=True):
                    _dialog_editar_venda(v)
                if col_excluir.button("🗑️", key=f"excluir_venda_{v['id']}", help="Excluir venda", use_container_width=True):
                    _dialog_excluir_venda(v)


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

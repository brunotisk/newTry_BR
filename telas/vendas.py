import streamlit as st
import tempfile
import math
from datetime import date, datetime
from typing import Optional

from db import supabase
from vendas_import import (
    ler_planilha,
    importar_vendas_excel,
    buscar_produto_id,
    editar_venda,
    excluir_venda,
    get_client,
    inserir_venda_e_baixar_estoque,
)
from telas.cadastros_auxiliares import tela_cadastros_auxiliares
from componentes.paginacao import render_paginacao, get_itens_por_pagina, reset_paginacao

# Compatibilidade: st.dialog é o nome estável (Streamlit >= 1.31); versões
# um pouco mais antigas ainda expõem a mesma coisa como st.experimental_dialog.
_dialog = getattr(st, "dialog", None) or st.experimental_dialog

MESES_PT = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
    5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
    9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
}


def _fmt_moeda(valor) -> str:
    return (
        f"R$ {float(valor or 0):,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


def _parse_data_segura(valor):
    """Converte 'YYYY-MM-DD...' (ou vazio/None/inválido) em date, sem lançar exceção."""
    texto = str(valor or "")[:10]
    if not texto:
        return None
    try:
        return datetime.strptime(texto, "%Y-%m-%d").date()
    except ValueError:
        return None


def _injetar_estilo_kpi():
    """Garante altura idêntica para todos os cards de KPI fixando a altura do subtítulo."""
    st.markdown(
        """
        <style>
        /* Desativa corte de texto nos valores dos KPIs */
        div[data-testid="stMetricValue"] {
            overflow: visible;
            white-space: normal;
            word-break: break-word;
            font-size: 1.35rem;
            line-height: 1.2;
        }
        
        /* Container do subtítulo com altura fixa para padronização */
        .kpi-subtitulo {
            text-align: center;
            font-size: 0.75rem;
            color: #9aa0ab;
            height: 18px;
            line-height: 18px;
            margin-bottom: 0.3rem;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _kpi_card(titulo: str, qtd: int, valor: float, subtitulo: Optional[str] = None):
    with st.container(border=True):
        st.markdown(
            f"<div style='text-align:center; font-weight:700; margin-bottom:0.1rem;'>{titulo}</div>",
            unsafe_allow_html=True,
        )
        
        # Garante que sempre haverá a linha do subtítulo para manter a mesma altura em todos os cards
        sub_texto = subtitulo if subtitulo else "&nbsp;"
        st.markdown(
            f"<div class='kpi-subtitulo'>{sub_texto}</div>",
            unsafe_allow_html=True,
        )

        col_qtd, col_valor = st.columns(2)
        with col_qtd:
            st.metric("Qtd. Vendas", qtd)
        with col_valor:
            st.metric("Valor", _fmt_moeda(valor))


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


@_dialog("💰 Venda", width="large")
def _dialog_editar_venda(
    venda: dict | None,
    canais_disponiveis: list,
    status_disponiveis: list,
    formas_pagamento_disponiveis: list,
    feiras_disponiveis: list,
):
    """Popup único para adicionar e editar vendas.

    Na inclusão, produto e canal podem ser escolhidos.
    Na edição, produto e canal permanecem bloqueados para preservar o
    comportamento atual do ajuste de estoque.
    """
    nova_venda = venda is None
    venda = venda or {}

    produto_atual = venda.get("produtos") or {}
    codigo_atual = produto_atual.get("codigo_interno") or ""

    canal_atual = venda.get("canais_venda") or {}
    canal_atual_nome = canal_atual.get("nome") or ""

    status_atual_nome = (venda.get("status_venda") or {}).get("nome") or ""
    forma_atual = venda.get("formas_pagamento") or {}
    forma_atual_desc = forma_atual.get("descricao") or ""
    feira_atual = venda.get("detalhes_feira") or {}
    feira_atual_id = venda.get("detalhe_feira_id")

    nomes_status = [s["nome"] for s in status_disponiveis]
    nomes_canais = [c["nome"] for c in canais_disponiveis]
    nomes_formas = [f["descricao"] for f in formas_pagamento_disponiveis]

    # Produtos disponíveis em estoque para uma nova venda.
    produtos_opcoes = {}
    if nova_venda:
        try:
            resp_produtos = (
                supabase
                .table("estoque")
                .select("quantidade_atual, produtos(id, codigo_interno, descricao)")
                .gt("quantidade_atual", 0)
                .execute()
            )
            for linha in resp_produtos.data or []:
                produto = linha.get("produtos")
                if produto:
                    rotulo = (
                        f"{produto['codigo_interno']} — {produto['descricao']} "
                        f"(saldo: {float(linha.get('quantidade_atual') or 0):g})"
                    )
                    produtos_opcoes[rotulo] = produto
        except Exception as e:
            st.error(f"Erro ao carregar produtos em estoque: {e}")

    data_atual = _parse_data_segura(venda.get("data_venda")) or date.today()

    st.caption("Nova venda" if nova_venda else f"Venda #{venda['id']}")

    form_key = f"form_venda_{'nova' if nova_venda else venda['id']}"

    with st.form(form_key):
        # Linha 1: Produto | Canal | Status
        col_produto, col_canal, col_status = st.columns(3)

        with col_produto:
            if nova_venda:
                if produtos_opcoes:
                    rotulos_produtos = list(produtos_opcoes.keys())
                    produto_rotulo = st.selectbox(
                        "Produto",
                        rotulos_produtos,
                        index=None,
                        placeholder="Selecione o produto...",
                    )
                    produto_selecionado = produtos_opcoes.get(produto_rotulo)
                else:
                    st.warning("Nenhum produto com saldo em estoque.")
                    produto_selecionado = None
            else:
                st.text_input(
                    "Código interno do produto",
                    value=codigo_atual,
                    disabled=True,
                )
                produto_selecionado = {
                    "id": venda.get("produto_id"),
                    "codigo_interno": codigo_atual,
                }

        with col_canal:
            if nova_venda:
                canal_nome = st.selectbox(
                    "Canal de venda",
                    nomes_canais,
                    index=None,
                    placeholder="Selecione o canal...",
                ) if nomes_canais else None
            else:
                st.text_input("Canal de venda", value=canal_atual_nome, disabled=True)
                canal_nome = canal_atual_nome

        with col_status:
            status_nome = st.selectbox(
                "Status",
                nomes_status,
                index=nomes_status.index(status_atual_nome)
                if status_atual_nome in nomes_status else 0,
            ) if nomes_status else None

        # Linha 2: Quantidade | Valor unitário | Desconto | Valor total
        col_qtd, col_unit, col_desc, col_total = st.columns(4)

        with col_qtd:
            quantidade = st.number_input(
                "Quantidade",
                min_value=0.01,
                step=1.0,
                format="%.2f",
                value=float(venda.get("quantidade") or 1),
            )

        with col_unit:
            valor_unitario = st.number_input(
                "Valor unitário",
                min_value=0.0,
                step=0.01,
                format="%.2f",
                value=float(venda.get("valor_unitario") or 0),
            )

        with col_desc:
            valor_desconto = st.number_input(
                "Desconto",
                min_value=0.0,
                step=0.01,
                format="%.2f",
                value=float(venda.get("valor_desconto") or 0),
            )

        with col_total:
            valor_total = st.number_input(
                "Valor total",
                min_value=0.0,
                step=0.01,
                format="%.2f",
                value=float(venda.get("valor_total") or 0),
            )

        # Linha 3: Data | Cliente | Forma de pagamento
        col_data, col_cliente, col_forma = st.columns([1.25, 1.75, 1.25])

        with col_data:
            data_venda = st.date_input("Data da venda", value=data_atual)

        with col_cliente:
            cliente = st.text_input("Cliente", value=venda.get("cliente") or "")

        with col_forma:
            forma_pagamento_nome = st.selectbox(
                "Forma de pagamento",
                [""] + nomes_formas,
                index=(
                    (nomes_formas.index(forma_atual_desc) + 1)
                    if forma_atual_desc in nomes_formas else 0
                ),
                placeholder="Selecione...",
            ) if nomes_formas else None

        # Só existe quando o canal escolhido for Feira.
        canal_eh_feira = (canal_nome or "").strip().lower() == "feira"

        feira_nome = None
        if canal_eh_feira:
            nomes_feiras = [f["nome_feira"] for f in feiras_disponiveis]
            nomes_feiras_com_vazio = [""] + nomes_feiras
            feira_atual_nome = feira_atual.get("nome_feira") or ""
            indice_feira = (
                nomes_feiras_com_vazio.index(feira_atual_nome)
                if feira_atual_nome in nomes_feiras_com_vazio else 0
            )
            feira_nome = st.selectbox(
                "Feira",
                nomes_feiras_com_vazio,
                index=indice_feira,
                placeholder="Selecione a feira...",
            )

        # Espaçamento proposital entre a última linha e os botões.
        st.markdown("<div style='height: 1.25rem;'></div>", unsafe_allow_html=True)

        if nova_venda:
            col_salvar, col_cancelar = st.columns(2)
            salvar = col_salvar.form_submit_button(
                "💾 Salvar", type="primary", use_container_width=True
            )
            cancelar = col_cancelar.form_submit_button(
                "Cancelar", use_container_width=True
            )
            excluir = False
        else:
            col_salvar, col_cancelar, col_excluir = st.columns(3)
            salvar = col_salvar.form_submit_button(
                "💾 Salvar", type="primary", use_container_width=True
            )
            cancelar = col_cancelar.form_submit_button(
                "Cancelar", use_container_width=True
            )
            excluir = col_excluir.form_submit_button(
                "🗑️ Excluir", use_container_width=True
            )

    if cancelar:
        st.rerun()

    if excluir:
        try:
            sb = get_client()
            excluir_venda(sb, venda)
            st.success("Venda excluída e estoque ajustado com sucesso!")
            st.rerun()
        except Exception as e:
            st.error(f"Erro ao excluir venda: {e}")

    if not salvar:
        return

    try:
        sb = get_client()

        if not produto_selecionado:
            st.error("Selecione um produto.")
            return
        if not canal_nome:
            st.error("Selecione o canal de venda.")
            return
        if not status_nome:
            st.error("Selecione o status.")
            return
        if not cliente.strip():
            st.error("Informe o cliente.")
            return
        if not forma_pagamento_nome:
            st.error("Selecione a forma de pagamento.")
            return

        canal_id = next(
            (c["id"] for c in canais_disponiveis if c["nome"] == canal_nome),
            None,
        )
        status_id = next(
            (s["id"] for s in status_disponiveis if s["nome"] == status_nome),
            None,
        )
        forma_pagamento_id = next(
            (
                f["id"]
                for f in formas_pagamento_disponiveis
                if f["descricao"] == forma_pagamento_nome
            ),
            None,
        )

        if canal_id is None or status_id is None or forma_pagamento_id is None:
            st.error("Não foi possível identificar os cadastros selecionados.")
            return

        detalhe_feira_id = None
        if canal_nome.strip().lower() == "feira":
            if not feira_nome:
                st.error("Selecione a feira.")
                return
            detalhe_feira_id = next(
                (
                    f["id"]
                    for f in feiras_disponiveis
                    if f["nome_feira"] == feira_nome
                ),
                None,
            )
            if detalhe_feira_id is None:
                st.error("Não foi possível identificar a feira selecionada.")
                return

        dados_novos = {
            "produto_id": produto_selecionado["id"],
            "canal_venda_id": canal_id,
            "status_id": status_id,
            "quantidade": float(quantidade),
            "valor_unitario": float(valor_unitario),
            "valor_desconto": float(valor_desconto),
            "valor_total": float(valor_total),
            "data_venda": data_venda.isoformat(),
            "cliente": cliente.strip(),
            "forma_pagamento_id": forma_pagamento_id,
            "detalhe_feira_id": detalhe_feira_id,
        }

        if nova_venda:
            inserir_venda_e_baixar_estoque(
                sb,
                dados_novos,
                produto_selecionado["id"],
            )
            st.success("Venda registrada com sucesso e estoque atualizado!")
        else:
            editar_venda(sb, venda, dados_novos)
            st.success("Venda atualizada e estoque ajustado com sucesso!")

        st.rerun()

    except Exception as e:
        acao = "registrar" if nova_venda else "salvar alterações"
        st.error(f"Erro ao {acao} venda: {e}")


def _resetar_filtros_vendas():
    """Callback do botão 'Limpar filtros'. Precisa ser um on_click (e não um
    'if st.button(...)' com atribuição direta), porque alterar
    st.session_state de uma key que já foi usada por um widget nessa MESMA
    execução do script dispara StreamlitWidgetAlreadyInstantiatedError. Um
    callback roda ANTES do script ser reexecutado do zero, então é seguro."""
    st.session_state["vendas_filtro_mes"] = "Todos"
    st.session_state["vendas_filtro_canal"] = "Todos"
    st.session_state["pagina_atual_vendas"] = 1



def _carregar_cadastros_popup():
    """Carrega os cadastros usados pelo popup de inclusão/edição."""
    try:
        canais = (
            supabase.table("canais_venda")
            .select("id, nome")
            .eq("ativo", True)
            .order("nome")
            .execute()
        ).data or []
    except Exception:
        canais = []

    try:
        status = (
            supabase.table("status_venda")
            .select("id, nome")
            .eq("ativo", True)
            .order("nome")
            .execute()
        ).data or []
    except Exception:
        status = []

    try:
        formas = (
            supabase.table("formas_pagamento")
            .select("id, descricao")
            .eq("ativo", True)
            .order("descricao")
            .execute()
        ).data or []
    except Exception:
        formas = []

    try:
        feiras = (
            supabase.table("detalhes_feira")
            .select("id, nome_feira, endereco_feira, pessoa_contato_feira, tel_contato_feira")
            .eq("ativo", True)
            .order("nome_feira")
            .execute()
        ).data or []
    except Exception:
        feiras = []

    return canais, status, formas, feiras



def _secao_listagem():
    # ------------------------------------------------------------------
    # 1) Dataset leve com TODAS as vendas (valor_total, data_venda e o nome
    #    do canal), usado para os KPIs fixos (Total/Ano/Mês atual), para o
    #    KPI "Filtros" e para montar as opções do filtro de mês/ano.
    # ------------------------------------------------------------------
    try:
        response_kpi = (
            supabase
            .table("vendas")
            .select("valor_total, data_venda, canais_venda(nome)")
            .execute()
        )
        vendas_kpi = response_kpi.data or []
    except Exception as e:
        st.error(f"Erro ao carregar vendas: {e}")
        return

    hoje = date.today()
    ano_atual, mes_atual = hoje.year, hoje.month

    def _acumula(filtro):
        qtd, soma = 0, 0.0
        for v in vendas_kpi:
            dt = _parse_data_segura(v.get("data_venda"))
            if dt is None or not filtro(v, dt):
                continue
            qtd += 1
            soma += float(v.get("valor_total") or 0)
        return qtd, soma

    qtd_total = len(vendas_kpi)
    soma_total = sum(float(v.get("valor_total") or 0) for v in vendas_kpi)
    qtd_ano, soma_ano = _acumula(lambda v, dt: dt.year == ano_atual)
    qtd_mes_atual, soma_mes_atual = _acumula(lambda v, dt: dt.year == ano_atual and dt.month == mes_atual)

    # ------------------------------------------------------------------
    # 2) Opções de filtro (mês/ano e canal), calculadas antes dos cards
    #    para que o card "Filtros" já reflita a seleção atual.
    # ------------------------------------------------------------------
    meses_disponiveis = sorted(
        {
            (dt.year, dt.month)
            for v in vendas_kpi
            if (dt := _parse_data_segura(v.get("data_venda"))) is not None
        },
        reverse=True,
    )
    opcoes_mes = ["Todos"] + [f"{MESES_PT[m]}/{a}" for (a, m) in meses_disponiveis]

    try:
        response_canais = (
            supabase.table("canais_venda")
            .select("id, nome")
            .order("nome")
            .execute()
        )
        canais_disponiveis = response_canais.data or []
    except Exception:
        canais_disponiveis = []  # filtro de canal fica indisponível se a consulta falhar

    opcoes_canal = ["Todos"] + [c["nome"] for c in canais_disponiveis]

    try:
        response_status = (
            supabase.table("status_venda")
            .select("id, nome")
            .order("nome")
            .execute()
        )
        status_disponiveis = response_status.data or []
    except Exception:
        status_disponiveis = []  # popup de edição fica sem opções de status se a consulta falhar

    # Seleção atual dos filtros (lida do session_state, com fallback seguro
    # caso a lista de opções tenha mudado desde a última execução)
    mes_selecionado_atual = st.session_state.get("vendas_filtro_mes", "Todos")
    if mes_selecionado_atual not in opcoes_mes:
        mes_selecionado_atual = "Todos"
    canal_selecionado_atual = st.session_state.get("vendas_filtro_canal", "Todos")
    if canal_selecionado_atual not in opcoes_canal:
        canal_selecionado_atual = "Todos"

    def _bate_filtro_atual(v, dt):
        if mes_selecionado_atual != "Todos":
            ano_f, mes_f = meses_disponiveis[opcoes_mes.index(mes_selecionado_atual) - 1]
            if dt.year != ano_f or dt.month != mes_f:
                return False
        if canal_selecionado_atual != "Todos":
            if ((v.get("canais_venda") or {}).get("nome")) != canal_selecionado_atual:
                return False
        return True

    qtd_filtro, soma_filtro = _acumula(_bate_filtro_atual)
    subtitulo_filtro = (
        f"{mes_selecionado_atual if mes_selecionado_atual != 'Todos' else 'Todo período'} · "
        f"{canal_selecionado_atual if canal_selecionado_atual != 'Todos' else 'Todos canais'}"
    )

    # ------------------------------------------------------------------
    # 3) Os 4 cartões de KPI (Todos padronizados com subtítulo)
    # ------------------------------------------------------------------
    _injetar_estilo_kpi()
    col_kpi1, col_kpi2, col_kpi3, col_kpi4 = st.columns(4)
    
    with col_kpi1:
        _kpi_card("Total", qtd_total, soma_total, subtitulo="Todo período")
        
    with col_kpi2:
        _kpi_card("Ano Atual", qtd_ano, soma_ano, subtitulo=f"Ano de {ano_atual}")
        
    with col_kpi3:
        _kpi_card("Mês Atual", qtd_mes_atual, soma_mes_atual, subtitulo=MESES_PT[mes_atual])
        
    with col_kpi4:
        _kpi_card("Filtros", qtd_filtro, soma_filtro, subtitulo=subtitulo_filtro)

    st.markdown("---")

    if not vendas_kpi:
        st.info("Nenhuma venda registrada.")
        return

    # ------------------------------------------------------------------
    # 4) Widgets de filtro (Mês/Ano da venda e Canal de venda) + botão
    #    para limpar os filtros
    # ------------------------------------------------------------------
    col_filtro_mes, col_filtro_canal, col_acoes = st.columns([2, 2, 2])
    with col_filtro_mes:
        mes_selecionado = st.selectbox("Mês da venda", opcoes_mes, key="vendas_filtro_mes")
    with col_filtro_canal:
        canal_selecionado = st.selectbox("Canal", opcoes_canal, key="vendas_filtro_canal")
    with col_acoes:
        st.markdown("<div style='margin-top:1.85rem;'></div>", unsafe_allow_html=True)
        btn_limpar, btn_adicionar = st.columns(2)
        with btn_limpar:
            st.button(
                "🔄 Limpar filtros",
                use_container_width=True,
                key="vendas_btn_limpar_filtros",
                on_click=_resetar_filtros_vendas,
            )
        with btn_adicionar:
            if st.button(
                "➕ Adicionar venda",
                type="secondary",
                use_container_width=True,
                key="vendas_btn_adicionar",
            ):
                canais_popup, status_popup, formas_popup, feiras_popup = _carregar_cadastros_popup()
                _dialog_editar_venda(
                    None,
                    canais_popup,
                    status_popup,
                    formas_popup,
                    feiras_popup,
                )

    # Reseta a página para 1 sempre que algum filtro mudar
    assinatura_filtros = f"{mes_selecionado}|{canal_selecionado}"
    if st.session_state.get("vendas_assinatura_filtros") != assinatura_filtros:
        st.session_state.pagina_atual_vendas = 1
        st.session_state.vendas_assinatura_filtros = assinatura_filtros

    # ------------------------------------------------------------------
    # 5) Paginação + consulta da página filtrada
    # ------------------------------------------------------------------
    # O componente mantém a quantidade selecionada (50/25/10) no estado
    # da sessão para que ela não volte para 50 ao trocar de página.
    if "pagina_atual_vendas" not in st.session_state:
        st.session_state.pagina_atual_vendas = 1

    itens_por_pagina = get_itens_por_pagina("vendas", 50)

    # Embed do canal como inner join só quando o filtro de canal está ativo,
    # para não excluir vendas sem canal preenchido quando não há filtro.
    canal_embed = "canais_venda!inner(nome)" if canal_selecionado != "Todos" else "canais_venda(nome)"

    def _monta_query():
        query = (
            supabase
            .table("vendas")
            .select(
                "id, produto_id, quantidade, valor_unitario, valor_desconto,"
                " valor_total, data_venda, cliente,"
                f" produtos(descricao, codigo_interno), {canal_embed}, "
                "status_venda(nome), formas_pagamento(descricao), "
                "detalhes_feira(nome_feira)",
                count="exact",
            )
        )

        if mes_selecionado != "Todos":
            ano_f, mes_f = meses_disponiveis[opcoes_mes.index(mes_selecionado) - 1]
            inicio = date(ano_f, mes_f, 1)
            fim = date(ano_f + 1, 1, 1) if mes_f == 12 else date(ano_f, mes_f + 1, 1)
            query = query.gte("data_venda", inicio.isoformat()).lt("data_venda", fim.isoformat())

        if canal_selecionado != "Todos":
            query = query.eq("canais_venda.nome", canal_selecionado)

        return query

    try:
        total_resp = _monta_query().order("data_venda", desc=True).range(0, 0).execute()
        total_filtrado = total_resp.count or 0
    except Exception as e:
        st.error(f"Erro ao carregar vendas: {e}")
        return

    total_paginas = math.ceil(total_filtrado / itens_por_pagina) if total_filtrado > 0 else 1
    if st.session_state.pagina_atual_vendas > total_paginas:
        st.session_state.pagina_atual_vendas = total_paginas

    # Contagem no topo, antes da tabela, sem criar controles duplicados.
    render_paginacao(
        "vendas",
        total_filtrado,
        itens_por_pagina,
        mostrar_contagem_superior=True,
        mostrar_contagem_inferior=False,
    )

    offset = (st.session_state.pagina_atual_vendas - 1) * itens_por_pagina

    if total_filtrado == 0:
        st.info("Nenhuma venda encontrada para os filtros selecionados.")
        return

    try:
        response = (
            _monta_query()
            .order("data_venda", desc=True)
            .range(offset, offset + itens_por_pagina - 1)
            .execute()
        )
        vendas = response.data or []
    except Exception as e:
        st.error(f"Erro ao carregar a página de vendas: {e}")
        return

    with st.container(border=True):
        c_data, c_canal, c_prod, c_qtd, c_total, c_status, c_cliente, c_acoes = st.columns(
            [1.4, 1.3, 2.8, 0.9, 1.4, 1.3, 1.7, 0.8]
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

        for v in vendas:
            col_data, col_canal, col_prod, col_qtd, col_total, col_status, col_cliente, col_acoes = (
                st.columns([1.4, 1.3, 2.8, 0.9, 1.4, 1.3, 1.7, 0.8])
            )

            col_data.write(str(v.get("data_venda") or "-")[:10])
            col_canal.write((v.get("canais_venda") or {}).get("nome") or "-")

            produto = v.get("produtos") or {}
            col_prod.write(produto.get("descricao") or "-")

            col_qtd.write(v.get("quantidade"))
            col_total.write(_fmt_moeda(v.get("valor_total")))
            col_status.write((v.get("status_venda") or {}).get("nome") or "-")
            col_cliente.write(v.get("cliente") or "-")

            if col_acoes.button(
                "✏️",
                key=f"editar_venda_{v['id']}",
                help="Editar ou excluir venda",
                use_container_width=True,
            ):
                formas_popup = (
                    supabase.table("formas_pagamento")
                    .select("id, descricao")
                    .eq("ativo", True)
                    .order("descricao")
                    .execute()
                ).data or []
                feiras_popup = (
                    supabase.table("detalhes_feira")
                    .select("id, nome_feira, endereco_feira, pessoa_contato_feira, tel_contato_feira")
                    .eq("ativo", True)
                    .order("nome_feira")
                    .execute()
                ).data or []
                _dialog_editar_venda(
                    v,
                    canais_disponiveis,
                    status_disponiveis,
                    formas_popup,
                    feiras_popup,
                )

    # Paginação completa no rodapé da tabela.
    render_paginacao(
        "vendas",
        total_filtrado,
        itens_por_pagina,
        mostrar_contagem_superior=False,
        mostrar_contagem_inferior=True,
    )


def tela_vendas():
    st.header("💰 Gestão de Vendas")

    aba_listagem, aba_importar, aba_auxiliares = st.tabs(
        ["Vendas registradas", "Importar planilha", "Cadastros Auxiliares"]
    )

    with aba_listagem:
        _secao_listagem()

    with aba_importar:
        _secao_importar()

    with aba_auxiliares:
        tela_cadastros_auxiliares()

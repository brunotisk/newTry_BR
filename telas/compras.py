import streamlit as st
from datetime import datetime
from zoneinfo import ZoneInfo
from db import supabase
from telas.importar_nf import tela_importar_nf
from componentes.paginacao import render_paginacao, get_itens_por_pagina, reset_paginacao
from servicos.arquivos_compra import (
    upload_arquivo_compra,
    listar_arquivos_compra,
    gerar_url_arquivo_compra,
    baixar_arquivo_compra,
    excluir_arquivo_compra,
    nome_arquivo_compra,
)

# Compatibilidade: st.dialog é o nome estável (Streamlit >= 1.31); versões
# um pouco mais antigas ainda expõem a mesma coisa como st.experimental_dialog.
_dialog = getattr(st, "dialog", None) or st.experimental_dialog


def _fmt_numero(valor) -> str:
    """Formata um número no padrão BR (1.234,56), sem o prefixo 'R$ '."""
    return (
        f"{float(valor or 0):,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


def _fmt_moeda(valor) -> str:
    return f"R$ {_fmt_numero(valor)}"


def _parse_valor_input(valor, padrao=0.0):
    """Converte valor digitado em formato BR/US para float."""
    if valor is None:
        return float(padrao)
    texto = str(valor).strip()
    if not texto:
        return 0.0
    try:
        if "," in texto:
            texto = texto.replace(".", "").replace(",", ".")
        return max(0.0, float(texto))
    except (TypeError, ValueError):
        return float(padrao)


def _fmt_valor_input(valor):
    return f"{float(valor or 0):.2f}".replace(".", ",")


def _fmt_qtd(valor) -> str:
    """Mostra quantidade sem casas decimais desnecessárias (ex.: 1 em vez de 1.000)."""
    return f"{float(valor or 0):g}"


def _badge_clipe_arquivos() -> str:
    """Indicador de 'NF possui arquivo anexado': um ícone de clipe discreto
    (SVG, sem fundo/caixa) ao lado do número da NF — mais limpo do que o
    emoji dentro de um selo colorido."""
    return (
        '<span title="Esta NF possui arquivos anexados." '
        'style="display:inline-flex;align-items:center;margin-left:6px;'
        'vertical-align:middle;color:#7C93C4;">'
        '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2.4" stroke-linecap="round" '
        'stroke-linejoin="round">'
        '<path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66'
        'l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>'
        '</svg>'
        '</span>'
    )


def _badge_tipo_arquivo(tipo: str) -> str:
    """Selo colorido para o tipo do arquivo (PDF/XML) na lista de anexados."""
    tipo_norm = (tipo or "-").upper()
    cor_texto, cor_fundo = {
        "PDF": ("#f87171", "rgba(239,68,68,.14)"),
        "XML": ("#60a5fa", "rgba(59,130,246,.14)"),
    }.get(tipo_norm, ("#9ca3af", "rgba(156,163,175,.14)"))
    return (
        f'<span style="display:inline-block;padding:3px 10px;border-radius:6px;'
        f'background:{cor_fundo};color:{cor_texto};font-size:12px;font-weight:700;'
        f'letter-spacing:.02em;">{tipo_norm}</span>'
    )


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


@_dialog("✏️ Desconto adicional")
def _dialog_editar_desconto(compra: dict):
    """Popup para preencher/editar o desconto adicional concedido numa compra
    (além do desconto já descrito na própria NF-e) e o motivo dele."""
    st.caption(
        f"NF nº {compra.get('numero_nf') or '-'} — "
        f"Chave: {compra.get('chave_acesso') or '-'}"
    )

    desconto_atual = float(compra.get("compras_desconto_adicional") or 0)
    motivo_atual = compra.get("compras_motivo_desconto") or ""

    with st.form(f"form_desconto_adicional_{compra['id']}", border=False):
        desconto_texto = st.text_input(
            "Desconto adicional (R$)",
            value=_fmt_valor_input(desconto_atual),
            help="Desconto concedido além do que já está descrito na nota fiscal.",
        )
        motivo_texto = st.text_area(
            "Motivo do desconto",
            value=motivo_atual,
            placeholder="Ex.: Avaria no transporte, negociação com o fornecedor...",
        )

        col_salvar, col_cancelar = st.columns(2)
        salvar = col_salvar.form_submit_button(
            "💾 Salvar", type="secondary", use_container_width=True
        )
        cancelar = col_cancelar.form_submit_button(
            "Cancelar", use_container_width=True
        )

    if cancelar:
        st.rerun()

    if not salvar:
        return

    desconto_valor = _parse_valor_input(desconto_texto, 0.0)
    motivo_valor = motivo_texto.strip()

    if desconto_valor > 0 and not motivo_valor:
        st.error("Informe o motivo do desconto adicional.")
        return

    try:
        supabase.table("compras").update({
            "compras_desconto_adicional": desconto_valor,
            "compras_motivo_desconto": motivo_valor or None,
        }).eq("id", compra["id"]).execute()
        st.success("Desconto adicional atualizado com sucesso!")
        st.rerun()
    except Exception as e:
        st.error(f"Erro ao salvar desconto adicional: {e}")



@_dialog("📎 Arquivos da compra", width="large")
def _dialog_arquivos_compra(compra: dict):
    """Gerencia XML/PDF vinculados a uma compra."""
    chave_wrap_upload = f"upload_arquivo_wrap_{compra['id']}"

    # CSS único, escopado só ao container deste botão (mesma técnica usada
    # no botão de preço de venda do estoque) — mais confiável do que torcer
    # por data-testid, que foi o que deixava o botão "voltando" pro vermelho
    # padrão do Streamlit antes.
    st.markdown(
        f"""
        <style>
        div[class*="st-key-{chave_wrap_upload}"] button[kind*="primary"] {{
            background-color: #2563EB !important;
            border-color: #2563EB !important;
            color: #FFFFFF !important;
        }}
        div[class*="st-key-{chave_wrap_upload}"] button[kind*="primary"]:hover {{
            background-color: #1D4ED8 !important;
            border-color: #1D4ED8 !important;
        }}
        div[class*="st-key-{chave_wrap_upload}"] button[kind*="primary"]:active {{
            background-color: #1E40AF !important;
            border-color: #1E40AF !important;
        }}
        div[class*="st-key-baixar_wrap_"] button {{
            color: #60a5fa !important;
            border-color: rgba(96,165,250,.35) !important;
        }}
        div[class*="st-key-baixar_wrap_"] button:hover {{
            background-color: rgba(59,130,246,.12) !important;
            border-color: #3b82f6 !important;
        }}
        div[class*="st-key-excluir_wrap_"] button {{
            color: #f87171 !important;
            border-color: rgba(248,113,113,.35) !important;
        }}
        div[class*="st-key-excluir_wrap_"] button:hover {{
            background-color: rgba(239,68,68,.12) !important;
            border-color: #ef4444 !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.caption(
        f"NF nº {compra.get('numero_nf') or '-'} — "
        f"Compra #{compra.get('id')}"
    )

    with st.container(border=True):
        st.markdown("**📤 Adicionar arquivo**")

        with st.container(key=chave_wrap_upload):
            with st.form(f"form_arquivo_compra_{compra['id']}", border=False):
                arquivo = st.file_uploader(
                    "Selecione o arquivo",
                    type=["xml", "pdf"],
                    key=f"upload_arquivo_compra_{compra['id']}",
                    help="Nesta versão são aceitos somente XML e PDF.",
                )

                col_tipo, col_botao = st.columns([1, 2], vertical_alignment="bottom")
                with col_tipo:
                    tipo = st.selectbox(
                        "Tipo do arquivo",
                        ["PDF", "XML"],
                        key=f"tipo_arquivo_compra_{compra['id']}",
                    )
                with col_botao:
                    enviar = st.form_submit_button(
                        "⬆️ Enviar arquivo",
                        type="primary",
                        use_container_width=True,
                    )

    if enviar:
        if arquivo is None:
            st.error("Selecione um arquivo.")
        else:
            extensao = arquivo.name.lower().rsplit(".", 1)[-1]
            if extensao != tipo.lower():
                st.error(
                    f"O tipo selecionado é {tipo}, mas o arquivo "
                    f"possui extensão .{extensao}."
                )
            else:
                try:
                    nome_padrao = nome_arquivo_compra(
                        compra.get("numero_nf"),
                        tipo,
                    )
                    registro = upload_arquivo_compra(
                        compra_id=compra["id"],
                        arquivo=arquivo,
                        nome_arquivo=nome_padrao,
                        tipo_arquivo=tipo,
                        mime_type=arquivo.type,
                    )
                    st.success(
                        f"Arquivo '{registro['nome_arquivo']}' enviado com sucesso."
                    )
                    st.rerun()
                except Exception as e:
                    st.error(f"Erro ao enviar arquivo: {e}")

    st.divider()

    try:
        arquivos = listar_arquivos_compra(compra["id"])
    except Exception as e:
        st.error(f"Erro ao carregar arquivos: {e}")
        return

    st.markdown(f"**📁 Arquivos na nuvem ({len(arquivos)})**")

    if arquivos:
        # Lista dos arquivos dentro de uma área visualmente separada.
        with st.container(border=True):
            for arquivo in arquivos:
                col_tipo, col_nome, col_data, col_acoes = st.columns(
                    [0.8, 3.2, 1.4, 1.8],
                    vertical_alignment="center",
                )

                tipo_arquivo = arquivo.get("tipo_arquivo") or "-"
                nome = arquivo.get("nome_arquivo") or "-"
                criado_em = arquivo.get("criado_em") or ""

                col_tipo.markdown(_badge_tipo_arquivo(tipo_arquivo), unsafe_allow_html=True)
                col_nome.write(nome)

                if criado_em:
                    try:
                        dt = datetime.fromisoformat(criado_em.replace("Z", "+00:00"))
                        # Supabase/Postgres grava o timestamp em UTC; exibe no horário de Brasília.
                        dt_br = dt.astimezone(ZoneInfo("America/Sao_Paulo"))
                        col_data.write(dt_br.strftime("%d/%m/%Y %H:%M"))
                    except (TypeError, ValueError):
                        col_data.write("-")
                else:
                    col_data.write("-")

                with col_acoes:
                    col_baixar, col_excluir = st.columns(2)

                    try:
                        conteudo_arquivo, nome_download, mime_download = baixar_arquivo_compra(
                            arquivo
                        )
                    except Exception:
                        conteudo_arquivo = None
                        nome_download = arquivo.get("nome_arquivo") or "arquivo"
                        mime_download = arquivo.get("mime_type") or "application/octet-stream"
                        col_baixar.error("Erro")

                    if conteudo_arquivo is not None:
                        with col_baixar.container(key=f"baixar_wrap_{arquivo['id']}"):
                            st.download_button(
                                "",
                                data=conteudo_arquivo,
                                file_name=nome_download,
                                mime=mime_download,
                                help="Baixar arquivo",
                                use_container_width=True,
                                key=f"baixar_arquivo_compra_{arquivo['id']}",
                                icon=":material/download:",
                            )

                    with col_excluir.container(key=f"excluir_wrap_{arquivo['id']}"):
                        if st.button(
                            "",
                            key=f"excluir_arquivo_compra_{arquivo['id']}",
                            help="Excluir arquivo da nuvem",
                            use_container_width=True,
                            icon=":material/delete:",
                        ):
                            try:
                                excluir_arquivo_compra(arquivo)
                                st.success("Arquivo excluído.")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Erro ao excluir arquivo: {e}")
    else:
        with st.container(border=True):
            st.info("Nenhum arquivo anexado a esta compra.")


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
                " valor_total, chave_acesso, compras_desconto_adicional,"
                " compras_motivo_desconto"
            )
            .order("data_emissao", desc=True)
            .execute()
        )
        compras = response.data or []

        # 2. Identifica quais compras possuem arquivos anexados.
        # Se a consulta falhar, a listagem principal continua funcionando.
        compras_com_arquivos: set[int] = set()
        compra_ids_arquivos = [c["id"] for c in compras]
        if compra_ids_arquivos:
            try:
                arquivos_resp = (
                    supabase.table("compras_arquivos")
                    .select("compra_id")
                    .in_("compra_id", compra_ids_arquivos)
                    .execute()
                )
                compras_com_arquivos = {
                    int(row["compra_id"])
                    for row in (arquivos_resp.data or [])
                    if row.get("compra_id") is not None
                }
            except Exception:
                pass

        # 3. Contagem de itens por compra numa única consulta a compras_itens
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

        # 4. Cálculo dos Cards (KPIs)
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

        # 5. Exibição dos Cards no Topo (KPIs)
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

        # 6. Paginação da listagem
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

        # 7. Tabela de Compras
        st.markdown(
            """
            <style>
            div[class*="st-key-ver_itens_compra_"] div[data-testid="stButton"],
            div[class*="st-key-editar_desconto_compra_"] div[data-testid="stButton"],
            div[class*="st-key-arquivos_compra_"] div[data-testid="stButton"] {
                width: 100% !important;
            }

            div[class*="st-key-ver_itens_compra_"] button,
            div[class*="st-key-editar_desconto_compra_"] button,
            div[class*="st-key-arquivos_compra_"] button {
                padding: 0.2rem 0 !important;
                min-height: 32px !important;
                height: 32px !important;
                min-width: 0 !important;
                width: 100% !important;
                display: flex !important;
                align-items: center !important;
                justify-content: center !important;
                box-sizing: border-box !important;
            }

            div[class*="st-key-ver_itens_compra_"] button p,
            div[class*="st-key-editar_desconto_compra_"] button p,
            div[class*="st-key-arquivos_compra_"] button p,
            div[class*="st-key-ver_itens_compra_"] button span,
            div[class*="st-key-editar_desconto_compra_"] button span,
            div[class*="st-key-arquivos_compra_"] button span {
                font-size: 0.95rem !important;
                line-height: 1 !important;
                margin: 0 !important;
                padding: 0 !important;
                overflow: visible !important;
                text-overflow: clip !important;
                white-space: nowrap !important;
                text-align: center !important;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )

        col_pesos = [1.3, 1.1, 1.3, 1.2, 1.3, 1.4, 0.6, 1.8]
        with st.container(border=True):
            c_nf, c_dt, c_prod, c_desc, c_tot, c_desc_adic, c_itens, c_acao = st.columns(
                col_pesos
            )

            c_nf.markdown("**Número NF**")
            c_dt.markdown("**Data Compra**")
            c_prod.markdown("**Valor Produto**")
            c_desc.markdown("**Desconto (-)**")
            c_tot.markdown("**Valor Total**")
            c_desc_adic.markdown("**Desconto Adicional**")
            c_itens.markdown("**Itens**")
            c_acao.markdown("**Ações**")

            st.divider()

            for item in compras_pagina:
                col_nf, col_dt, col_prod, col_desc, col_tot, col_desc_adic, col_itens, col_acao = (
                    st.columns(col_pesos)
                )

                desconto_adicional = float(item.get("compras_desconto_adicional") or 0)
                motivo_desconto = (item.get("compras_motivo_desconto") or "").strip()
                tem_desconto_adicional = desconto_adicional > 0
                chave_acesso = item.get("chave_acesso") or "-"

                # A chave de acesso (antes uma coluna própria) fica disponível
                # ao passar o mouse, via tooltip nativo do navegador. O
                # indicador de desconto adicional fica só na própria coluna
                # "Desconto Adicional" (badge laranja).
                rotulo_nf = item.get("numero_nf") or "-"
                tem_arquivos = item["id"] in compras_com_arquivos
                indicador_arquivos = _badge_clipe_arquivos() if tem_arquivos else ""
                tooltip_nf = f"Chave de acesso: {chave_acesso}"
                if tem_desconto_adicional:
                    tooltip_nf += f" | Desconto adicional: {motivo_desconto or 'motivo não informado'}"
                tooltip_nf_html = tooltip_nf.replace('"', "&quot;")
                col_nf.markdown(
                    f'<span title="{tooltip_nf_html}" style="cursor: help;">'
                    f'{rotulo_nf}{indicador_arquivos}</span>',
                    unsafe_allow_html=True,
                )

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

                col_prod.write(_fmt_numero(v_prod))
                col_desc.write(_fmt_numero(v_desc))
                col_tot.write(_fmt_numero(v_tot))

                with col_desc_adic:
                    if tem_desconto_adicional:
                        motivo_tooltip = motivo_desconto or "Motivo não informado"
                        # Escapa aspas para não quebrar o atributo title do HTML.
                        motivo_html = motivo_tooltip.replace('"', "&quot;")
                        st.markdown(
                            f'<span title="{motivo_html}" '
                            'style="color:#ff8a3d; font-weight:600; cursor: help;">'
                            f'🏷️ {_fmt_numero(desconto_adicional)}</span>',
                            unsafe_allow_html=True,
                        )
                    else:
                        st.write("-")

                col_itens.write(str(qtd_itens_por_compra.get(item["id"], 0)))

                with col_acao:
                    col_ver, col_editar, col_arquivos = st.columns(
                        3, gap="small", vertical_alignment="center"
                    )
                    if col_ver.button(
                        "🔎",
                        key=f"ver_itens_compra_{item['id']}",
                        help="Ver itens da compra",
                        use_container_width=True,
                    ):
                        _dialog_itens_compra(item)
                    if col_editar.button(
                        "✏️",
                        key=f"editar_desconto_compra_{item['id']}",
                        help="Editar desconto adicional",
                        use_container_width=True,
                    ):
                        _dialog_editar_desconto(item)
                    if col_arquivos.button(
                        "📎",
                        key=f"arquivos_compra_{item['id']}",
                        help="Arquivos da compra",
                        use_container_width=True,
                    ):
                        _dialog_arquivos_compra(item)

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

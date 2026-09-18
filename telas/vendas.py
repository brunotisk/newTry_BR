import streamlit as st
import tempfile
import hashlib
from io import BytesIO
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
    gerar_planilha_modelo_vendas,
    validar_planilha_vendas,
    importar_linhas_validadas,
    _normalizar_nome,
)
from telas.cadastros_auxiliares import tela_cadastros_auxiliares
from componentes.campo_cliente import campo_cliente
from componentes.campo_mascarado import campo_mascarado
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


def _gerar_excel_linhas_nao_importadas(linhas: list[dict]) -> bytes:
    """Gera o mesmo formato da planilha de entrada, acrescentando Motivo."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    wb = Workbook()
    ws = wb.active
    ws.title = "Linhas não importadas"
    cabecalhos = [
        "Canal", "Código Interno", "Quantidade", "Valor Lista", "Desconto",
        "Valor Final", "Forma Pagamento", "Status", "Feira", "data_venda",
        "Nome Cliente", "Observação", "Motivo",
    ]
    fill = PatternFill(fill_type="solid", fgColor="1F4E78")
    font = Font(color="FFFFFF", bold=True)
    for col, titulo in enumerate(cabecalhos, 1):
        c = ws.cell(1, col, titulo)
        c.fill = fill
        c.font = font
        c.alignment = Alignment(horizontal="center")

    linha_excel = 2
    for item in linhas:
        motivos = item.get("motivos_nao_importar") or []
        if not motivos:
            continue
        valores = [
            item.get("canal_venda", ""), item.get("codigo_interno", ""), item.get("quantidade"),
            item.get("valor_lista", 0), item.get("valor_desconto", 0), item.get("valor_final", 0),
            item.get("forma_pagamento", ""), item.get("status", ""), item.get("feira", ""),
            item.get("data_venda", ""), item.get("cliente", ""), item.get("observacao", ""),
            " / ".join(motivos),
        ]
        for col, valor in enumerate(valores, 1):
            ws.cell(linha_excel, col, valor)
        ws.cell(linha_excel, 2).number_format = "@"
        for col in (4, 5, 6):
            ws.cell(linha_excel, col).number_format = 'R$ #,##0.00'
        ws.cell(linha_excel, 3).number_format = '0.##'
        linha_excel += 1

    larguras = [18, 18, 12, 15, 15, 16, 24, 16, 24, 15, 30, 35, 55]
    for i, largura in enumerate(larguras, 1):
        ws.column_dimensions[chr(64+i) if i <= 26 else get_column_letter(i)].width = largura
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:M{max(2, linha_excel-1)}"
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()

def _estilo_validacao_vendas(resultado: dict):
    """Cria uma tabela visual: vermelho para problemas críticos, amarelo para cadastros a corrigir e verde para clientes novos."""
    import pandas as pd

    linhas = resultado.get("linhas", [])
    dados = []
    for item in linhas:
        dados.append({
            "Linha": item["linha"],
            "Canal": item["canal_venda"],
            "Código Interno": item["codigo_interno"],
            "Descrição": item["descricao_produto"],
            "Qtd": item["quantidade"],
            "Forma Pagamento": item["forma_pagamento"],
            "Status": item["status"],
            "Feira": item["feira"],
            "Data": item["data_venda"],
            "Cliente": item["cliente"],
            "Situação": {
                "PRODUTO_NAO_CADASTRADO": "Produto não cadastrado",
                "ESTOQUE_INSUFICIENTE": "Estoque insuficiente",
                "ERRO": "Corrigir cadastro",
                "DUPLICADA": "Já existe",
                "CLIENTE_NOVO": "Cliente será criado",
                "OK": "OK",
            }.get(item["status_linha"], item["status_linha"]),
        })
    df = pd.DataFrame(dados)
    if df.empty:
        return df

    def aplicar_cor(row):
        # O Styler espera uma Series indexada pelos nomes das colunas. Usar
        # posições inteiras aqui pode gerar TypeError em versões recentes do
        # pandas ao renderizar pelo Streamlit.
        estilos = pd.Series("", index=df.columns, dtype="object")
        item = linhas[row.name]
        estilo_critico = "color: #ff0000; font-weight: bold"
        estilo_cadastro = "color: #f0b400; font-weight: bold"
        estilo_cliente_novo = "color: #00a000; font-weight: bold"
        mapa = {
            "canal_venda": "Canal",
            "codigo_interno": "Código Interno",
            "quantidade": "Qtd",
            "forma_pagamento": "Forma Pagamento",
            "status": "Status",
            "feira": "Feira",
            "data_venda": "Data",
        }
        if item.get("status_linha") in {"ERRO", "PRODUTO_NAO_CADASTRADO", "ESTOQUE_INSUFICIENTE"}:
            for campo in item.get("campos_erro", []):
                coluna = mapa.get(campo)
                if coluna:
                    estilos.loc[coluna] = estilo_cadastro
            if item.get("status_linha") == "PRODUTO_NAO_CADASTRADO":
                estilos.loc["Código Interno"] = estilo_critico
                estilos.loc["Situação"] = estilo_critico
            if item.get("status_linha") == "ESTOQUE_INSUFICIENTE":
                estilos.loc["Qtd"] = estilo_critico
                estilos.loc["Situação"] = estilo_critico
            if item.get("status_linha") == "DUPLICADA":
                estilos.loc["Situação"] = estilo_critico
        if item.get("status_linha") == "ERRO":
            estilos.loc["Situação"] = estilo_cadastro
        if item.get("cliente_novo"):
            estilos.loc["Cliente"] = estilo_cliente_novo
        return estilos

    return df.style.apply(aplicar_cor, axis=1)


def _aplicar_correcao_validacao(indice: int, campo: str, valor):
    """Aplica uma correção escolhida na tela e atualiza os IDs oficiais."""
    linhas = st.session_state.get("vendas_importacao_resultado", {}).get("linhas", [])
    resultado = st.session_state.get("vendas_importacao_resultado")
    if not resultado or indice >= len(linhas):
        return
    item = linhas[indice]
    cadastros = resultado["cadastros"]

    item[campo] = valor
    st.session_state.setdefault("vendas_importacao_correcoes", {})[(indice, campo)] = valor
    mapas = {
        "canal_venda": ("canais", "nome", "canal_id"),
        "forma_pagamento": ("formas", "descricao", "forma_pagamento_id"),
        "status": ("status", "nome", "status_id"),
        "feira": ("feiras", "nome_feira", "detalhe_feira_id"),
    }
    if campo in mapas:
        lista, chave, id_campo = mapas[campo]
        alvo = " ".join(str(valor or "").casefold().split())
        encontrado = next((x for x in cadastros[lista] if " ".join(str(x.get(chave) or "").casefold().split()) == alvo), None)
        item[id_campo] = encontrado["id"] if encontrado else None

    # Recalcula os erros de cadastro da própria linha. Quantidade, data,
    # produto e estoque não são alterados por dropdown.
    mensagens = []
    campos = set(item.get("campos_erro", []))
    regras = {
        "canal_venda": ("canais", "nome", "canal_id", "Canal de venda"),
        "forma_pagamento": ("formas", "descricao", "forma_pagamento_id", "Forma de pagamento"),
        "status": ("status", "nome", "status_id", "Status"),
    }
    for campo_regra, (lista, chave, id_campo, rotulo) in regras.items():
        valor_atual = _normalizar_nome(item.get(campo_regra))
        encontrado = next((x for x in cadastros[lista] if _normalizar_nome(x.get(chave)) == valor_atual), None)
        if not valor_atual:
            mensagens.append(f"{rotulo} vazio")
            campos.add(campo_regra)
        elif encontrado is None:
            mensagens.append(f"{rotulo} '{item.get(campo_regra)}' não cadastrado")
            campos.add(campo_regra)
        else:
            campos.discard(campo_regra)
            item[id_campo] = encontrado["id"]

    canal_eh_feira = _normalizar_nome(item.get("canal_venda")) == "feira"
    feira_valor = _normalizar_nome(item.get("feira"))
    feira_encontrada = next((x for x in cadastros["feiras"] if _normalizar_nome(x.get("nome_feira")) == feira_valor), None)
    if canal_eh_feira:
        if not feira_valor:
            mensagens.append("Canal Feira exige uma feira")
            campos.add("feira")
        elif feira_encontrada is None:
            mensagens.append(f"Feira '{item.get('feira')}' não cadastrada")
            campos.add("feira")
        else:
            campos.discard("feira")
            item["detalhe_feira_id"] = feira_encontrada["id"]
    else:
        campos.discard("feira")
        item["feira"] = ""
        item["detalhe_feira_id"] = None

    # Preserva mensagens de validações que não são de cadastro.
    outras = [e for e in item.get("erros", []) if not any(e.lower().startswith(prefix) for prefix in ("canal", "forma de pagamento", "status", "feira"))]
    item["erros"] = outras + mensagens
    item["campos_erro"] = sorted(campos)
    if item.get("status_linha") == "ERRO" and not item["erros"]:
        item["status_linha"] = "CLIENTE_NOVO" if item.get("cliente_novo") else "OK"


def _tela_validacao_importacao():
    resultado = st.session_state.get("vendas_importacao_resultado")
    if not resultado:
        return False

    st.markdown("### 🔎 Conferência antes da importação")
    st.caption("Esta etapa consulta o banco atual. Somente as linhas liberadas serão gravadas ao prosseguir.")

    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("Linhas", resultado["total"])
    with col2:
        st.metric("Cadastros a corrigir", resultado["erros"])
    with col3:
        st.metric("Produtos não cadastrados", resultado["produtos_nao_cadastrados"])
    with col4:
        st.metric("Estoque insuficiente", resultado.get("estoque_insuficiente", 0))
    with col5:
        st.metric("Clientes novos", resultado["clientes_novos"])

    # Legenda consolidada em um único banner, explicando as cores e
    # deixando explícito o comportamento de cada tipo de ocorrência.
    st.markdown(
        """
        <div style="padding: 14px 16px; border-radius: 8px; background: #19324b; margin: 0 0 12px 0;">
            <div style="display:flex; align-items:flex-start; gap:10px; margin-bottom:10px; line-height:1.45;">
                <span style="font-size:18px; line-height:1.45; width:18px; flex:0 0 18px; text-align:center;">🔴</span>
                <span><strong style="color:#4da3ff;">Vermelho — Crítico:</strong> produto não cadastrado ou estoque insuficiente. A linha não será importada.</span>
            </div>
            <div style="display:flex; align-items:flex-start; gap:10px; margin-bottom:10px; line-height:1.45;">
                <span style="font-size:18px; line-height:1.45; width:18px; flex:0 0 18px; text-align:center;">🟡</span>
                <span><strong style="color:#4da3ff;">Amarelo — Cadastro a corrigir:</strong> canal, forma de pagamento, status ou feira. Corrija usando o cadastro oficial e clique em <strong>Validar novamente</strong>.</span>
            </div>
            <div style="display:flex; align-items:flex-start; gap:10px; line-height:1.45;">
                <span style="font-size:18px; line-height:1.45; width:18px; flex:0 0 18px; text-align:center;">🟢</span>
                <span><strong style="color:#4da3ff;">Verde — Cliente novo:</strong> o cliente não foi encontrado no cadastro atual e será criado durante a importação.</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if resultado.get("nao_importaveis"):
        excel_nao_importadas = _gerar_excel_linhas_nao_importadas(resultado["linhas"])
        st.download_button(
            "📥 Baixar linhas não importadas",
            data=excel_nao_importadas,
            file_name="linhas_nao_importadas.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="download_linhas_nao_importadas",
        )

    tabela = _estilo_validacao_vendas(resultado)
    st.dataframe(tabela, use_container_width=True, hide_index=True)

    problemas = [(i, x) for i, x in enumerate(resultado["linhas"]) if x.get("status_linha") == "ERRO"]
    if problemas:
        st.markdown("#### 🛠️ Corrigir cadastros")
        st.caption("Use os dropdowns oficiais para corrigir os campos destacados. Produtos inexistentes e estoque insuficiente não podem ser corrigidos nesta tela.")
        for indice, item in problemas:
            with st.container(border=True):
                st.markdown(f"**Linha {item['linha']}** — {item['descricao_produto'] or item['codigo_interno']}")
                colunas = []
                if "canal_venda" in item.get("campos_erro", []): colunas.append("canal_venda")
                if "forma_pagamento" in item.get("campos_erro", []): colunas.append("forma_pagamento")
                if "status" in item.get("campos_erro", []): colunas.append("status")
                if "feira" in item.get("campos_erro", []): colunas.append("feira")
                if colunas:
                    cols = st.columns(min(3, len(colunas)))
                    for pos, campo in enumerate(colunas):
                        with cols[pos % len(cols)]:
                            if campo == "canal_venda":
                                opcoes = [x["nome"] for x in resultado["cadastros"]["canais"]]
                            elif campo == "forma_pagamento":
                                opcoes = [x["descricao"] for x in resultado["cadastros"]["formas"]]
                            elif campo == "status":
                                opcoes = [x["nome"] for x in resultado["cadastros"]["status"]]
                            else:
                                opcoes = [x["nome_feira"] for x in resultado["cadastros"]["feiras"]]
                            labels = {"canal_venda":"Canal", "forma_pagamento":"Forma de Pagamento", "status":"Status", "feira":"Feira"}
                            atual = item.get(campo) if item.get(campo) in opcoes else None
                            novo = st.selectbox(labels[campo], opcoes, index=opcoes.index(atual) if atual else None, placeholder="Selecione...", key=f"corr_{indice}_{campo}")
                            if novo and novo != item.get(campo):
                                _aplicar_correcao_validacao(indice, campo, novo)
                if item.get("erros"):
                    st.caption(" | ".join(item["erros"]))

        if st.button("🔄 Validar novamente", key="validar_importacao_novamente"):
            # Revalida contra o BD, preservando as correções feitas nos dropdowns.
            correcoes = st.session_state.get("vendas_importacao_correcoes", {})
            resultado_novo = validar_planilha_vendas(st.session_state["vendas_importacao_tmp_path"], supabase)
            st.session_state["vendas_importacao_resultado"] = resultado_novo
            # Reaplica as correções escolhidas na tela, inclusive os IDs
            # oficiais correspondentes.
            for (indice, campo), valor in correcoes.items():
                if indice < len(resultado_novo["linhas"]):
                    _aplicar_correcao_validacao(indice, campo, valor)
            st.rerun()

    # Calcula os bloqueios a partir do estado atual das linhas, e não apenas
    # do contador produzido na primeira validação. Isso permite que uma
    # correção feita pelo dropdown libere o botão imediatamente, sem depender
    # de um contador antigo em session_state.
    bloqueios = sum(
        1
        for x in resultado.get("linhas", [])
        if x.get("status_linha") == "ERRO" and bool(x.get("erros"))
    )
    col_cancelar, col_prosseguir = st.columns(2)
    with col_cancelar:
        if st.button("Cancelar", key="cancelar_validacao_importacao"):
            for chave in ("vendas_importacao_resultado", "vendas_importacao_tmp_path", "vendas_importacao_hash", "vendas_importacao_correcoes", "vendas_importacao_pronta"):
                st.session_state.pop(chave, None)
            st.rerun()
    with col_prosseguir:
        if st.button("Prosseguir com importação →", type="primary", disabled=bloqueios > 0, key="prosseguir_importacao"):
            with st.spinner("Importando as linhas válidas e atualizando o estoque..."):
                resultado_importacao = importar_linhas_validadas(resultado["linhas"], supabase)
            if resultado_importacao["erros"]:
                st.error("A importação terminou com ocorrências:\n" + "\n".join(resultado_importacao["erros"]))
            else:
                st.success(
                    f"Importação concluída: {resultado_importacao['importadas']} venda(s) importada(s) e "
                    f"{resultado_importacao['clientes_criados']} cliente(s) criado(s)."
                )
            for chave in ("vendas_importacao_resultado", "vendas_importacao_tmp_path", "vendas_importacao_hash", "vendas_importacao_correcoes", "vendas_importacao_pronta"):
                st.session_state.pop(chave, None)
            st.rerun()

    if bloqueios:
        st.warning(
            f"Ainda existem {bloqueios} linha(s) com cadastro para corrigir. "
            "Corrija pelos dropdowns acima e depois clique em Validar novamente. "
            "Linhas em vermelho (produto não cadastrado ou estoque insuficiente) são críticas e serão excluídas do lote, "
            "mas não impedem o prosseguimento."
        )
    return True

def _secao_importar():
    st.subheader("📥 Importar vendas de planilha Excel")

    # Se já existe uma validação em andamento, a tela intermediária assume o
    # controle. Nenhuma operação de gravação é feita nesta etapa.
    if st.session_state.get("vendas_importacao_resultado"):
        _tela_validacao_importacao()
        return

    st.caption(
        "Baixe o modelo para preencher suas vendas. O arquivo já vem com os "
        "cadastros atuais, listas suspensas e validações."
    )

    try:
        modelo_excel = gerar_planilha_modelo_vendas(supabase)
        st.download_button(
            "📥 Baixar planilha modelo",
            data=modelo_excel,
            file_name="modelo_importacao_vendas.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            help="Gera um modelo atualizado com canais, formas de pagamento, status, clientes, feiras e produtos/estoque atuais.",
            key="download_modelo_vendas",
        )
    except Exception as e:
        st.warning(f"Não foi possível gerar a planilha modelo agora: {e}")

    st.caption(
        "Depois de preencher a aba 'Vendas', salve o arquivo e envie-o abaixo. "
        "Clientes novos podem ser digitados; os demais cadastros devem existir no sistema."
    )

    arquivo = st.file_uploader("Selecione o arquivo .xlsx", type=["xlsx"], key="upload_vendas")
    if arquivo is None:
        return

    arquivo_bytes = arquivo.getvalue()
    assinatura = hashlib.sha256(arquivo_bytes).hexdigest()
    if st.session_state.get("vendas_importacao_hash") != assinatura:
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp.write(arquivo_bytes)
            tmp_path = tmp.name
        try:
            with st.spinner("Consultando os cadastros atuais e validando a planilha..."):
                resultado = validar_planilha_vendas(tmp_path, supabase)
        except Exception as e:
            st.error(f"Não foi possível validar a planilha: {e}")
            return
        st.session_state["vendas_importacao_hash"] = assinatura
        st.session_state["vendas_importacao_tmp_path"] = tmp_path
        st.session_state["vendas_importacao_resultado"] = resultado
        st.session_state.pop("vendas_importacao_pronta", None)
        st.rerun()

    _tela_validacao_importacao()


@_dialog("💰 Venda", width="large")
def _dialog_editar_venda(
    venda: dict | None,
    canais_disponiveis: list,
    status_disponiveis: list,
    formas_pagamento_disponiveis: list,
    feiras_disponiveis: list,
    clientes_disponiveis: list,
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

    # Todos os campos do popup ficam dentro de uma única borda.
    # A primeira linha permanece fora do st.form para permitir que o canal
    # habilite/desabilite a Feira imediatamente.
    with st.container(border=True):
    # Linha 1 fica fora do formulário para que a escolha do canal provoque
    # atualização imediata e habilite/desabilite o campo Feira.
    # Linha 1: Produto (1/2) | Canal de venda (1/4) | Feira (1/4)
        col_produto, col_canal, col_feira = st.columns([2, 1, 1])

        with col_produto:
            if nova_venda:
                if produtos_opcoes:
                    rotulos_produtos = list(produtos_opcoes.keys())
                    produto_rotulo = st.selectbox(
                        "Produto",
                        rotulos_produtos,
                        index=None,
                        placeholder="Selecione o produto...",
                        key="venda_produto_nova",
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
                    key="venda_canal_nome_nova",
                ) if nomes_canais else None
            else:
                st.text_input("Canal de venda", value=canal_atual_nome, disabled=True)
                canal_nome = canal_atual_nome

        # A feira fica sempre na primeira linha e só é habilitada quando
        # o canal selecionado for exatamente "Feira".
        canal_eh_feira = (canal_nome or "").strip().casefold() == "feira"
        nomes_feiras = [f["nome_feira"] for f in feiras_disponiveis]
        feira_atual_nome = feira_atual.get("nome_feira") or ""

        with col_feira:
            if nova_venda and not canal_eh_feira:
                st.selectbox(
                    "Feira",
                    ["Selecione o canal Feira"],
                    index=0,
                    disabled=True,
                    key="venda_feira_desabilitada_nova",
                )
                feira_nome = None
            elif not nova_venda and not canal_eh_feira:
                st.selectbox(
                    "Feira",
                    ["Não se aplica"],
                    index=0,
                    disabled=True,
                    key=f"venda_feira_desabilitada_{venda.get('id')}",
                )
                feira_nome = None
            else:
                nomes_feiras_com_vazio = [""] + nomes_feiras
                indice_feira = (
                    nomes_feiras_com_vazio.index(feira_atual_nome)
                    if feira_atual_nome in nomes_feiras_com_vazio else 0
                )
                feira_nome = st.selectbox(
                    "Feira",
                    nomes_feiras_com_vazio,
                    index=indice_feira,
                    placeholder="Selecione a feira...",
                    key=f"venda_feira_{'nova' if nova_venda else venda.get('id')}",
                )


        with st.form(form_key, border=False):
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
                valor_lista = st.number_input(
                    "Valor lista",
                    min_value=0.0,
                    step=0.01,
                    format="%.2f",
                    value=float(venda.get("valor_lista") or 0),
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
                valor_final = st.number_input(
                    "Valor final",
                    min_value=0.0,
                    step=0.01,
                    format="%.2f",
                    value=float(venda.get("valor_final") or 0),
                )

            # Linha 3: Cliente (1/2) | Data da venda (1/4) | Forma de pagamento (1/4)
            col_cliente, col_data, col_forma = st.columns([2, 1, 1])

            with col_cliente:
                nomes_clientes = [c.get("nome") for c in clientes_disponiveis if c.get("nome")]
                cliente = campo_cliente(
                    "Cliente",
                    value=venda.get("cliente") or "",
                    opcoes=nomes_clientes,
                    placeholder="Digite ou selecione o cliente...",
                    key=f"venda_cliente_{'nova' if nova_venda else venda.get('id')}",
                )

            with col_data:
                data_venda_texto = campo_mascarado(
                    "Data da venda",
                    value=data_atual.strftime("%d/%m/%Y"),
                    tipo="data",
                    placeholder="DD/MM/YYYY",
                    key=f"venda_data_mask_{'nova' if nova_venda else venda.get('id')}",
                )

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

            # Status continua obrigatório, mas foi deslocado para uma linha
            # própria para preservar exatamente a distribuição solicitada.
            status_nome = st.selectbox(
                "Status",
                nomes_status,
                index=nomes_status.index(status_atual_nome)
                if status_atual_nome in nomes_status else 0,
            ) if nomes_status else None

            # Espaçamento proposital entre a última linha e os botões.
            st.markdown("<div style='height: 1.25rem;'></div>", unsafe_allow_html=True)

            if nova_venda:
                col_salvar, col_cancelar = st.columns(2)
                salvar = col_salvar.form_submit_button(
                    "💾 Salvar", type="secondary", use_container_width=True
                )
                cancelar = col_cancelar.form_submit_button(
                    "Cancelar", use_container_width=True
                )
                excluir = False
            else:
                col_salvar, col_cancelar, col_excluir = st.columns(3)
                salvar = col_salvar.form_submit_button(
                    "💾 Salvar", type="secondary", use_container_width=True
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

        # Garante que o cliente digitado exista na tabela `clientes`.
        # Para clientes novos, somente `nome` é preenchido.
        cliente_nome = cliente.strip()
        _obter_ou_criar_cliente(sb, cliente_nome)

        try:
            data_venda = datetime.strptime(data_venda_texto.strip(), "%d/%m/%Y").date()
        except (ValueError, TypeError):
            st.error("Data da venda inválida. Use o formato DD/MM/YYYY.")
            return

        dados_novos = {
            "produto_id": produto_selecionado["id"],
            "canal_venda_id": canal_id,
            "status_id": status_id,
            "quantidade": float(quantidade),
            "valor_lista": float(valor_lista),
            "valor_desconto": float(valor_desconto),
            "valor_final": float(valor_final),
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



def _obter_ou_criar_cliente(sb, nome: str) -> int | None:
    """Retorna o id do cliente pelo nome ou cria um cliente mínimo.

    Se o usuário digitar um nome que ainda não existe, somente o campo
    `nome` é preenchido; telefone, canal e datas permanecem nulos.
    """
    nome = (nome or "").strip()
    if not nome:
        return None

    try:
        existentes = (
            sb.table("clientes")
            .select("id, nome")
            .execute()
        ).data or []
    except Exception as e:
        raise RuntimeError(f"Não foi possível consultar os clientes: {e}") from e

    normalizado = nome.casefold()
    for cliente in existentes:
        if (cliente.get("nome") or "").strip().casefold() == normalizado:
            return cliente["id"]

    try:
        resp = sb.table("clientes").insert({"nome": nome}).execute()
        return resp.data[0]["id"] if resp.data else None
    except Exception as e:
        # O índice único lower(trim(nome)) também protege contra corrida
        # entre duas inclusões simultâneas. Se já existir, recupera o registro.
        try:
            existentes = (
                sb.table("clientes")
                .select("id, nome")
                .execute()
            ).data or []
            for cliente in existentes:
                if (cliente.get("nome") or "").strip().casefold() == normalizado:
                    return cliente["id"]
        except Exception:
            pass
        raise RuntimeError(f"Não foi possível cadastrar o cliente: {e}") from e


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

    try:
        clientes = (
            supabase.table("clientes")
            .select("id, nome")
            .order("nome")
            .execute()
        ).data or []
    except Exception:
        clientes = []

    return canais, status, formas, feiras, clientes



def _secao_listagem():
    # ------------------------------------------------------------------
    # 1) Dataset leve com TODAS as vendas (valor_final, data_venda e o nome
    #    do canal), usado para os KPIs fixos (Total/Ano/Mês atual), para o
    #    KPI "Filtros" e para montar as opções do filtro de mês/ano.
    # ------------------------------------------------------------------
    try:
        response_kpi = (
            supabase
            .table("vendas")
            .select("valor_final, data_venda, canais_venda(nome)")
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
            soma += float(v.get("valor_final") or 0)
        return qtd, soma

    qtd_total = len(vendas_kpi)
    soma_total = sum(float(v.get("valor_final") or 0) for v in vendas_kpi)
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
                canais_popup, status_popup, formas_popup, feiras_popup, clientes_popup = _carregar_cadastros_popup()
                _dialog_editar_venda(
                    None,
                    canais_popup,
                    status_popup,
                    formas_popup,
                    feiras_popup,
                    clientes_popup,
                )

    # Reseta a página para 1 sempre que algum filtro mudar
    assinatura_filtros = f"{mes_selecionado}|{canal_selecionado}"
    if st.session_state.get("vendas_assinatura_filtros") != assinatura_filtros:
        st.session_state.pagina_atual_vendas = 1
        st.session_state.vendas_assinatura_filtros = assinatura_filtros

    # ------------------------------------------------------------------
    # 5) Paginação + consulta da página filtrada
    # ------------------------------------------------------------------
    itens_por_pagina = get_itens_por_pagina("vendas", 50)
    if "pagina_atual_vendas" not in st.session_state:
        st.session_state.pagina_atual_vendas = 1

    # Embed do canal como inner join só quando o filtro de canal está ativo,
    # para não excluir vendas sem canal preenchido quando não há filtro.
    canal_embed = "canais_venda!inner(nome)" if canal_selecionado != "Todos" else "canais_venda(nome)"

    def _monta_query():
        query = (
            supabase
            .table("vendas")
            .select(
                "id, produto_id, quantidade, valor_lista, valor_desconto,"
                " valor_final, data_venda, cliente,"
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

    total_paginas = max(1, (total_filtrado + itens_por_pagina - 1) // itens_por_pagina)
    if st.session_state.pagina_atual_vendas > total_paginas:
        st.session_state.pagina_atual_vendas = total_paginas

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

    # Informação no topo, usando o mesmo componente das demais telas.
    render_paginacao(
        "vendas",
        total_filtrado,
        itens_por_pagina=itens_por_pagina,
        mostrar_contagem_superior=True,
        mostrar_contagem_inferior=False,
        permitir_seletor=True,
    )

    with st.container(border=True):
        c_data, c_canal, c_prod, c_qtd, c_total, c_status, c_cliente, c_acoes = st.columns(
            [1.4, 1.3, 2.8, 0.9, 1.4, 1.3, 1.7, 0.8]
        )
        c_data.markdown("**Data**")
        c_canal.markdown("**Canal**")
        c_prod.markdown("**Produto**")
        c_qtd.markdown("**Qtd**")
        c_total.markdown("**Valor Final**")
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
            col_total.write(_fmt_moeda(v.get("valor_final")))
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
                clientes_popup = (
                    supabase.table("clientes")
                    .select("id, nome")
                    .order("nome")
                    .execute()
                ).data or []
                _dialog_editar_venda(
                    v,
                    canais_disponiveis,
                    status_disponiveis,
                    formas_popup,
                    feiras_popup,
                    clientes_popup,
                )

    # Navegação/contagem no rodapé, no mesmo padrão de Compras.
    render_paginacao(
        "vendas",
        total_filtrado,
        itens_por_pagina=itens_por_pagina,
        mostrar_contagem_superior=False,
        mostrar_contagem_inferior=True,
        permitir_seletor=True,
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

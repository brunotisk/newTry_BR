import streamlit as st
import streamlit.components.v1 as components
import tempfile
import hashlib
import html
import uuid
import pandas as pd
from io import BytesIO
from datetime import date, datetime, timedelta
from typing import Optional

from db import supabase, buscar_todos
from servicos.vendas_import import (
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
    PlanilhaJaImportadaError,
    calcular_comissao_venda,
    _normalizar_nome,
)
from telas.cadastros_gerais import tela_cadastros_gerais
from componentes.campo_cliente import campo_cliente
from componentes.campo_mascarado import campo_mascarado
from componentes.contador_quantidade import contador_quantidade
from componentes.busca_produto import busca_produto
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
    """Estilos dos KPIs e do tooltip customizado, seguindo o padrão visual do app."""
    st.markdown(
        """
        <style>
        div[data-testid="stMetricValue"] {
            overflow: visible;
            white-space: normal;
            word-break: break-word;
            font-size: 1.35rem;
            line-height: 1.2;
        }

        .kpi-subtitulo {
            text-align: center;
            font-size: 0.75rem;
            color: #8B8D98;
            height: 18px;
            line-height: 18px;
            margin-bottom: 0.3rem;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }

        .kpi-help {
            position: relative;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 17px;
            height: 17px;
            margin-left: 5px;
            border: 1px solid #5C5F69;
            border-radius: 50%;
            color: #8B8D98;
            font-size: 11px;
            font-weight: 700;
            cursor: help;
            vertical-align: 1px;
        }
        .kpi-help .kpi-tooltip {
            visibility: hidden;
            opacity: 0;
            position: absolute;
            z-index: 1000;
            left: 50%;
            bottom: calc(100% + 8px);
            transform: translateX(-50%);
            min-width: 190px;
            padding: 9px 11px;
            border: 1px solid #3A3D46;
            border-radius: 7px;
            background: #262730;
            color: #FAFAFA;
            box-shadow: 0 4px 14px rgba(0,0,0,.28);
            font-size: 0.78rem;
            font-weight: 400;
            line-height: 1.45;
            text-align: left;
            white-space: nowrap;
            transition: opacity .12s ease;
            pointer-events: none;
        }
        .kpi-help:hover .kpi-tooltip {
            visibility: visible;
            opacity: 1;
        }
        .kpi-help .kpi-tooltip strong {
            color: #FAFAFA;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _kpi_card(
    titulo: str,
    qtd: int,
    valor_final: float,
    comissao: float = 0.0,
    subtitulo: Optional[str] = None,
):
    """Card de KPI. Exibe o líquido e mostra valor bruto/comissão no tooltip."""
    with st.container(border=True):
        tooltip = (
            f"<strong>Valor final:</strong> {html.escape(_fmt_moeda(valor_final))}<br>"
            f"<strong>Comissão:</strong> {html.escape(_fmt_moeda(comissao))}"
        )
        st.markdown(
            f'<div style="text-align:center;font-weight:700;margin-bottom:0.1rem;">'
            f'{html.escape(titulo)}<span class="kpi-help" aria-label="Detalhes da comissão">i'
            f'<span class="kpi-tooltip">{tooltip}</span></span></div>',
            unsafe_allow_html=True,
        )
        sub_texto = html.escape(subtitulo) if subtitulo else "&nbsp;"
        st.markdown(f"<div class='kpi-subtitulo'>{sub_texto}</div>", unsafe_allow_html=True)

        col_qtd, col_valor = st.columns(2)
        with col_qtd:
            st.metric("Qtd. Vendas", qtd)
        with col_valor:
            st.metric("Valor", _fmt_moeda(valor_final - comissao))


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
            try:
                resultado_novo = validar_planilha_vendas(st.session_state["vendas_importacao_tmp_path"], supabase)
            except PlanilhaJaImportadaError as e:
                st.error(f"🚫 {e}")
                for chave in ("vendas_importacao_resultado", "vendas_importacao_tmp_path", "vendas_importacao_hash", "vendas_importacao_correcoes", "vendas_importacao_pronta", "vendas_importacao_nome_arquivo"):
                    st.session_state.pop(chave, None)
                st.rerun()
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
                resultado_importacao = importar_linhas_validadas(
                    resultado["linhas"],
                    supabase,
                    id_planilha=resultado.get("id_planilha"),
                    nome_arquivo=st.session_state.get("vendas_importacao_nome_arquivo"),
                )
            if resultado_importacao["erros"]:
                st.error("A importação terminou com ocorrências:\n" + "\n".join(resultado_importacao["erros"]))
            else:
                st.success(
                    f"Importação concluída: {resultado_importacao['importadas']} venda(s) importada(s) e "
                    f"{resultado_importacao['clientes_criados']} cliente(s) criado(s)."
                )
            for chave in ("vendas_importacao_resultado", "vendas_importacao_tmp_path", "vendas_importacao_hash", "vendas_importacao_correcoes", "vendas_importacao_pronta", "vendas_importacao_nome_arquivo"):
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
        except PlanilhaJaImportadaError as e:
            st.error(f"🚫 {e}")
            return
        except Exception as e:
            st.error(f"Não foi possível validar a planilha: {e}")
            return
        st.session_state["vendas_importacao_hash"] = assinatura
        st.session_state["vendas_importacao_tmp_path"] = tmp_path
        st.session_state["vendas_importacao_resultado"] = resultado
        st.session_state["vendas_importacao_nome_arquivo"] = arquivo.name
        st.session_state.pop("vendas_importacao_pronta", None)
        st.rerun()

    _tela_validacao_importacao()



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


def _resetar_controles_venda(venda, nova_venda, produto_selecionado):
    """Inicializa os controles independentes do popup de venda."""
    chave_venda = "nova" if nova_venda else str(venda.get("id"))
    produto_id = (produto_selecionado or {}).get("id")
    chave_produto = f"{chave_venda}:{produto_id}"

    if (
        st.session_state.get("venda_controles_chave") != chave_produto
        or "venda_desconto_texto" not in st.session_state
    ):
        st.session_state["venda_controles_chave"] = chave_produto
        st.session_state["venda_qtd"] = max(1.0, float(venda.get("quantidade") or 1))
        if nova_venda and produto_selecionado:
            st.session_state["venda_valor_lista"] = float(
                produto_selecionado.get("estoque_preco_venda_sugerida") or 0
            )
        else:
            st.session_state["venda_valor_lista"] = float(venda.get("valor_lista") or 0)
        st.session_state["venda_desconto_texto"] = _fmt_valor_input(
            venda.get("valor_desconto") or 0
        )
        # Valor lista inicia sempre protegido. O usuário pode abrir
        # explicitamente pelo seletor Travado/Aberto.
        st.session_state["venda_valor_lista_aberto"] = False
        st.session_state["venda_valor_lista_input"] = _fmt_valor_input(
            st.session_state["venda_valor_lista"]
        )


def _render_controles_valores_venda():
    """Renderiza quantidade e os valores da venda.

    Quantidade usa o mesmo contador da tela de Ajuste de estoque.
    Valor lista inicia travado e pode ser liberado pelo seletor
    Travado/Aberto. Desconto é editável e Valor final é calculado.
    """

    col_qtd, col_lista, col_desc, col_final = st.columns(4)

    with col_qtd:
        st.markdown("**Quantidade**")
        estado_qtd = contador_quantidade(
            valor=int(max(1.0, float(st.session_state.get("venda_qtd", 1)))),
            min_valor=1,
            bloqueado=True,
            label="",
            key="contador_venda_nova",
        )
        quantidade = max(
            1,
            int(estado_qtd.get("valor", st.session_state.get("venda_qtd", 1))),
        )
        st.session_state["venda_qtd"] = quantidade

    with col_lista:
        # O seletor é discreto e começa sempre em Travado. Antes usava um
        # segmented_control/radio com textos ("🔒 Travado" / "🔓 Aberto")
        # dividindo o espaço com o rótulo — nessa coluna estreita (1/4 do
        # popup) o texto não cabia e cortava. Um toggle compacto (só o
        # ícone + interruptor) resolve tanto o corte quanto o
        # desalinhamento, porque ocupa a mesma altura de uma linha simples,
        # igual às colunas vizinhas.
        col_rotulo, col_bloqueio = st.columns([2, 1], vertical_alignment="center")
        with col_rotulo:
            st.markdown("**Valor lista**")
        with col_bloqueio:
            aberto = st.toggle(
                "🔓",
                value=st.session_state.get("venda_valor_lista_aberto", False),
                key="venda_valor_lista_bloqueio",
                help=(
                    "Travado: usa o valor sugerido do estoque. "
                    "Ative para digitar um valor lista diferente."
                ),
            )
            st.session_state["venda_valor_lista_aberto"] = aberto

        valor_lista_texto = st.text_input(
            "Valor lista",
            key="venda_valor_lista_input",
            disabled=not aberto,
            label_visibility="collapsed",
        )
        valor_lista = _parse_valor_input(
            valor_lista_texto,
            st.session_state.get("venda_valor_lista", 0),
        )
        # Não altere a chave do widget depois que ele foi instanciado.
        # O valor normalizado fica separado do texto exibido no input.
        st.session_state["venda_valor_lista"] = valor_lista

    with col_desc:
        st.markdown("**Desconto**")
        desconto_texto = st.text_input(
            "Desconto",
            key="venda_desconto_texto",
            label_visibility="collapsed",
        )
        valor_desconto = _parse_valor_input(desconto_texto, 0.0)

    with col_final:
        st.markdown("**Valor final**")
        valor_final = (float(quantidade) * float(valor_lista)) - valor_desconto
        st.text_input(
            "Valor final",
            value=_fmt_valor_input(valor_final),
            disabled=True,
            label_visibility="collapsed",
        )

    return (
        float(quantidade),
        float(valor_lista),
        float(valor_desconto),
        float(valor_final),
    )


# ---------------------------------------------------------------------------
# Itens da venda nova: tabela-resumo (somente leitura) + formulário único
# ---------------------------------------------------------------------------
# Estado guardado em st.session_state:
#   venda_itens          lista de itens já adicionados à venda
#   venda_item_editando  uid do item carregado no formulário (None = item novo)
#   vitem_*              widgets e estado do formulário único de item
#
# Todos os botões dessa área usam callbacks (on_click). O callback roda ANTES
# do re-render do popup, então pode alterar à vontade as chaves dos widgets do
# formulário (limpar, carregar um item para edição etc.) e dispensa st.rerun(),
# que fecharia o dialog.

def _limpar_itens_venda_nova():
    """Descarta todos os itens e o estado do formulário de item. Chamado ao
    cancelar, ao salvar com sucesso e ao abrir '➕ Adicionar venda' — assim o
    popup sempre começa sem itens e com o formulário vazio."""
    ss = st.session_state
    for chave in (
        "venda_itens", "venda_item_editando", "venda_msg_falha",
        "venda_header", "venda_header_ok", "venda_header_ref",
        "venda_controles_chave",
    ):
        ss.pop(chave, None)
    for chave in [k for k in ss.keys() if str(k).startswith(("vitem_", "contador_vitem_"))]:
        ss.pop(chave, None)


def _definir_form_item(produto, quantidade, valor_lista, valor_desconto, aberto=False):
    """Carrega valores no formulário único de item. Só deve ser chamada em
    callbacks ou antes de os widgets do formulário serem criados na execução.

    `vitem_gen` entra na key do contador de quantidade: trocar a key força o
    componente a reiniciar com o novo valor (o componente guarda estado
    próprio no front-end e ignoraria uma mudança só no argumento `valor`)."""
    ss = st.session_state
    ss["vitem_gen"] = ss.get("vitem_gen", 0) + 1
    ss["vitem_ref"] = produto["id"] if produto else None
    ss["vitem_qtd"] = max(1, int(quantidade))
    ss["vitem_valor_lista"] = float(valor_lista)
    ss["vitem_valor_lista_input"] = _fmt_valor_input(valor_lista)
    ss["vitem_bloqueio"] = bool(aberto)
    ss["vitem_desconto"] = _fmt_valor_input(valor_desconto)


def _cb_adicionar_item(produtos_opcoes: dict):
    """Botão '➕ Adicionar item' / '✔️ Atualizar item'."""
    ss = st.session_state
    produto = next(
        (p for p in produtos_opcoes.values() if p.get("id") == ss.get("vitem_ref")),
        None,
    )
    if not produto:
        ss["vitem_msg"] = "Selecione o produto."
        return

    quantidade = max(1, int(ss.get("vitem_qtd", 1)))
    valor_lista = _parse_valor_input(
        ss.get("vitem_valor_lista_input"), ss.get("vitem_valor_lista", 0)
    )
    valor_desconto = _parse_valor_input(ss.get("vitem_desconto"), 0.0)
    valor_final = quantidade * valor_lista - valor_desconto

    saldo = produto.get("saldo")
    if saldo is not None and quantidade > saldo:
        ss["vitem_msg"] = (
            f"Quantidade ({quantidade}) maior que o saldo em estoque ({saldo:g})."
        )
        return
    if valor_final < 0:
        ss["vitem_msg"] = "O desconto não pode ser maior que o valor total do item."
        return

    itens = ss.setdefault("venda_itens", [])
    editando = ss.get("venda_item_editando")
    item = {
        "uid": editando or str(uuid.uuid4()),
        "produto": produto,
        "quantidade": float(quantidade),
        "valor_lista": float(valor_lista),
        "valor_desconto": float(valor_desconto),
        "valor_final": float(valor_final),
    }
    posicao = next((i for i, x in enumerate(itens) if x["uid"] == item["uid"]), None)
    if posicao is None:
        itens.append(item)
        ss["vitem_pagina"] = max(1, -(-len(itens) // _ITENS_POR_PAGINA_VENDA))
    else:
        itens[posicao] = item

    # Item gravado na tabela: volta o formulário para o estado "novo item".
    ss["venda_item_editando"] = None
    ss["vitem_ref"] = None
    ss["vitem_busca_produto"] = {"id": None, "termo": "", "buscar_descricao": False, "aberto": False}
    _definir_form_item(None, 1, 0.0, 0.0)


def _cb_editar_item(uid: str, produtos_opcoes: dict):
    """Ícone ✏️ da tabela: carrega o item no formulário único."""
    ss = st.session_state
    item = next((x for x in ss.get("venda_itens", []) if x["uid"] == uid), None)
    if not item:
        return
    if not any(p.get("id") == item["produto"]["id"] for p in produtos_opcoes.values()):
        ss["vitem_msg"] = "Este produto não tem mais saldo em estoque."
        return

    sugerido = float(item["produto"].get("estoque_preco_venda_sugerida") or 0)
    _definir_form_item(
        item["produto"],
        item["quantidade"],
        item["valor_lista"],
        item["valor_desconto"],
        # Se o valor lista do item foi alterado manualmente, já abre destravado.
        aberto=abs(item["valor_lista"] - sugerido) > 0.004,
    )
    ss["vitem_ref"] = item["produto"]["id"]
    ss["venda_item_editando"] = uid
    ss.pop("vitem_msg", None)


def _cb_cancelar_edicao():
    ss = st.session_state
    ss["venda_item_editando"] = None
    ss["vitem_ref"] = None
    ss["vitem_busca_produto"] = {"id": None, "termo": "", "buscar_descricao": False, "aberto": False}
    ss.pop("vitem_msg", None)
    _definir_form_item(None, 1, 0.0, 0.0)


def _cb_remover_item(uid: str):
    ss = st.session_state
    ss["venda_itens"] = [x for x in ss.get("venda_itens", []) if x["uid"] != uid]
    if ss.get("venda_item_editando") == uid:
        _cb_cancelar_edicao()


def _rerun_dialogo() -> bool:
    """Reexecuta só o popup (sem fechá-lo). Retorna False quando a versão do
    Streamlit não suporta st.rerun(scope="fragment"); quando suporta, a
    função não retorna (o rerun interrompe a execução)."""
    try:
        st.rerun(scope="fragment")
    except Exception:
        return False
    return False


# Itens por página na tabela-resumo da venda.
_ITENS_POR_PAGINA_VENDA = 5

# CSS que formata SOMENTE a tabela-resumo (escopo pela key do container,
# classe `st-key-vitem_tabela`). A formatação das células é inline, então a
# tabela continua legível mesmo se o CSS não for aplicado.
_CSS_TABELA_ITENS = """
<style>
.st-key-vitem_tabela,
.st-key-vitem_tabela [data-testid="stVerticalBlock"] { gap: 0 !important; }
.st-key-vitem_tabela [data-testid="stHorizontalBlock"] {
    gap: 0.5rem !important; align-items: center !important;
}
.st-key-vitem_tabela [data-testid="stElementContainer"],
.st-key-vitem_tabela [data-testid="element-container"],
.st-key-vitem_tabela [data-testid="stMarkdownContainer"] { margin: 0 !important; }
.st-key-vitem_tabela [data-testid="stMarkdownContainer"] p { margin: 0 !important; }

/* Linha de títulos: divisória única, de ponta a ponta. */
.st-key-vitem_cab {
    border-bottom: 1px solid rgba(128,128,128,0.45);
    padding: 0.1rem 0 0.35rem 0;
    margin-bottom: 0.15rem;
}
/* Linhas de itens: altura uniforme e divisória suave entre elas. */
[class*="st-key-vitem_linha_"] {
    border-bottom: 1px solid rgba(128,128,128,0.16);
    padding: 0.3rem 0;
}
[class*="st-key-vitem_linha_"]:last-of-type { border-bottom: none; }

/* Botões de ação (editar / remover): compactos e centralizados. */
.st-key-vitem_tabela button {
    min-height: 1.9rem !important; height: 1.9rem !important;
    padding: 0 !important; font-size: 0.85rem !important; line-height: 1 !important;
}

/* Paginação. */
.st-key-vitem_paginacao { padding-top: 0.5rem; }
.st-key-vitem_paginacao [data-testid="stHorizontalBlock"] { align-items: center !important; }
</style>
"""


def _celula_tabela(texto, alinhar="left", cabecalho=False, negrito=False, titulo=None):
    """Célula em uma linha só (sem quebra), com reticências se não couber.
    Altura fixa e conteúdo centralizado na vertical, para alinhar com os
    botões da mesma linha."""
    justificar = {"left": "flex-start", "right": "flex-end", "center": "center"}[alinhar]
    estilo = (
        f"display:flex;align-items:center;justify-content:{justificar};"
        "min-height:1.9rem;white-space:nowrap;overflow:hidden;"
        f"text-align:{alinhar};"
    )
    if cabecalho:
        estilo += (
            "min-height:1.4rem;font-size:0.7rem;font-weight:600;opacity:0.65;"
            "text-transform:uppercase;letter-spacing:0.03em;"
        )
    else:
        estilo += "font-size:0.85rem;"
        if negrito:
            estilo += "font-weight:600;"
    tip = f' title="{html.escape(titulo)}"' if titulo else ""
    # O texto vai em um <span> para a reticência funcionar dentro do flex.
    return (
        f'<div style="{estilo}"{tip}><span style="overflow:hidden;'
        f'text-overflow:ellipsis;white-space:nowrap;">{texto}</span></div>'
    )


def _cb_pagina_itens(delta: int):
    ss = st.session_state
    ss["vitem_pagina"] = max(1, ss.get("vitem_pagina", 1) + delta)


def _container_com_key(key: str, **opcoes):
    try:
        return st.container(key=key, **opcoes)
    except TypeError:  # Streamlit antigo, sem `key` no container
        return st.container(**opcoes)


def _render_tabela_itens(itens: list, produtos_opcoes: dict):
    """Tabela-resumo paginada dos itens já adicionados. A edição é feita pelo
    formulário único, acionado pelo ícone ✏️ no fim da linha."""
    if not itens:
        st.caption(
            "Nenhum item adicionado ainda. Preencha o formulário acima e "
            "clique em **Adicionar item**."
        )
        return

    ss = st.session_state
    editando = ss.get("venda_item_editando")
    larguras = [4.2, 0.8, 1.5, 1.5, 1.6, 0.5, 0.5]
    alinhamento = ["left", "right", "right", "right", "right", "left", "left"]
    titulos = ["Produto", "Qtd", "Valor lista", "Desconto", "Valor final", "", ""]

    # Paginação: ajusta a página atual se itens foram removidos.
    total_paginas = max(1, -(-len(itens) // _ITENS_POR_PAGINA_VENDA))
    pagina = min(max(1, ss.get("vitem_pagina", 1)), total_paginas)
    ss["vitem_pagina"] = pagina
    inicio = (pagina - 1) * _ITENS_POR_PAGINA_VENDA
    itens_pagina = itens[inicio:inicio + _ITENS_POR_PAGINA_VENDA]

    st.markdown(_CSS_TABELA_ITENS, unsafe_allow_html=True)

    with _container_com_key("vitem_tabela", border=True):
        with _container_com_key("vitem_cab"):
            cabecalho = st.columns(larguras, vertical_alignment="center")
            for coluna, titulo, alinhar in zip(cabecalho, titulos, alinhamento):
                coluna.markdown(
                    _celula_tabela(html.escape(titulo), alinhar, cabecalho=True),
                    unsafe_allow_html=True,
                )

        for item in itens_pagina:
            uid = item["uid"]
            produto = item["produto"]
            with _container_com_key(f"vitem_linha_{uid}"):
                cols = st.columns(larguras, vertical_alignment="center")

                nome = f"{produto.get('codigo_interno') or '-'} — {produto.get('descricao') or '-'}"
                texto_produto = html.escape(nome)
                if uid == editando:
                    texto_produto += ' <span style="opacity:0.6;font-weight:400;">· em edição</span>'
                valores = [
                    texto_produto,
                    f"{item['quantidade']:g}",
                    html.escape(_fmt_moeda(item["valor_lista"])),
                    html.escape(_fmt_moeda(item["valor_desconto"])),
                    html.escape(_fmt_moeda(item["valor_final"])),
                ]
                for i, valor in enumerate(valores):
                    titulo_celula = None
                    if i == 0:
                        titulo_celula = nome
                    elif i == 4:
                        titulo_celula = (
                            f"Valor final: {_fmt_moeda(item['valor_final'])}\n"
                            f"Comissão: {_fmt_moeda(item.get('comissao_valor', 0))}"
                        )
                    cols[i].markdown(
                        _celula_tabela(
                            valor,
                            alinhamento[i],
                            negrito=(i == 4 or uid == editando),
                            titulo=titulo_celula,
                        ),
                        unsafe_allow_html=True,
                    )
                cols[5].button(
                    "✏️",
                    key=f"vitem_editar_{uid}",
                    help="Editar este item",
                    use_container_width=True,
                    on_click=_cb_editar_item,
                    args=(uid, produtos_opcoes),
                )
                cols[6].button(
                    "🗑️",
                    key=f"vitem_remover_{uid}",
                    help="Remover este item",
                    use_container_width=True,
                    on_click=_cb_remover_item,
                    args=(uid,),
                )

    # Rodapé: paginação (só quando há mais de uma página) + total.
    if total_paginas > 1:
        with _container_com_key("vitem_paginacao"):
            _, c_ant, c_txt, c_prox, _ = st.columns([3, 1, 2, 1, 3], vertical_alignment="center")
            c_ant.button(
                "◀",
                key="vitem_pag_ant",
                help="Página anterior",
                disabled=pagina <= 1,
                use_container_width=True,
                on_click=_cb_pagina_itens,
                args=(-1,),
            )
            c_txt.markdown(
                f"<div style='text-align:center;font-size:0.85rem;'>"
                f"Página <b>{pagina}</b> de <b>{total_paginas}</b></div>",
                unsafe_allow_html=True,
            )
            c_prox.button(
                "▶",
                key="vitem_pag_prox",
                help="Próxima página",
                disabled=pagina >= total_paginas,
                use_container_width=True,
                on_click=_cb_pagina_itens,
                args=(1,),
            )

    total = sum(i["valor_final"] for i in itens)
    st.markdown(
        '<div style="text-align:right;font-size:0.85rem;padding-top:0.4rem;">'
        f"<b>{len(itens)} item(ns)</b> nesta venda · Valor total: "
        f"<b>{html.escape(_fmt_moeda(total))}</b></div>",
        unsafe_allow_html=True,
    )


def _selectbox_cadastro(rotulo, opcoes, tabela, index=0, key=None, placeholder=None):
    """Selectbox de cadastro que nunca some em silêncio: se não há registros
    ativos, mostra o campo desabilitado com um aviso. Não inclui opção vazia
    (quando o campo é obrigatório e não tem padrão, use index=None)."""
    if not opcoes:
        st.selectbox(
            rotulo,
            ["Nenhum registro ativo"],
            disabled=True,
            key=f"{key}_vazio" if key else None,
        )
        st.caption(f"⚠️ Sem registros ativos em `{tabela}`.")
        return None
    extras = {"placeholder": placeholder} if placeholder else {}
    return st.selectbox(rotulo, opcoes, index=index, key=key, **extras)


def _render_form_item(produtos_opcoes: dict, itens: list):
    """Formulário único de item: serve tanto para adicionar um item novo
    quanto para editar um item que veio da tabela-resumo."""
    ss = st.session_state
    editando = ss.get("venda_item_editando")

    # Um produto já usado em outro item não pode ser escolhido de novo
    # (o item em edição não conta, para poder manter o próprio produto).
    usados = {i["produto"]["id"] for i in itens if i["uid"] != editando}
    rotulos = [r for r, p in produtos_opcoes.items() if p["id"] not in usados]

    # O componente reutilizável busca_produto concentra a pesquisa por código
    # ou descrição e mantém o mesmo padrão visual das demais telas.
    produtos_disponiveis = [p for p in produtos_opcoes.values() if p["id"] not in usados]
    produto_id_atual = ss.get("vitem_ref")

    with st.container(border=True):
        st.markdown("**✏️ Editando item**" if editando else "**➕ Novo item**")

        produto_id_selecionado = busca_produto(
            produtos_disponiveis,
            label="Produto",
            placeholder="Digite o código ou pesquise pela descrição...",
            key="vitem_busca_produto",
            mostrar_saldo=True,
            valor_selecionado=produto_id_atual,
        )
        produto = next(
            (p for p in produtos_disponiveis if p.get("id") == produto_id_selecionado),
            None,
        )

        # O retorno do componente é a fonte única da seleção.
        if produto_id_selecionado != produto_id_atual:
            ss["vitem_ref"] = produto_id_selecionado
            if produto:
                _definir_form_item(
                    produto, 1, produto.get("estoque_preco_venda_sugerida") or 0, 0.0
                )
            else:
                ss["vitem_ref"] = None

        if not produto:
            # Sem produto, os campos abaixo não são desenhados (e o Streamlit
            # descarta o estado deles): na próxima escolha, recomeça do zero.
            ss["vitem_ref"] = None
        else:
            if ss.get("vitem_ref") != produto["id"]:
                _definir_form_item(
                    produto, 1, produto.get("estoque_preco_venda_sugerida") or 0, 0.0
                )

            col_qtd, col_lista, col_desc, col_final = st.columns(4)

            with col_qtd:
                st.markdown("**Quantidade**")
                estado_qtd = contador_quantidade(
                    valor=max(1, int(ss.get("vitem_qtd", 1))),
                    min_valor=1,
                    bloqueado=True,
                    label="",
                    key=f"contador_vitem_{ss.get('vitem_gen', 0)}",
                )
                quantidade = max(1, int(estado_qtd.get("valor", ss.get("vitem_qtd", 1))))
                ss["vitem_qtd"] = quantidade

            with col_lista:
                col_rotulo, col_bloqueio = st.columns([2, 1], vertical_alignment="center")
                with col_rotulo:
                    st.markdown("**Valor lista**")
                with col_bloqueio:
                    aberto = st.toggle(
                        "🔓",
                        key="vitem_bloqueio",
                        help=(
                            "Travado: usa o valor sugerido do estoque. "
                            "Ative para digitar um valor lista diferente."
                        ),
                    )
                valor_lista_texto = st.text_input(
                    "Valor lista",
                    key="vitem_valor_lista_input",
                    disabled=not aberto,
                    label_visibility="collapsed",
                )
                valor_lista = _parse_valor_input(
                    valor_lista_texto, ss.get("vitem_valor_lista", 0)
                )
                ss["vitem_valor_lista"] = valor_lista

            with col_desc:
                st.markdown("**Desconto**")
                desconto_texto = st.text_input(
                    "Desconto",
                    key="vitem_desconto",
                    label_visibility="collapsed",
                )
                valor_desconto = _parse_valor_input(desconto_texto, 0.0)

            with col_final:
                st.markdown("**Valor final**")
                valor_final = quantidade * valor_lista - valor_desconto
                # Widget com `key`: o valor exibido vem do session_state e o
                # parâmetro `value` seria ignorado após a 1ª execução. Por
                # isso o cálculo é gravado no session_state antes de criar o
                # widget.
                ss["vitem_valor_final"] = _fmt_valor_input(valor_final)
                st.text_input(
                    "Valor final",
                    key="vitem_valor_final",
                    disabled=True,
                    label_visibility="collapsed",
                )

        if editando:
            col_ok, col_cancelar = st.columns(2)
            col_ok.button(
                "✔️ Atualizar item",
                key="vitem_btn_atualizar",
                use_container_width=True,
                disabled=produto is None,
                on_click=_cb_adicionar_item,
                args=(produtos_opcoes,),
            )
            col_cancelar.button(
                "Cancelar edição",
                key="vitem_btn_cancelar_edicao",
                use_container_width=True,
                on_click=_cb_cancelar_edicao,
            )
        else:
            st.button(
                "➕ Adicionar item",
                key="vitem_btn_adicionar",
                use_container_width=True,
                disabled=produto is None,
                on_click=_cb_adicionar_item,
                args=(produtos_opcoes,),
            )

        mensagem = ss.pop("vitem_msg", None)
        if mensagem:
            st.error(mensagem)


def _cb_editar_header():
    """Botão ✏️ do resumo: reabre o cabeçalho para edição (itens são mantidos)."""
    st.session_state["venda_header_ok"] = False


def _injetar_estilo_dialogo() -> None:
    """Faz o popup abrir ao lado do menu lateral, em vez de por cima dele.

    O Streamlit desenha o dialog em tela cheia (inclusive sobre a sidebar).
    Medimos a largura atual da sidebar (que muda se for recolhida ou
    redimensionada) e deslocamos o popup por essa largura via CSS."""
    st.markdown(
        """
        <style>
        :root { --menu-lateral-w: 0px; }
        div[data-testid="stDialog"],
        div[data-testid="stDialog"] div[data-baseweb="modal"] {
            left: var(--menu-lateral-w) !important;
            width: calc(100vw - var(--menu-lateral-w)) !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    components.html(
        """
        <script>
        (function () {
            const doc = window.parent.document;
            function medir() {
                const sb = doc.querySelector('section[data-testid="stSidebar"]');
                let w = 0;
                if (sb) { w = Math.max(0, sb.getBoundingClientRect().right); }
                doc.documentElement.style.setProperty('--menu-lateral-w', w + 'px');
            }
            medir();
            const sb = doc.querySelector('section[data-testid="stSidebar"]');
            if (sb && window.ResizeObserver) { new ResizeObserver(medir).observe(sb); }
            window.parent.addEventListener('resize', medir);
            setInterval(medir, 500);
        })();
        </script>
        """,
        height=0,
    )


def _render_resumo_header(h: dict) -> None:
    """Cabeçalho da venda em uma única linha + botão de editar."""
    partes = [("Canal", h["canal_nome"])]
    if h.get("feira_nome"):
        partes.append(("Feira", h["feira_nome"]))
    partes += [
        ("Cliente", h["cliente"]),
        ("Data", h["data_venda_texto"]),
        ("Pagamento", h["forma_pagamento_nome"]),
        ("Status", h["status_nome"]),
    ]
    texto = " &nbsp;·&nbsp; ".join(
        f"<span style='opacity:0.65;'>{r}:</span> <b>{html.escape(str(v))}</b>"
        for r, v in partes
    )
    with st.container(border=True):
        try:
            col_txt, col_btn = st.columns([14, 1], vertical_alignment="center")
        except TypeError:
            col_txt, col_btn = st.columns([14, 1])
        with col_txt:
            st.markdown(
                "<div style='font-size:0.92rem; white-space:nowrap; overflow:hidden; "
                f"text-overflow:ellipsis;'>{texto}</div>",
                unsafe_allow_html=True,
            )
        with col_btn:
            st.button(
                "✏️",
                key="venda_header_editar",
                help="Editar cabeçalho",
                on_click=_cb_editar_header,
                use_container_width=True,
            )


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
            linhas_estoque = buscar_todos(
                lambda: supabase
                .table("estoque")
                .select(
                    "quantidade_atual, estoque_preco_venda_sugerida, "
                    "produtos(id, codigo_interno, descricao)"
                )
                .gt("quantidade_atual", 0)
                .order("produto_id")
            )
            for linha in linhas_estoque:
                produto = linha.get("produtos")
                if produto:
                    rotulo = (
                        f"{produto['codigo_interno']} — {produto['descricao']} "
                        f"(saldo: {float(linha.get('quantidade_atual') or 0):g})"
                    )
                    # Mantém junto ao produto o preço de venda sugerido
                    # armazenado na tabela estoque.
                    produtos_opcoes[rotulo] = {
                        **produto,
                        "estoque_preco_venda_sugerida": float(
                            linha.get("estoque_preco_venda_sugerida") or 0
                        ),
                        "saldo": float(linha.get("quantidade_atual") or 0),
                    }
        except Exception as e:
            st.error(f"Erro ao carregar produtos em estoque: {e}")

    data_atual = _parse_data_segura(venda.get("data_venda")) or date.today()

    st.caption("Nova venda" if nova_venda else f"Venda #{venda['id']}")
    _injetar_estilo_dialogo()

    sufixo = "nova" if nova_venda else str(venda.get("id"))
    form_key = f"form_venda_{sufixo}"

    ss = st.session_state
    feira_atual_nome = feira_atual.get("nome_feira") or ""

    # Valores iniciais do cabeçalho de uma venda existente.
    header_da_venda = None
    if not nova_venda:
        header_da_venda = {
            "canal_nome": canal_atual_nome,
            "feira_nome": feira_atual_nome or None,
            "cliente": venda.get("cliente") or "",
            "data_venda_texto": data_atual.strftime("%d/%m/%Y"),
            "forma_pagamento_nome": forma_atual_desc,
            "status_nome": status_atual_nome,
        }

    # O estado do cabeçalho pertence a UMA venda (a nova ou uma já existente);
    # se o popup foi aberto para outra, começa do zero.
    if ss.get("venda_header_ref") != sufixo:
        ss.pop("venda_header", None)
        ss.pop("venda_header_ok", None)
        ss["venda_header_ref"] = sufixo
        if header_da_venda:
            # Edição: já abre com o cabeçalho recolhido (dados da venda) e o
            # item carregado; o ✏️ do resumo reabre o cabeçalho.
            ss["venda_header"] = header_da_venda
            ss["venda_header_ok"] = True

    header_salvo = ss.get("venda_header") or {}
    # Duas etapas (nova venda e edição): 1) cabeçalho, 2) item(ns), com o
    # cabeçalho recolhido em uma linha.
    header_fechado = bool(ss.get("venda_header_ok")) and bool(header_salvo)

    if nova_venda:
        header_base = header_salvo
        produto_selecionado = None
    else:
        header_base = header_salvo or header_da_venda
        produto_selecionado = {
            "id": venda.get("produto_id"),
            "codigo_interno": codigo_atual,
        }

    salvar = cancelar = excluir = False
    salvar_header = False
    itens_validos: list[dict] = []
    nomes_clientes = [c.get("nome") for c in clientes_disponiveis if c.get("nome")]
    nomes_feiras = [f["nome_feira"] for f in feiras_disponiveis]

    # Todos os campos do popup ficam dentro de uma única borda.
    with st.container(border=True):
        if header_fechado:
            # ------------------------------------------------------------
            # Etapa 2: cabeçalho recolhido em uma linha + item(ns).
            # ------------------------------------------------------------
            canal_nome = header_salvo["canal_nome"]
            feira_nome = header_salvo.get("feira_nome")
            cliente = header_salvo["cliente"]
            data_venda_texto = header_salvo["data_venda_texto"]
            forma_pagamento_nome = header_salvo["forma_pagamento_nome"]
            status_nome = header_salvo["status_nome"]
            _render_resumo_header(header_salvo)

            if nova_venda:
                st.markdown("##### Itens da venda")

                if not produtos_opcoes:
                    st.warning("Nenhum produto com saldo em estoque.")

                # Mesma lista usada pelos callbacks; é ela que será gravada.
                itens_validos = st.session_state.setdefault("venda_itens", [])

                # 1) Formulário único (adicionar / editar item) · 2) tabela-resumo
                # (somente leitura) logo abaixo.
                _render_form_item(produtos_opcoes, itens_validos)

                # Resultado de um salvamento parcial (ver mais abaixo).
                msg_falha = st.session_state.pop("venda_msg_falha", None)
                if msg_falha:
                    st.warning(msg_falha)

                _render_tabela_itens(itens_validos, produtos_opcoes)

                st.markdown("<div style='height: 1.25rem;'></div>", unsafe_allow_html=True)

                with st.form(form_key, border=False):
                    col_salvar, col_cancelar = st.columns(2)
                    salvar = col_salvar.form_submit_button(
                        "💾 Salvar", type="secondary", use_container_width=True
                    )
                    cancelar = col_cancelar.form_submit_button(
                        "Cancelar", use_container_width=True
                    )
            else:
                # Edição: sempre há um único item, já carregado (sem tabela).
                # Produto e canal continuam bloqueados para preservar o
                # ajuste de estoque.
                st.markdown("##### Item da venda")

                with st.container(border=True):
                    st.markdown("**✏️ Editando item**")
                    st.text_input(
                        "Produto",
                        value=" — ".join(
                            filter(None, [codigo_atual, produto_atual.get("descricao") or ""])
                        ),
                        disabled=True,
                    )
                    _resetar_controles_venda(venda, nova_venda, produto_selecionado)
                    quantidade, valor_lista, valor_desconto, valor_final = (
                        _render_controles_valores_venda()
                    )

                st.markdown("<div style='height: 1.25rem;'></div>", unsafe_allow_html=True)

                with st.form(form_key, border=False):
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

        else:
            # ------------------------------------------------------------
            # Etapa 1: cabeçalho. Os widgets ficam fora do st.form (o form
            # carrega só os botões), assim a escolha do canal habilita/
            # desabilita a Feira na hora; voltam preenchidos se o cabeçalho
            # for reaberto.
            # ------------------------------------------------------------
            col_canal, col_feira = st.columns([1, 1])

            with col_canal:
                if nova_venda:
                    canal_salvo = header_base.get("canal_nome")
                    canal_nome = st.selectbox(
                        "Canal de venda",
                        nomes_canais,
                        index=(
                            nomes_canais.index(canal_salvo)
                            if canal_salvo in nomes_canais else None
                        ),
                        placeholder="Selecione o canal...",
                        key=f"venda_canal_nome_{sufixo}",
                    ) if nomes_canais else None
                else:
                    st.text_input("Canal de venda", value=canal_atual_nome, disabled=True)
                    canal_nome = canal_atual_nome

            # A feira só é habilitada quando o canal selecionado for "Feira".
            canal_eh_feira = (canal_nome or "").strip().casefold() == "feira"
            feira_padrao = header_base.get("feira_nome") or ""

            with col_feira:
                if not canal_eh_feira:
                    st.selectbox(
                        "Feira",
                        ["Selecione o canal Feira" if nova_venda else "Não se aplica"],
                        index=0,
                        disabled=True,
                        key=f"venda_feira_desabilitada_{sufixo}",
                    )
                    feira_nome = None
                else:
                    feira_nome = st.selectbox(
                        "Feira",
                        nomes_feiras,
                        index=(
                            nomes_feiras.index(feira_padrao)
                            if feira_padrao in nomes_feiras else None
                        ),
                        placeholder="Selecione a feira...",
                        key=f"venda_feira_{sufixo}",
                    )

            col_cliente, col_data, col_forma, col_status = st.columns(4)

            with col_cliente:
                cliente = campo_cliente(
                    "Cliente",
                    value=header_base.get("cliente", ""),
                    opcoes=nomes_clientes,
                    placeholder="Digite ou selecione o cliente...",
                    key=f"venda_cliente_{sufixo}",
                )

            with col_data:
                data_venda_texto = campo_mascarado(
                    "Data da venda",
                    value=header_base.get("data_venda_texto")
                    or data_atual.strftime("%d/%m/%Y"),
                    tipo="data",
                    placeholder="DD/MM/YYYY",
                    key=f"venda_data_mask_{sufixo}",
                )

            with col_forma:
                forma_base = header_base.get("forma_pagamento_nome")
                forma_pagamento_nome = _selectbox_cadastro(
                    "Forma de pagamento",
                    nomes_formas,
                    "formas_pagamento",
                    index=nomes_formas.index(forma_base) if forma_base in nomes_formas else None,
                    placeholder="Selecione...",
                    key=f"venda_forma_pagamento_{sufixo}",
                )

            with col_status:
                status_base = header_base.get("status_nome")
                status_nome = _selectbox_cadastro(
                    "Status",
                    nomes_status,
                    "status_venda",
                    index=nomes_status.index(status_base) if status_base in nomes_status else 0,
                    key=f"venda_status_{sufixo}",
                )

            st.markdown("<div style='height: 0.5rem;'></div>", unsafe_allow_html=True)

            with st.form(f"{form_key}_header", border=False):
                col_sh, col_ch = st.columns(2)
                salvar_header = col_sh.form_submit_button(
                    "💾 Salvar cabeçalho", type="secondary", use_container_width=True
                )
                cancelar = col_ch.form_submit_button(
                    "Cancelar", use_container_width=True
                )

            if salvar_header:
                erro = None
                if not canal_nome:
                    erro = "Selecione o canal de venda."
                elif canal_eh_feira and not feira_nome:
                    erro = "Selecione a feira."
                elif not (cliente or "").strip():
                    erro = "Informe o cliente."
                elif not forma_pagamento_nome:
                    erro = "Selecione a forma de pagamento."
                elif not status_nome:
                    erro = "Selecione o status."
                else:
                    try:
                        datetime.strptime((data_venda_texto or "").strip(), "%d/%m/%Y")
                    except ValueError:
                        erro = "Data da venda inválida. Use o formato DD/MM/YYYY."

                if erro:
                    st.error(erro)
                else:
                    ss["venda_header"] = {
                        "canal_nome": canal_nome,
                        "feira_nome": feira_nome if canal_eh_feira else None,
                        "cliente": cliente.strip(),
                        "data_venda_texto": data_venda_texto.strip(),
                        "forma_pagamento_nome": forma_pagamento_nome,
                        "status_nome": status_nome,
                    }
                    ss["venda_header_ok"] = True
                    if not _rerun_dialogo():
                        st.info("Cabeçalho salvo. Interaja com a tela para continuar.")

    if cancelar:
        _limpar_itens_venda_nova()
        st.rerun()

    if excluir:
        try:
            sb = get_client()
            excluir_venda(sb, venda)
            st.success("Venda excluída e estoque ajustado com sucesso!")
            _limpar_itens_venda_nova()
            st.rerun()
        except Exception as e:
            st.error(f"Erro ao excluir venda: {e}")

    if not salvar:
        return

    try:
        sb = get_client()

        if nova_venda:
            if st.session_state.get("vitem_ref"):
                st.error(
                    "Há um item em preenchimento que ainda não foi gravado na tabela. "
                    "Clique em \"Adicionar item\" (ou \"Atualizar item\"), ou limpe o "
                    "produto, antes de salvar a venda."
                )
                return
            if not itens_validos:
                st.error("Adicione ao menos um produto à venda.")
                return
        elif not produto_selecionado:
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

        # Garante que o cliente digitado exista na tabela `clientes`. Para
        # clientes novos, além do nome, já grava o canal e (quando aplicável)
        # a feira desta venda — assim como na importação por planilha.
        cliente_nome = cliente.strip()
        _obter_ou_criar_cliente(
            sb,
            cliente_nome,
            canal_id=canal_id,
            detalhe_feira_id=detalhe_feira_id,
        )

        try:
            data_venda = datetime.strptime(data_venda_texto.strip(), "%d/%m/%Y").date()
        except (ValueError, TypeError):
            st.error("Data da venda inválida. Use o formato DD/MM/YYYY.")
            return

        # Campos de "header", compartilhados por todas as linhas/itens desta
        # venda (iguais para todo mundo, independente de quantos produtos).
        dados_header = {
            "canal_venda_id": canal_id,
            "status_id": status_id,
            "data_venda": data_venda.isoformat(),
            "cliente": cliente.strip(),
            "forma_pagamento_id": forma_pagamento_id,
            "detalhe_feira_id": detalhe_feira_id,
        }

        if nova_venda:
            # Cada item vira uma venda isolada na tabela `vendas`, com os
            # mesmos dados de header e seus próprios produto/quantidade/
            # valores — equivalente a duplicar manualmente os campos
            # repetidos em várias linhas, só que feito pela tela.
            sucesso = 0
            falhas = []
            itens_falhos = []
            for item in itens_validos:
                try:
                    comissao_percentual, comissao_valor = calcular_comissao_venda(
                        sb, canal_id, item["valor_final"]
                    )
                    dados_item = {
                        **dados_header,
                        "produto_id": item["produto"]["id"],
                        "quantidade": float(item["quantidade"]),
                        "valor_lista": float(item["valor_lista"]),
                        "valor_desconto": float(item["valor_desconto"]),
                        "valor_final": float(item["valor_final"]),
                        "comissao_percentual": comissao_percentual,
                        "comissao_valor": comissao_valor,
                    }
                    inserir_venda_e_baixar_estoque(
                        sb,
                        dados_item,
                        item["produto"]["id"],
                        motivo_saida="Venda Manual",
                    )
                    sucesso += 1
                except Exception as exc:
                    itens_falhos.append(item)
                    falhas.append(
                        f"{item['produto'].get('codigo_interno') or item['produto']['id']}: {exc}"
                    )

            if falhas:
                # Os itens já gravados saem da lista; só os que falharam
                # permanecem na tabela para o usuário corrigir e salvar de novo
                # (sem duplicar o que já foi registrado).
                st.session_state["venda_itens"] = itens_falhos
                if st.session_state.get("venda_item_editando") not in {
                    i["uid"] for i in itens_falhos
                }:
                    st.session_state["venda_item_editando"] = None
                mensagem = (
                    f"⚠️ {sucesso} de {len(itens_validos)} item(ns) registrados com sucesso "
                    f"(estoque já atualizado e removidos da lista). "
                    f"{len(falhas)} falharam e continuam na tabela para nova tentativa:\n\n"
                    + "\n".join(f"- {f}" for f in falhas)
                )
                st.session_state["venda_msg_falha"] = mensagem
                if not _rerun_dialogo():
                    # Versão do Streamlit sem rerun de fragmento: mostra aqui.
                    st.session_state.pop("venda_msg_falha", None)
                    st.warning(mensagem)
            else:
                st.success(
                    f"{sucesso} venda(s) registrada(s) com sucesso e estoque atualizado!"
                )
                _limpar_itens_venda_nova()
                st.rerun()
        else:
            dados_novos = {
                **dados_header,
                "produto_id": produto_selecionado["id"],
                "quantidade": float(quantidade),
                "valor_lista": float(valor_lista),
                "valor_desconto": float(valor_desconto),
                "valor_final": float(valor_final),
            }
            comissao_percentual, comissao_valor = calcular_comissao_venda(
                sb, canal_id, valor_final
            )
            dados_novos["comissao_percentual"] = comissao_percentual
            dados_novos["comissao_valor"] = comissao_valor

            editar_venda(sb, venda, dados_novos)
            st.success("Venda atualizada e estoque ajustado com sucesso!")
            _limpar_itens_venda_nova()
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
    st.session_state["vendas_filtro_codigo"] = ""
    st.session_state["pagina_atual_vendas"] = 1



def _obter_ou_criar_cliente(
    sb,
    nome: str,
    canal_id: int | None = None,
    detalhe_feira_id: int | None = None,
) -> int | None:
    """Retorna o id do cliente pelo nome ou cria um cliente mínimo.

    Se o usuário digitar um nome que ainda não existe, o cliente é criado
    com `nome` e, quando informados, `canal_id` e `detalhe_feira_id` —
    registrando por qual canal (e feira, se aplicável) esse cliente entrou
    pela primeira vez. Telefone e datas de nascimento/casamento continuam
    nulos, pois não são coletados nesta tela. Um cliente já existente nunca
    tem esses campos sobrescritos aqui.
    """
    nome = (nome or "").strip()
    if not nome:
        return None

    try:
        existentes = buscar_todos(
            lambda: sb.table("clientes").select("id, nome").order("id")
        )
    except Exception as e:
        raise RuntimeError(f"Não foi possível consultar os clientes: {e}") from e

    normalizado = nome.casefold()
    for cliente in existentes:
        if (cliente.get("nome") or "").strip().casefold() == normalizado:
            return cliente["id"]

    dados_novo_cliente = {"nome": nome}
    if canal_id is not None:
        dados_novo_cliente["canal_id"] = canal_id
    if detalhe_feira_id is not None:
        dados_novo_cliente["detalhe_feira_id"] = detalhe_feira_id

    try:
        resp = sb.table("clientes").insert(dados_novo_cliente).execute()
        return resp.data[0]["id"] if resp.data else None
    except Exception as e:
        # O índice único lower(trim(nome)) também protege contra corrida
        # entre duas inclusões simultâneas. Se já existir, recupera o registro.
        try:
            existentes = buscar_todos(
                lambda: sb.table("clientes").select("id, nome").order("id")
            )
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
            .order("ordem_exibicao", nullsfirst=False)
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
            .order("ordem_exibicao", nullsfirst=False)
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
            .order("ordem_exibicao", nullsfirst=False)
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
            .order("ordem_exibicao", nullsfirst=False)
            .order("nome_feira")
            .execute()
        ).data or []
    except Exception:
        feiras = []

    try:
        clientes = buscar_todos(
            lambda: supabase.table("clientes")
            .select("id, nome")
            .order("nome")
            .order("id")
        )
    except Exception:
        clientes = []

    return canais, status, formas, feiras, clientes



def _abrir_venda_pendente():
    venda_id = st.session_state.pop("vendas_abrir_id", None)
    if not venda_id:
        return
    try:
        venda_resp = (
            supabase.table("vendas")
            .select(
                "id, produto_id, quantidade, valor_lista, valor_desconto, valor_final, "
                "data_venda, cliente, detalhe_feira_id, "
                "produtos(descricao, codigo_interno), canais_venda(nome), "
                "status_venda(nome), formas_pagamento(descricao), detalhes_feira(nome_feira)"
            )
            .eq("id", int(venda_id))
            .single()
            .execute()
        )
        venda = venda_resp.data
        if not venda:
            st.warning(f"Venda #{venda_id} não encontrada.")
            return

        _limpar_itens_venda_nova()
        canais, status, formas, feiras, clientes = _carregar_cadastros_popup()
        _dialog_editar_venda(venda, canais, status, formas, feiras, clientes)
    except Exception as e:
        st.error(f"Erro ao abrir a venda #{venda_id}: {e}")


def _gerar_excel_vendas(vendas: list[dict]) -> bytes:
    """Planilha com as vendas recebidas (uma linha por venda), com valores
    numéricos reais (somáveis no Excel), filtro, cabeçalho fixo e linha de total."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Vendas"

    cabecalhos = [
        "Data", "Canal", "Feira", "Cód. Produto", "Produto", "Qtd",
        "Valor Lista", "Desconto", "Valor Final", "Comissão", "Forma Pgto", "Status", "Cliente",
    ]
    fill = PatternFill(fill_type="solid", fgColor="1F4E78")
    fonte = Font(color="FFFFFF", bold=True)
    for col, titulo in enumerate(cabecalhos, 1):
        c = ws.cell(1, col, titulo)
        c.fill = fill
        c.font = fonte
        c.alignment = Alignment(horizontal="center", vertical="center")

    linha = 2
    for v in vendas:
        produto = v.get("produtos") or {}
        valores = [
            _parse_data_segura(v.get("data_venda")),
            (v.get("canais_venda") or {}).get("nome") or "",
            (v.get("detalhes_feira") or {}).get("nome_feira") or "",
            produto.get("codigo_interno") or "",
            produto.get("descricao") or "",
            float(v.get("quantidade") or 0),
            float(v.get("valor_lista") or 0),
            float(v.get("valor_desconto") or 0),
            float(v.get("valor_final") or 0),
            float(v.get("comissao_valor") or 0),
            (v.get("formas_pagamento") or {}).get("descricao") or "",
            (v.get("status_venda") or {}).get("nome") or "",
            v.get("cliente") or "",
        ]
        for col, valor in enumerate(valores, 1):
            ws.cell(linha, col, valor)
        ws.cell(linha, 1).number_format = "DD/MM/YYYY"
        ws.cell(linha, 1).alignment = Alignment(horizontal="center")
        ws.cell(linha, 4).number_format = "@"  # código como texto (preserva zeros à esquerda)
        ws.cell(linha, 6).number_format = "0.##"
        for col in (7, 8, 9, 10):
            ws.cell(linha, col).number_format = "R$ #,##0.00"
        linha += 1

    ultima_dados = linha - 1

    # Linha de total com SUBTOTAL: acompanha os filtros aplicados na própria planilha.
    linha_total = linha
    ws.cell(linha_total, 5, "TOTAL").font = Font(bold=True)
    ws.cell(linha_total, 5).alignment = Alignment(horizontal="right")
    borda = Border(top=Side(style="thin"))
    for col in range(1, len(cabecalhos) + 1):
        ws.cell(linha_total, col).border = borda
    for col in (6, 7, 8, 9, 10):
        letra = get_column_letter(col)
        c = ws.cell(linha_total, col, f"=SUBTOTAL(109,{letra}2:{letra}{ultima_dados})")
        c.font = Font(bold=True)
        c.number_format = "0.##" if col == 6 else "R$ #,##0.00"

    larguras = [12, 18, 22, 16, 45, 8, 14, 14, 14, 14, 20, 14, 30]
    for i, largura in enumerate(larguras, 1):
        ws.column_dimensions[get_column_letter(i)].width = largura
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(cabecalhos))}{ultima_dados}"

    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _nome_arquivo_vendas(mes: str, canal: str, codigo: str = "") -> str:
    """vendas_<mes>_<canal>_<data>.xlsx, sem acentos nem caracteres inválidos."""
    import re
    import unicodedata

    def limpo(texto: str) -> str:
        sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
        return re.sub(r"[^A-Za-z0-9]+", "-", sem_acento).strip("-") or "todos"

    sufixo_codigo = f"_cod-{limpo(codigo)}" if codigo else ""
    return f"vendas_{limpo(mes)}_{limpo(canal)}{sufixo_codigo}_{date.today():%Y%m%d}.xlsx"


@_dialog("📥 Exportar vendas", width="small")
def _dialog_exportar_vendas(
    inicio: Optional[str], fim: Optional[str], canal: str, mes_rotulo: str, codigo: str = ""
):
    """Busca TODAS as vendas dos filtros atuais (não só a página visível) e
    oferece o download em Excel. A planilha só é gerada quando o popup abre."""
    st.caption(
        f"Período: **{mes_rotulo if mes_rotulo != 'Todos' else 'Todo período'}** · "
        f"Canal: **{canal if canal != 'Todos' else 'Todos os canais'}**"
        + (f" · Código do produto: **{codigo}**" if codigo else "")
    )

    canal_embed = "canais_venda!inner(nome)" if canal != "Todos" else "canais_venda(nome)"
    # inner join só quando há filtro de código, para não excluir vendas sem produto
    produto_embed = (
        "produtos!inner(descricao, codigo_interno)" if codigo else "produtos(descricao, codigo_interno)"
    )

    def _query():
        q = (
            supabase
            .table("vendas")
            .select(
                "id, quantidade, valor_lista, valor_desconto, valor_final, comissao_valor,"
                " data_venda, cliente,"
                f" {produto_embed}, {canal_embed},"
                " status_venda(nome), formas_pagamento(descricao),"
                " detalhes_feira(nome_feira)"
            )
        )
        if inicio:
            q = q.gte("data_venda", inicio)
        if fim:
            q = q.lt("data_venda", fim)
        if canal != "Todos":
            q = q.eq("canais_venda.nome", canal)
        if codigo:
            q = q.ilike("produtos.codigo_interno", f"%{codigo}%")
        # Ordem estável (data + id) para a paginação interna não repetir/perder linhas.
        return q.order("data_venda", desc=True).order("id", desc=True)

    try:
        with st.spinner("Gerando planilha..."):
            vendas = buscar_todos(_query)
            dados = _gerar_excel_vendas(vendas) if vendas else None
    except Exception as e:
        st.error(f"Não foi possível gerar a planilha: {e}")
        return

    if not vendas:
        st.info("Nenhuma venda para exportar com os filtros atuais.")
        return

    st.success(f"{len(vendas)} venda(s) prontas para exportar.")
    st.download_button(
        "⬇️ Baixar Excel",
        data=dados,
        file_name=_nome_arquivo_vendas(mes_rotulo, canal, codigo),
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
        key="vendas_download_excel",
    )


def _botao_adicionar_venda():
    """Botão '➕ Adicionar venda' (abre o popup de nova venda)."""
    if st.button(
        "➕ Adicionar venda",
        type="secondary",
        use_container_width=True,
        key="vendas_btn_adicionar",
    ):
        # Garante que nada de uma venda anterior (ex.: popup fechado pelo X
        # em vez de Cancelar) sobre para esta abertura.
        _limpar_itens_venda_nova()
        canais_popup, status_popup, formas_popup, feiras_popup, clientes_popup = _carregar_cadastros_popup()
        _dialog_editar_venda(
            None,
            canais_popup,
            status_popup,
            formas_popup,
            feiras_popup,
            clientes_popup,
        )


def _secao_listagem():
    _abrir_venda_pendente()
    # ------------------------------------------------------------------
    # 1) Dataset leve com TODAS as vendas (valor_final, data_venda e o nome
    #    do canal), usado para os KPIs fixos (Total/Ano/Mês atual), para o
    #    KPI "Filtros" e para montar as opções do filtro de mês/ano.
    # ------------------------------------------------------------------
    try:
        vendas_kpi = buscar_todos(
            lambda: supabase
            .table("vendas")
            .select(
                "valor_final, comissao_valor, data_venda,"
                " canais_venda(nome), produtos(codigo_interno)"
            )
            .order("id")
        )
    except Exception as e:
        st.error(f"Erro ao carregar vendas: {e}")
        return

    hoje = date.today()
    ano_atual, mes_atual = hoje.year, hoje.month

    def _acumula(filtro):
        qtd, soma, comissao = 0, 0.0, 0.0
        for v in vendas_kpi:
            dt = _parse_data_segura(v.get("data_venda"))
            if dt is None or not filtro(v, dt):
                continue
            qtd += 1
            soma += float(v.get("valor_final") or 0)
            comissao += float(v.get("comissao_valor") or 0)
        return qtd, soma, comissao

    qtd_total = len(vendas_kpi)
    soma_total = sum(float(v.get("valor_final") or 0) for v in vendas_kpi)
    comissao_total = sum(float(v.get("comissao_valor") or 0) for v in vendas_kpi)
    qtd_ano, soma_ano, comissao_ano = _acumula(lambda v, dt: dt.year == ano_atual)
    qtd_mes_atual, soma_mes_atual, comissao_mes_atual = _acumula(
        lambda v, dt: dt.year == ano_atual and dt.month == mes_atual
    )

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

    codigo_atual = (st.session_state.get("vendas_filtro_codigo") or "").strip()

    def _bate_filtro_atual(v, dt):
        if mes_selecionado_atual != "Todos":
            ano_f, mes_f = meses_disponiveis[opcoes_mes.index(mes_selecionado_atual) - 1]
            if dt.year != ano_f or dt.month != mes_f:
                return False
        if canal_selecionado_atual != "Todos":
            if ((v.get("canais_venda") or {}).get("nome")) != canal_selecionado_atual:
                return False
        if codigo_atual:
            codigo_venda = str((v.get("produtos") or {}).get("codigo_interno") or "")
            if codigo_atual.lower() not in codigo_venda.lower():
                return False
        return True

    qtd_filtro, soma_filtro, comissao_filtro = _acumula(_bate_filtro_atual)
    subtitulo_filtro = (
        f"{mes_selecionado_atual if mes_selecionado_atual != 'Todos' else 'Todo período'} · "
        f"{canal_selecionado_atual if canal_selecionado_atual != 'Todos' else 'Todos canais'}"
        + (f" · Cód. {codigo_atual}" if codigo_atual else "")
    )

    # ------------------------------------------------------------------
    # 3) Os 4 cartões de KPI (Todos padronizados com subtítulo)
    # ------------------------------------------------------------------
    _injetar_estilo_kpi()
    col_kpi1, col_kpi2, col_kpi3, col_kpi4 = st.columns(4)
    
    with col_kpi1:
        _kpi_card("Total", qtd_total, soma_total, comissao_total, subtitulo="Todo período")
        
    with col_kpi2:
        _kpi_card("Ano Atual", qtd_ano, soma_ano, comissao_ano, subtitulo=f"Ano de {ano_atual}")
        
    with col_kpi3:
        _kpi_card(
            "Mês Atual", qtd_mes_atual, soma_mes_atual, comissao_mes_atual, subtitulo=MESES_PT[mes_atual]
        )
        
    with col_kpi4:
        _kpi_card("Filtros", qtd_filtro, soma_filtro, comissao_filtro, subtitulo=subtitulo_filtro)

    st.markdown("---")

    if not vendas_kpi:
        st.info("Nenhuma venda registrada.")
        # O botão precisa existir mesmo sem registros, senão não há como
        # cadastrar a primeira venda.
        col_primeira, _ = st.columns([1, 3])
        with col_primeira:
            _botao_adicionar_venda()
        return

    # ------------------------------------------------------------------
    # 4) Widgets de filtro (Mês/Ano da venda e Canal de venda) + botão
    #    para limpar os filtros
    # ------------------------------------------------------------------
    col_filtro_mes, col_filtro_canal, col_filtro_codigo, col_acoes = st.columns([1.5, 1.5, 1.7, 2.6])
    with col_filtro_mes:
        mes_selecionado = st.selectbox("Mês da venda", opcoes_mes, key="vendas_filtro_mes")
    with col_filtro_canal:
        canal_selecionado = st.selectbox("Canal", opcoes_canal, key="vendas_filtro_canal")
    with col_filtro_codigo:
        codigo_selecionado = st.text_input(
            "Código do produto",
            placeholder="Digite o código...",
            key="vendas_filtro_codigo",
        ).strip()
    with col_acoes:
        st.markdown("<div style='margin-top:1.85rem;'></div>", unsafe_allow_html=True)
        btn_limpar, btn_adicionar, btn_exportar = st.columns([3, 3, 1.1])
        with btn_limpar:
            st.button(
                "🔄 Limpar filtros",
                use_container_width=True,
                key="vendas_btn_limpar_filtros",
                on_click=_resetar_filtros_vendas,
            )
        with btn_adicionar:
            _botao_adicionar_venda()
        with btn_exportar:
            if st.button(
                "📥",
                key="vendas_btn_exportar",
                help="Exportar para Excel (respeita os filtros de Mês, Canal e Código do produto)",
                use_container_width=True,
            ):
                exp_inicio = exp_fim = None
                if mes_selecionado != "Todos":
                    ano_f, mes_f = meses_disponiveis[opcoes_mes.index(mes_selecionado) - 1]
                    exp_inicio = date(ano_f, mes_f, 1).isoformat()
                    exp_fim = (
                        date(ano_f + 1, 1, 1) if mes_f == 12 else date(ano_f, mes_f + 1, 1)
                    ).isoformat()
                _dialog_exportar_vendas(
                    exp_inicio, exp_fim, canal_selecionado, mes_selecionado, codigo_selecionado
                )

    # Reseta a página para 1 sempre que algum filtro mudar
    assinatura_filtros = f"{mes_selecionado}|{canal_selecionado}|{codigo_selecionado}"
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
    # Mesmo raciocínio para o código do produto: inner join só com o filtro ativo.
    produto_embed = (
        "produtos!inner(descricao, codigo_interno)" if codigo_selecionado else "produtos(descricao, codigo_interno)"
    )

    def _monta_query():
        query = (
            supabase
            .table("vendas")
            .select(
                "id, produto_id, quantidade, valor_lista, valor_desconto,"
                " valor_final, comissao_percentual, comissao_valor, data_venda, cliente, detalhe_feira_id,"
                f" {produto_embed}, {canal_embed}, "
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

        if codigo_selecionado:
            query = query.ilike("produtos.codigo_interno", f"%{codigo_selecionado}%")

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
        # Data | Canal | Cod. Produto | Produto | Valor Final |
        # Forma Pgto | Status | Cliente | Edição
        c_data, c_canal, c_codigo, c_prod, c_total, c_forma, c_status, c_cliente, c_edicao = st.columns(
            [1.1, 1.2, 1.2, 2.5, 1.4, 1.5, 1.2, 1.7, 0.8]
        )
        c_data.markdown("**Data**")
        c_canal.markdown("**Canal**")
        c_codigo.markdown("**Cod. Produto**")
        c_prod.markdown("**Produto**")
        c_total.markdown("**Valor Final**")
        c_forma.markdown("**Forma Pgto**")
        c_status.markdown("**Status**")
        c_cliente.markdown("**Cliente**")
        c_edicao.markdown("**Edição**")

        st.divider()

        for v in vendas:
            col_data, col_canal, col_codigo, col_prod, col_total, col_forma, col_status, col_cliente, col_edicao = (
                st.columns([1.1, 1.2, 1.2, 2.5, 1.4, 1.5, 1.2, 1.7, 0.8])
            )

            col_data.write(str(v.get("data_venda") or "-")[:10])
            col_canal.write((v.get("canais_venda") or {}).get("nome") or "-")

            produto = v.get("produtos") or {}
            col_codigo.write(produto.get("codigo_interno") or "-")
            col_prod.write(produto.get("descricao") or "-")

            valor_final_fmt = _fmt_moeda(v.get("valor_final"))
            comissao_fmt = _fmt_moeda(v.get("comissao_valor"))
            percentual_fmt = (
                f"{float(v.get('comissao_percentual') or 0):g}%"
                if v.get("comissao_percentual") is not None else "-"
            )
            col_total.markdown(
                f'<span title="Valor final: {html.escape(valor_final_fmt)}&#10;Comissão: {html.escape(comissao_fmt)} ({html.escape(percentual_fmt)})" style="cursor:help;">{html.escape(valor_final_fmt)}</span>',
                unsafe_allow_html=True,
            )
            col_forma.write((v.get("formas_pagamento") or {}).get("descricao") or "-")
            col_status.write((v.get("status_venda") or {}).get("nome") or "-")
            col_cliente.write(v.get("cliente") or "-")

            if col_edicao.button(
                "✏️",
                key=f"editar_venda_{v['id']}",
                help="Editar ou excluir venda",
                use_container_width=True,
            ):
                _limpar_itens_venda_nova()
                (
                    canais_popup,
                    status_popup,
                    formas_popup,
                    feiras_popup,
                    clientes_popup,
                ) = _carregar_cadastros_popup()
                _dialog_editar_venda(
                    v,
                    canais_popup,
                    status_popup,
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


def _render_cabecalho_parceiro(parceiro: dict) -> None:
    canal_nome = (parceiro.get("canais_venda") or {}).get("nome") or "-"
    telefone = parceiro.get("telefone") or "-"
    email = parceiro.get("email") or "-"
    comissao = parceiro.get("percentual_comissao")
    comissao_fmt = f"{float(comissao):g}%" if comissao is not None else "-"
    observacao = (parceiro.get("observacao") or "").strip()

    observacao_html = (
        f'<div style="margin-top:0.5rem; font-size:0.85rem; opacity:0.75;">'
        f'{html.escape(observacao)}</div>'
        if observacao else ""
    )

    st.markdown(
        f"""
        <div style="border:1px solid rgba(128,128,128,0.35); border-radius:0.5rem;
                     padding:1rem 1.2rem; margin-bottom:1rem;">
            <div style="font-size:1.1rem; font-weight:700; margin-bottom:0.5rem;">
                {html.escape(parceiro.get('nome') or '')}
            </div>
            <div style="display:flex; gap:2rem; flex-wrap:wrap; font-size:0.9rem;">
                <div><span style="opacity:0.7;">Canal:</span> {html.escape(canal_nome)}</div>
                <div><span style="opacity:0.7;">Comissão padrão:</span> {comissao_fmt}</div>
                <div><span style="opacity:0.7;">Telefone:</span> {html.escape(telefone)}</div>
                <div><span style="opacity:0.7;">E-mail:</span> {html.escape(email)}</div>
            </div>
            {observacao_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _secao_parceiro_produtos_enviados(parceiro: dict) -> None:
    """Lista as vendas (= produtos enviados) feitas através do canal deste
    parceiro, em um período selecionável. Puramente de leitura — não depende
    de nenhuma regra de fechamento."""
    canal_id = parceiro.get("canal_id")
    if not canal_id:
        st.warning("Este parceiro não possui um canal de venda vinculado.")
        return

    col_ini, col_fim = st.columns(2)
    with col_ini:
        data_ini = st.date_input(
            "De", value=date.today().replace(day=1), key="parceiros_produtos_data_ini"
        )
    with col_fim:
        data_fim = st.date_input("Até", value=date.today(), key="parceiros_produtos_data_fim")

    if data_ini > data_fim:
        st.error("A data inicial não pode ser depois da data final.")
        return

    try:
        vendas = buscar_todos(
            lambda: supabase.table("vendas")
            .select(
                "id, data_venda, cliente, quantidade, valor_final, "
                "comissao_percentual, comissao_valor, "
                "produtos(codigo_interno, descricao), status_venda(nome)"
            )
            .eq("canal_venda_id", canal_id)
            .gte("data_venda", data_ini.isoformat())
            .lte("data_venda", data_fim.isoformat())
            .order("data_venda", desc=True)
            .order("id", desc=True)
        )
    except Exception as e:
        st.error(f"Não foi possível carregar os produtos enviados: {e}")
        return

    if not vendas:
        st.info("Nenhuma venda deste canal no período selecionado.")
        return

    linhas = []
    total_valor = 0.0
    total_comissao = 0.0
    for v in vendas:
        produto = v.get("produtos") or {}
        valor_final = float(v.get("valor_final") or 0)
        comissao_valor = float(v.get("comissao_valor") or 0)
        total_valor += valor_final
        total_comissao += comissao_valor
        linhas.append({
            "Data": v.get("data_venda"),
            "Cliente": v.get("cliente") or "-",
            "Produto": produto.get("descricao") or "-",
            "Código": produto.get("codigo_interno") or "-",
            "Qtd": v.get("quantidade"),
            "Valor": valor_final,
            "Comissão %": v.get("comissao_percentual"),
            "Comissão R$": comissao_valor,
            "Status": (v.get("status_venda") or {}).get("nome") or "-",
        })

    col_m1, col_m2, col_m3 = st.columns(3)
    col_m1.metric("Produtos enviados", len(linhas))
    col_m2.metric("Valor total", _fmt_moeda(total_valor))
    col_m3.metric("Comissão total", _fmt_moeda(total_comissao))

    st.dataframe(linhas, use_container_width=True, hide_index=True)


def _secao_parceiro_fechamento(parceiro: dict) -> None:
    """Fluxo de fechamento do parceiro:

    - Sem ciclo aberto: botão para iniciar um novo ciclo. `data_inicio` é
      automática (dia seguinte ao `data_fim` do último ciclo fechado); no
      primeiro ciclo do parceiro, pede a data manualmente.
    - Com ciclo aberto: o usuário escolhe a data final, vê as vendas do
      canal do parceiro nesse período que ainda não entraram em nenhum
      fechamento, pode desmarcar alguma (ela fica disponível para o próximo
      ciclo) e fecha o ciclo. A comissão de cada venda vem do que já está
      gravado nela (`comissao_percentual`/`comissao_valor`), não é
      recalculada aqui.
    """
    parceiro_id = parceiro["id"]
    canal_id = parceiro.get("canal_id")

    try:
        fechamentos = (
            supabase.table("parcerias_fechamentos")
            .select(
                "id, numero_ciclo, data_inicio, data_fim, percentual_comissao, "
                "valor_total_vendas, valor_total_comissao, status"
            )
            .eq("parceiro_id", parceiro_id)
            .order("numero_ciclo", desc=True)
            .execute()
            .data
            or []
        )
    except Exception as e:
        st.error(f"Não foi possível carregar os fechamentos: {e}")
        return

    ciclo_aberto = next((f for f in fechamentos if f.get("status") == "Aberto"), None)
    ultimo_fechamento = fechamentos[0] if fechamentos else None

    if not canal_id:
        st.warning("Este parceiro não possui um canal de venda vinculado.")
        return

    if not ciclo_aberto:
        proximo_numero = (ultimo_fechamento["numero_ciclo"] + 1) if ultimo_fechamento else 1

        if ultimo_fechamento and ultimo_fechamento.get("data_fim"):
            data_inicio_sugerida = (
                _parse_data_segura(ultimo_fechamento["data_fim"]) + timedelta(days=1)
            )
            st.info(
                f"Próximo ciclo: **#{proximo_numero}**, a partir de "
                f"**{data_inicio_sugerida.strftime('%d/%m/%Y')}** "
                "(dia seguinte ao fim do último ciclo)."
            )
        else:
            data_inicio_sugerida = st.date_input(
                "Data de início do 1º ciclo",
                value=date.today().replace(day=1),
                key="parceiros_fechamento_data_inicio_manual",
            )

        if st.button("▶️ Iniciar novo ciclo", key="parceiros_iniciar_ciclo"):
            try:
                supabase.table("parcerias_fechamentos").insert({
                    "parceiro_id": parceiro_id,
                    "numero_ciclo": proximo_numero,
                    "data_inicio": data_inicio_sugerida.isoformat(),
                    "percentual_comissao": parceiro.get("percentual_comissao") or 0,
                    "status": "Aberto",
                }).execute()
                st.success(f"Ciclo #{proximo_numero} iniciado.")
                st.rerun()
            except Exception as e:
                st.error(f"Não foi possível iniciar o ciclo: {e}")

    else:
        data_inicio_ciclo = _parse_data_segura(ciclo_aberto["data_inicio"])

        # Permite ajustar o início do ciclo enquanto ele estiver aberto.
        # Evita sobreposição com o ciclo finalizado anterior.
        anteriores = [
            f for f in fechamentos
            if f.get("id") != ciclo_aberto.get("id")
            and f.get("status") == "Finalizado"
            and f.get("data_fim")
        ]
        ultimo_anterior = max(
            anteriores,
            key=lambda f: f.get("numero_ciclo", 0),
            default=None,
        )
        data_min_inicio = None
        if ultimo_anterior:
            fim_anterior = _parse_data_segura(ultimo_anterior.get("data_fim"))
            if fim_anterior:
                data_min_inicio = fim_anterior + timedelta(days=1)

        if data_inicio_ciclo is None:
            data_inicio_ciclo = data_min_inicio or date.today()
        if data_min_inicio and data_inicio_ciclo < data_min_inicio:
            data_inicio_ciclo = data_min_inicio

        col_inicio, col_fim = st.columns(2)
        with col_inicio:
            novo_inicio_ciclo = st.date_input(
                "Início do ciclo",
                value=data_inicio_ciclo,
                min_value=data_min_inicio,
                key=f"parceiros_fechamento_data_inicio_{ciclo_aberto['id']}",
                help="A data inicial pode ser ajustada enquanto o ciclo estiver aberto.",
            )

        with col_fim:
            data_fim_ciclo = st.date_input(
                "Fechar com vendas até",
                value=max(date.today(), novo_inicio_ciclo),
                min_value=novo_inicio_ciclo,
                key=f"parceiros_fechamento_data_fim_{ciclo_aberto['id']}",
            )

        if novo_inicio_ciclo != data_inicio_ciclo:
            try:
                supabase.table("parcerias_fechamentos").update({
                    "data_inicio": novo_inicio_ciclo.isoformat(),
                }).eq("id", ciclo_aberto["id"]).execute()
                st.success(
                    f"Início do ciclo #{ciclo_aberto['numero_ciclo']} alterado para "
                    f"{novo_inicio_ciclo.strftime('%d/%m/%Y')}."
                )
                st.rerun()
            except Exception as e:
                st.error(f"Não foi possível alterar o início do ciclo: {e}")
                return

        if data_inicio_ciclo and data_fim_ciclo < data_inicio_ciclo:
            st.error("A data final não pode ser antes do início do ciclo.")
            return

        try:
            ids_ja_incluidos = {
                v["venda_id"]
                for v in (
                    supabase.table("parcerias_fechamento_vendas")
                    .select("venda_id")
                    .in_(
                        "fechamento_id",
                        [f["id"] for f in fechamentos],
                    )
                    .execute()
                    .data
                    or []
                )
            }
            vendas_periodo = (
                supabase.table("vendas")
                .select(
                    "id, data_venda, cliente, valor_final, "
                    "comissao_percentual, comissao_valor, "
                    "produtos(descricao), status_venda(nome)"
                )
                .eq("canal_venda_id", canal_id)
                .gte("data_venda", data_inicio_ciclo.isoformat())
                .lte("data_venda", data_fim_ciclo.isoformat())
                .order("data_venda")
                .execute()
                .data
                or []
            )
            candidatas = [v for v in vendas_periodo if v["id"] not in ids_ja_incluidos]
        except Exception as e:
            st.error(f"Não foi possível carregar as vendas do período: {e}")
            return

        if not candidatas:
            st.info("Nenhuma venda pendente neste período para incluir no fechamento.")

        # A tabela-resumo fica visualmente acima da tabela de vendas.
        # Usamos um placeholder para preencher o resumo depois de calcular as
        # vendas selecionadas, mantendo a ordem visual desejada.
        resumo_container = st.container()

        linhas_editor = [
            {
                "Incluir": True,
                "id": v["id"],
                "Data": v.get("data_venda"),
                "Cliente": v.get("cliente") or "-",
                "Produto": (v.get("produtos") or {}).get("descricao") or "-",
                "Valor": float(v.get("valor_final") or 0),
                "Comissão R$": float(v.get("comissao_valor") or 0),
            }
            for v in candidatas
        ]

        if candidatas:
            editado = st.data_editor(
                linhas_editor,
                key=f"parceiros_fechamento_editor_{ciclo_aberto['id']}",
                use_container_width=True,
                hide_index=True,
                disabled=["id", "Data", "Cliente", "Produto", "Valor", "Comissão R$"],
                column_order=["Incluir", "Data", "Cliente", "Produto", "Valor", "Comissão R$"],
                column_config={
                    "Incluir": st.column_config.CheckboxColumn("Incluir", width="small"),
                    "Data": st.column_config.TextColumn("Data", width="small"),
                    "Cliente": st.column_config.TextColumn("Cliente"),
                    "Produto": st.column_config.TextColumn("Produto"),
                    "Valor": st.column_config.NumberColumn("Valor", format="R$ %.2f", disabled=True),
                    "Comissão R$": st.column_config.NumberColumn("Comissão R$", format="R$ %.2f", disabled=True),
                },
            )
        else:
            editado = []

        selecionadas = [linha for linha in editado if linha["Incluir"]]
        valor_total = sum(float(l.get("Valor") or 0) for l in selecionadas)

        venda_por_id = {v["id"]: v for v in candidatas}
        comissao_total = sum(
            float(linha.get("Comissão R$") or 0)
            for linha in selecionadas
            if (venda_por_id.get(linha["id"], {}).get("status_venda") or {}).get("nome") != "Retirada - Parceiros"
        )

        retiradas_parceiros = [
            venda_por_id[linha["id"]]
            for linha in selecionadas
            if (venda_por_id.get(linha["id"], {}).get("status_venda") or {}).get("nome") == "Retirada - Parceiros"
        ]
        retirada_automatica = sum(float(v.get("valor_final") or 0) for v in retiradas_parceiros)

        # A seção de ajustes foi removida. A linha permanece no resumo para
        # deixar explícita a composição do fechamento, atualmente sem ajustes.
        ajustes_total = 0.0
        observacoes_ajustes = []
        valor_final_fechamento = comissao_total - retirada_automatica

        obs_auto_key = f"retirada_auto_obs_{ciclo_aberto['id']}"
        if obs_auto_key not in st.session_state:
            st.session_state[obs_auto_key] = "Retirada - Parceiros"

        # Preenche o resumo antes da tabela de produtos/vendas.
        with resumo_container:
            st.markdown(
                f"##### Resumo do fechamento - de {data_inicio_ciclo.strftime('%d/%m/%Y')} até {data_fim_ciclo.strftime('%d/%m/%Y')}"
            )
            resumo_df = pd.DataFrame([
                {"Descrição": "Vendas no período", "Valor": _fmt_moeda(valor_total), "Observação": ""},
                {"Descrição": "Comissão Total (+)", "Valor": _fmt_moeda(comissao_total), "Observação": ""},
                {"Descrição": "Retirada Peças (-)", "Valor": _fmt_moeda(-retirada_automatica), "Observação": st.session_state.get(obs_auto_key, "")},
                {"Descrição": "Ajustes", "Valor": _fmt_moeda(ajustes_total), "Observação": ""},
                {"Descrição": "Valor Final", "Valor": _fmt_moeda(valor_final_fechamento), "Observação": ""},
            ])
            styled_resumo = resumo_df.style.apply(
                lambda row: [
                    "font-weight: 700; background-color: rgba(0, 123, 255, 0.16);"
                    if row["Descrição"] == "Valor Final" else ""
                    for _ in row
                ],
                axis=1,
            )
            st.dataframe(styled_resumo, use_container_width=True, hide_index=True)

        if candidatas:
            st.caption(
                "Desmarque alguma venda para deixá-la de fora deste ciclo — ela "
                "continua disponível para um fechamento futuro."
            )

        # Informações/KPIs do ciclo aberto ficam abaixo da tabela de vendas.
        st.info(
            f"Ciclo **#{ciclo_aberto['numero_ciclo']}** aberto desde "
            f"**{data_inicio_ciclo.strftime('%d/%m/%Y')}**."
        )

        # Os componentes visuais de retirada de peças e os KPIs
        # foram removidos. Os valores continuam sendo calculados acima
        # porque são utilizados no fechamento do ciclo.

        if st.button(
            f"✅ Fechar ciclo #{ciclo_aberto['numero_ciclo']}",
            type="primary",
            disabled=not selecionadas,
            key="parceiros_fechar_ciclo",
        ):
            try:
                venda_por_id = {v["id"]: v for v in candidatas}
                registros = []
                for linha in selecionadas:
                    venda = venda_por_id[linha["id"]]
                    eh_retirada_parceiro = (
                        (venda.get("status_venda") or {}).get("nome")
                        == "Retirada - Parceiros"
                    )
                    registros.append({
                        "fechamento_id": ciclo_aberto["id"],
                        "venda_id": venda["id"],
                        "valor_venda": float(venda.get("valor_final") or 0),
                        "percentual_comissao": 0
                        if eh_retirada_parceiro
                        else float(venda.get("comissao_percentual") or 0),
                        "valor_comissao": 0
                        if eh_retirada_parceiro
                        else float(venda.get("comissao_valor") or 0),
                    })
                if registros:
                    supabase.table("parcerias_fechamento_vendas").insert(registros).execute()

                supabase.table("parcerias_fechamentos").update({
                    "data_fim": data_fim_ciclo.isoformat(),
                    "valor_total_vendas": valor_total,
                    "valor_total_comissao": comissao_total,
                    "status": "Finalizado",
                    "fechado_em": datetime.now().isoformat(),
                }).eq("id", ciclo_aberto["id"]).execute()

                st.success(
                    f"Ciclo #{ciclo_aberto['numero_ciclo']} fechado com "
                    f"{len(registros)} venda(s)."
                )
                st.rerun()
            except Exception as e:
                st.error(f"Não foi possível fechar o ciclo: {e}")

    st.divider()
    st.markdown("##### Histórico de ciclos")
    if not fechamentos:
        st.caption("Nenhum ciclo de fechamento registrado ainda para este parceiro.")
        return

    # Calcula o valor final de cada ciclo histórico usando a mesma regra
    # da visualização do fechamento: comissão menos retiradas de peças.
    valor_final_por_fechamento = {}
    try:
        ids_fechamentos = [f["id"] for f in fechamentos if f.get("id") is not None]
        vinculos_historico = []
        if ids_fechamentos:
            vinculos_historico = (
                supabase.table("parcerias_fechamento_vendas")
                .select("fechamento_id, venda_id, valor_comissao")
                .in_("fechamento_id", ids_fechamentos)
                .execute()
                .data
                or []
            )

        ids_vendas_historico = [v["venda_id"] for v in vinculos_historico if v.get("venda_id") is not None]
        status_por_venda = {}
        if ids_vendas_historico:
            vendas_historico = (
                supabase.table("vendas")
                .select("id, status_venda(nome), valor_final")
                .in_("id", ids_vendas_historico)
                .execute()
                .data
                or []
            )
            status_por_venda = {v["id"]: v for v in vendas_historico}

        for fechamento in fechamentos:
            fechamento_id = fechamento.get("id")
            comissao = float(fechamento.get("valor_total_comissao") or 0)
            retirada = 0.0
            for vinculo in vinculos_historico:
                if vinculo.get("fechamento_id") != fechamento_id:
                    continue
                venda = status_por_venda.get(vinculo.get("venda_id"), {})
                if (venda.get("status_venda") or {}).get("nome") == "Retirada - Parceiros":
                    retirada += float(venda.get("valor_final") or 0)
            valor_final_por_fechamento[fechamento_id] = comissao - retirada
    except Exception:
        # Se não for possível carregar os vínculos históricos, mantém a
        # comissão como fallback para não impedir a visualização do histórico.
        for fechamento in fechamentos:
            valor_final_por_fechamento[fechamento.get("id")] = float(
                fechamento.get("valor_total_comissao") or 0
            )

    linhas_fechamento = [
        {
            "Ciclo": f["numero_ciclo"],
            "Início": f.get("data_inicio"),
            "Fim": f.get("data_fim") or "-",
            "Status": f.get("status"),
            "Comissão %": f.get("percentual_comissao"),
            "Valor vendas": _fmt_moeda(f.get("valor_total_vendas")),
            "Valor comissão": _fmt_moeda(f.get("valor_total_comissao")),
            "Valor final": _fmt_moeda(valor_final_por_fechamento.get(f.get("id"), 0)),
        }
        for f in fechamentos
    ]

    selecao_historico = st.dataframe(
        linhas_fechamento,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key=f"historico_ciclos_{parceiro_id}",
    )

    # Ao clicar em um ciclo finalizado, abre a mesma visão do fechamento,
    # porém somente para consulta: sem campos editáveis e sem botão de fechar.
    linhas_selecionadas = selecao_historico.selection.rows
    if linhas_selecionadas:
        ciclo_visualizado = fechamentos[linhas_selecionadas[0]]

        try:
            vinculos = (
                supabase.table("parcerias_fechamento_vendas")
                .select("venda_id, valor_venda, percentual_comissao, valor_comissao")
                .eq("fechamento_id", ciclo_visualizado["id"])
                .execute()
                .data
                or []
            )
            ids_vendas = [v["venda_id"] for v in vinculos]

            vendas_fechamento = []
            if ids_vendas:
                vendas_fechamento = (
                    supabase.table("vendas")
                    .select(
                        "id, data_venda, cliente, valor_final, quantidade, "
                        "produtos(descricao, codigo_interno), status_venda(nome)"
                    )
                    .in_("id", ids_vendas)
                    .order("data_venda")
                    .execute()
                    .data
                    or []
                )
        except Exception as e:
            st.error(f"Não foi possível carregar os detalhes do ciclo: {e}")
            return

        vinculo_por_venda = {v["venda_id"]: v for v in vinculos}
        retirada_visualizacao = sum(
            float(v.get("valor_final") or 0)
            for v in vendas_fechamento
            if (v.get("status_venda") or {}).get("nome") == "Retirada - Parceiros"
        )
        comissao_visualizacao = sum(
            float(vinculo_por_venda.get(v["id"], {}).get("valor_comissao") or 0)
            for v in vendas_fechamento
            if (v.get("status_venda") or {}).get("nome") != "Retirada - Parceiros"
        )
        valor_vendas_visualizacao = sum(
            float(v.get("valor_final") or 0) for v in vendas_fechamento
        )
        ajustes_visualizacao = 0.0
        valor_final_visualizacao = comissao_visualizacao - retirada_visualizacao

        st.divider()
        inicio_visualizacao = _parse_data_segura(ciclo_visualizado.get("data_inicio"))
        fim_visualizacao = _parse_data_segura(ciclo_visualizado.get("data_fim"))
        inicio_txt = inicio_visualizacao.strftime("%d/%m/%Y") if inicio_visualizacao else str(ciclo_visualizado.get("data_inicio") or "-")
        fim_txt = fim_visualizacao.strftime("%d/%m/%Y") if fim_visualizacao else str(ciclo_visualizado.get("data_fim") or "-")

        st.markdown(
            f"##### Visualização do ciclo #{ciclo_visualizado['numero_ciclo']} - "
            f"de {inicio_txt} até {fim_txt}"
        )

        resumo_visualizacao = pd.DataFrame([
            {"Descrição": "Vendas no período", "Valor": _fmt_moeda(valor_vendas_visualizacao), "Observação": ""},
            {"Descrição": "Comissão Total (+)", "Valor": _fmt_moeda(comissao_visualizacao), "Observação": ""},
            {"Descrição": "Retirada Peças (-)", "Valor": _fmt_moeda(-retirada_visualizacao), "Observação": "Retirada - Parceiros" if retirada_visualizacao else ""},
            {"Descrição": "Ajustes", "Valor": _fmt_moeda(ajustes_visualizacao), "Observação": ""},
            {"Descrição": "Valor Final", "Valor": _fmt_moeda(valor_final_visualizacao), "Observação": ""},
        ])
        styled_visualizacao = resumo_visualizacao.style.apply(
            lambda row: [
                "font-weight: 700; background-color: rgba(0, 123, 255, 0.16);"
                if row["Descrição"] == "Valor Final" else ""
                for _ in row
            ],
            axis=1,
        )
        st.dataframe(styled_visualizacao, use_container_width=True, hide_index=True)

        linhas_vendas_visualizacao = [
            {
                "Data": v.get("data_venda"),
                "Cliente": v.get("cliente") or "-",
                "Produto": (v.get("produtos") or {}).get("descricao") or "-",
                "Código": (v.get("produtos") or {}).get("codigo_interno") or "-",
                "Qtd": v.get("quantidade"),
                "Valor": float(v.get("valor_final") or 0),
                "Comissão R$": float(vinculo_por_venda.get(v["id"], {}).get("valor_comissao") or 0),
                "Status": (v.get("status_venda") or {}).get("nome") or "-",
            }
            for v in vendas_fechamento
        ]

        if linhas_vendas_visualizacao:
            st.dataframe(
                linhas_vendas_visualizacao,
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("Este ciclo não possui vendas vinculadas.")


def _limpar_estado_parceiro() -> None:
    """Remove do session_state tudo que pertence ao parceiro atualmente selecionado."""
    prefixos = ("parceiros_", "historico_ciclos_", "retirada_auto_obs_")
    for chave in list(st.session_state.keys()):
        if isinstance(chave, str) and chave.startswith(prefixos):
            del st.session_state[chave]


def _desmarcar_parceiro() -> None:
    _limpar_estado_parceiro()
    st.session_state.pop("parceiros_selecionado", None)


def _secao_parceiros():
    try:
        resp = (
            supabase.table("parceiros")
            .select(
                "id, nome, canal_id, telefone, email, percentual_comissao, "
                "observacao, ativo, canais_venda(nome)"
            )
            .eq("ativo", True)
            .order("nome")
            .execute()
        )
        parceiros = resp.data or []
    except Exception as e:
        st.markdown("### 🤝 Parceiros")
        st.error(f"Não foi possível carregar os parceiros: {e}")
        return

    if not parceiros:
        st.markdown("### 🤝 Parceiros")
        st.info(
            "Nenhum parceiro cadastrado ainda. Cadastre um parceiro na aba "
            "**Cadastros Auxiliares** para começar."
        )
        return

    nome_selecionado = st.session_state.get("parceiros_selecionado")
    parceiro = next((p for p in parceiros if p["nome"] == nome_selecionado), None)

    # Parceiro salvo não existe mais (ou nada selecionado): mostra o dropdown.
    if parceiro is None:
        if nome_selecionado:
            _limpar_estado_parceiro()

        st.markdown("### 🤝 Parceiros")
        escolhido = st.selectbox(
            "Selecione um parceiro",
            options=[p["nome"] for p in parceiros],
            index=None,
            placeholder="Escolha um parceiro...",
            key="parceiros_selecao_dropdown",
        )
        if escolhido:
            # Primeira seleção: guarda o parceiro e recarrega sem o dropdown.
            st.session_state["parceiros_selecionado"] = escolhido
            st.rerun()

        st.caption("Selecione um parceiro acima para ver o fechamento e os produtos enviados.")
        return

    # Parceiro selecionado: título com nome, comissão e botão para desmarcar.
    comissao = parceiro.get("percentual_comissao")
    comissao_fmt = f"{float(comissao):g}%" if comissao is not None else "-"
    titulo = f"Parceiros: {parceiro['nome']} (Comissão: {comissao_fmt})"

    st.markdown(
        """
        <style>
        .st-key-parceiros_desmarcar button {
            min-height: 2rem;
            height: 2rem;
            width: 2rem;
            min-width: 2rem;
            padding: 0;
            border-radius: 50%;
            border: 1px solid rgba(220, 53, 69, 0.55);
            color: #dc3545;
            background: transparent;
            font-size: 1rem;
            font-weight: 700;
            line-height: 1;
        }
        .st-key-parceiros_desmarcar button:hover {
            background: #dc3545;
            border-color: #dc3545;
            color: #ffffff;
        }
        .st-key-parceiros_desmarcar button p { margin: 0; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # Título em uma linha só: as colunas desta linha se ajustam ao conteúdo
    # (largura automática), então o ✕ fica colado ao texto, qualquer que seja
    # o tamanho do nome do parceiro.
    st.markdown(
        """
        <style>
        .st-key-parceiros_titulo [data-testid="stHorizontalBlock"] {
            flex-wrap: nowrap !important;
            align-items: center !important;
            gap: 0.75rem !important;
        }
        .st-key-parceiros_titulo [data-testid="stColumn"] {
            width: auto !important;
            flex: 0 0 auto !important;
            min-width: 0 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # Larguras proporcionais só como reserva, caso o CSS acima não se aplique.
    largura_titulo = max(len(titulo) * 1.5, 30)
    espaco = max(100 - largura_titulo - 4, 1)
    with _container_com_key("parceiros_titulo"):
        try:
            col_titulo, col_x, _ = st.columns(
                [largura_titulo, 4, espaco], vertical_alignment="center", gap="small"
            )
        except TypeError:  # Streamlit mais antigo, sem vertical_alignment
            col_titulo, col_x, _ = st.columns([largura_titulo, 4, espaco], gap="small")

        with col_titulo:
            st.markdown(
                "<div style='font-size:1.65rem; font-weight:700; line-height:1.3; "
                f"white-space:nowrap;'>🤝 {html.escape(titulo)}</div>",
                unsafe_allow_html=True,
            )
        with col_x:
            st.button(
                "✕",
                key="parceiros_desmarcar",
                help="Desmarcar parceiro",
                on_click=_desmarcar_parceiro,
            )

    sub_fechamento, sub_produtos = st.tabs(["📑 Fechamento", "📦 Produtos Enviados"])

    with sub_fechamento:
        _secao_parceiro_fechamento(parceiro)

    with sub_produtos:
        _secao_parceiro_produtos_enviados(parceiro)


def tela_vendas():
    st.header("💰 Gestão de Vendas")

    aba_listagem, aba_importar, aba_parceiros, aba_auxiliares = st.tabs(
        ["Vendas registradas", "Importar planilha", "Parceiros", "Cadastros Auxiliares"]
    )

    with aba_listagem:
        _secao_listagem()

    with aba_importar:
        _secao_importar()

    with aba_parceiros:
        _secao_parceiros()

    with aba_auxiliares:
        tela_cadastros_gerais()

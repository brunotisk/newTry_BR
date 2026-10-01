import streamlit as st
import tempfile
import hashlib
import html
import uuid
from io import BytesIO
from datetime import date, datetime, timedelta
from typing import Optional

from db import supabase
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

    if st.session_state.get("venda_controles_chave") != chave_produto:
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


_PREFIXOS_ITEM_VENDA = (
    "venda_item_produto_",
    "venda_item_controles_chave_",
    "venda_item_qtd_",
    "contador_venda_item_",
    "venda_item_valor_lista_",
    "venda_item_valor_lista_aberto_",
    "venda_item_valor_lista_bloqueio_",
    "venda_item_valor_lista_input_",
    "venda_item_desconto_texto_",
)


def _limpar_itens_venda_nova():
    """Remove do session_state a lista de itens da venda nova e todas as
    chaves de widgets de cada linha (produto, quantidade, valores). Chamado
    ao cancelar, ao salvar com sucesso, ou ao remover uma linha — assim o
    próximo "➕ Adicionar venda" sempre abre com uma única linha em branco."""
    for row_uid in st.session_state.pop("venda_itens_ids", []):
        for prefixo in _PREFIXOS_ITEM_VENDA:
            st.session_state.pop(f"{prefixo}{row_uid}", None)


def _render_item_venda(row_uid: str, produtos_opcoes: dict, produtos_excluidos: set) -> dict:
    """Renderiza uma linha 'item' do formulário de nova venda: produto +
    quantidade + valores (mesmos controles de _render_controles_valores_venda,
    só que indexados por `row_uid` para suportar várias linhas simultâneas).

    Retorna sempre um dict com `remover` (bool). Quando `remover` é True, o
    chamador deve excluir esta linha e ignorar os demais campos. Quando um
    produto ainda não foi escolhido nesta linha, os demais campos vêm vazios
    (`produto` é None).
    """
    rotulos_disponiveis = [
        rotulo for rotulo, prod in produtos_opcoes.items()
        if prod["id"] not in produtos_excluidos
    ]
    rotulo_atual = st.session_state.get(f"venda_item_produto_{row_uid}")

    col_produto, col_remover = st.columns([5, 1], vertical_alignment="bottom")
    with col_produto:
        produto_rotulo = st.selectbox(
            "Produto",
            rotulos_disponiveis,
            index=rotulos_disponiveis.index(rotulo_atual) if rotulo_atual in rotulos_disponiveis else None,
            placeholder="Selecione o produto...",
            key=f"venda_item_produto_{row_uid}",
        )
    with col_remover:
        remover = st.button(
            "🗑️", key=f"venda_item_remover_{row_uid}",
            help="Remover este item", use_container_width=True,
        )

    if remover:
        return {"remover": True, "produto": None}

    produto_selecionado = produtos_opcoes.get(produto_rotulo)
    if not produto_selecionado:
        return {"remover": False, "produto": None}

    # Reinicia os controles de quantidade/valor sempre que o produto desta
    # linha muda — mesma lógica de _resetar_controles_venda, indexada por
    # row_uid em vez de uma única chave global.
    chave_item = f"{row_uid}:{produto_selecionado['id']}"
    if st.session_state.get(f"venda_item_controles_chave_{row_uid}") != chave_item:
        st.session_state[f"venda_item_controles_chave_{row_uid}"] = chave_item
        st.session_state[f"venda_item_qtd_{row_uid}"] = 1
        st.session_state[f"venda_item_valor_lista_{row_uid}"] = float(
            produto_selecionado.get("estoque_preco_venda_sugerida") or 0
        )
        st.session_state[f"venda_item_desconto_texto_{row_uid}"] = _fmt_valor_input(0)
        st.session_state[f"venda_item_valor_lista_aberto_{row_uid}"] = False
        st.session_state[f"venda_item_valor_lista_input_{row_uid}"] = _fmt_valor_input(
            st.session_state[f"venda_item_valor_lista_{row_uid}"]
        )

    col_qtd, col_lista, col_desc, col_final = st.columns(4)

    with col_qtd:
        st.markdown("**Quantidade**")
        estado_qtd = contador_quantidade(
            valor=int(max(1.0, float(st.session_state.get(f"venda_item_qtd_{row_uid}", 1)))),
            min_valor=1,
            bloqueado=True,
            label="",
            key=f"contador_venda_item_{row_uid}",
        )
        quantidade = max(
            1, int(estado_qtd.get("valor", st.session_state.get(f"venda_item_qtd_{row_uid}", 1)))
        )
        st.session_state[f"venda_item_qtd_{row_uid}"] = quantidade

    with col_lista:
        col_rotulo, col_bloqueio = st.columns([2, 1], vertical_alignment="center")
        with col_rotulo:
            st.markdown("**Valor lista**")
        with col_bloqueio:
            aberto = st.toggle(
                "🔓",
                value=st.session_state.get(f"venda_item_valor_lista_aberto_{row_uid}", False),
                key=f"venda_item_valor_lista_bloqueio_{row_uid}",
                help=(
                    "Travado: usa o valor sugerido do estoque. "
                    "Ative para digitar um valor lista diferente."
                ),
            )
            st.session_state[f"venda_item_valor_lista_aberto_{row_uid}"] = aberto

        valor_lista_texto = st.text_input(
            "Valor lista",
            key=f"venda_item_valor_lista_input_{row_uid}",
            disabled=not aberto,
            label_visibility="collapsed",
        )
        valor_lista = _parse_valor_input(
            valor_lista_texto, st.session_state.get(f"venda_item_valor_lista_{row_uid}", 0),
        )
        st.session_state[f"venda_item_valor_lista_{row_uid}"] = valor_lista

    with col_desc:
        st.markdown("**Desconto**")
        desconto_texto = st.text_input(
            "Desconto",
            key=f"venda_item_desconto_texto_{row_uid}",
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
            key=f"venda_item_valor_final_display_{row_uid}",
        )

    return {
        "remover": False,
        "produto": produto_selecionado,
        "quantidade": float(quantidade),
        "valor_lista": float(valor_lista),
        "valor_desconto": float(valor_desconto),
        "valor_final": float(valor_final),
    }


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
                .select(
                    "quantidade_atual, estoque_preco_venda_sugerida, "
                    "produtos(id, codigo_interno, descricao)"
                )
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
                    # Mantém junto ao produto o preço de venda sugerido
                    # armazenado na tabela estoque.
                    produtos_opcoes[rotulo] = {
                        **produto,
                        "estoque_preco_venda_sugerida": float(
                            linha.get("estoque_preco_venda_sugerida") or 0
                        ),
                    }
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
    # Na edição, Produto também fica aqui (1/2) já que é uma única linha.
    # Na inclusão, o produto virou "item" (ver abaixo) — cada venda nova
    # pode ter vários produtos, então a linha 1 fica só com Canal | Feira.
        if nova_venda:
            col_canal, col_feira = st.columns([1, 1])
            produto_selecionado = None  # não se aplica mais nesta posição
        else:
            col_produto, col_canal, col_feira = st.columns([2, 1, 1])
            with col_produto:
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

        itens_validos: list[dict] = []
        nomes_clientes = [c.get("nome") for c in clientes_disponiveis if c.get("nome")]

        if nova_venda:
            # ------------------------------------------------------------
            # Header (Cliente | Data | Forma de pagamento | Status) numa
            # única linha, acima dos itens — vale igualmente para todos os
            # produtos desta venda. Fica fora do st.form (assim como
            # canal/feira/itens) porque o form, aqui, carrega só os botões.
            # ------------------------------------------------------------
            col_cliente, col_data, col_forma, col_status = st.columns(4)

            with col_cliente:
                cliente = campo_cliente(
                    "Cliente",
                    value="",
                    opcoes=nomes_clientes,
                    placeholder="Digite ou selecione o cliente...",
                    key="venda_cliente_nova",
                )

            with col_data:
                data_venda_texto = campo_mascarado(
                    "Data da venda",
                    value=data_atual.strftime("%d/%m/%Y"),
                    tipo="data",
                    placeholder="DD/MM/YYYY",
                    key="venda_data_mask_nova",
                )

            with col_forma:
                forma_pagamento_nome = st.selectbox(
                    "Forma de pagamento",
                    [""] + nomes_formas,
                    index=0,
                    placeholder="Selecione...",
                    key="venda_forma_pagamento_nova",
                ) if nomes_formas else None

            with col_status:
                status_nome = st.selectbox(
                    "Status",
                    nomes_status,
                    index=0,
                    key="venda_status_nova",
                ) if nomes_status else None

            st.markdown("##### Itens da venda")

            if not produtos_opcoes:
                st.warning("Nenhum produto com saldo em estoque.")

            ids_itens = st.session_state.setdefault("venda_itens_ids", [str(uuid.uuid4())])

            produtos_ja_usados: set = set()
            ids_para_remover = []

            for row_uid in list(ids_itens):
                item = _render_item_venda(row_uid, produtos_opcoes, produtos_ja_usados)
                if item.get("remover"):
                    ids_para_remover.append(row_uid)
                    continue
                if item.get("produto"):
                    produtos_ja_usados.add(item["produto"]["id"])
                    itens_validos.append(item)
                st.markdown("<hr style='margin:0.4rem 0;'>", unsafe_allow_html=True)

            # Importante: NÃO chamar st.rerun() aqui. Um clique de botão já
            # dispara sozinho um novo rerun do script (comportamento padrão
            # do Streamlit); st.rerun() explícito dentro de um @st.dialog
            # fecha o popup (é assim que Salvar/Cancelar fecham de propósito
            # mais abaixo) — foi isso que fazia a tela "fechar" ao clicar em
            # Adicionar produto e, ao reabrir, mostrar linhas em branco
            # sobrando do session_state.
            if ids_para_remover:
                for row_uid in ids_para_remover:
                    ids_itens.remove(row_uid)
                    for prefixo in _PREFIXOS_ITEM_VENDA:
                        st.session_state.pop(f"{prefixo}{row_uid}", None)
                if not ids_itens:
                    ids_itens.append(str(uuid.uuid4()))

            if st.button("➕ Adicionar produto", key="venda_btn_add_item"):
                ids_itens.append(str(uuid.uuid4()))

            if itens_validos:
                valor_total_itens = sum(i["valor_final"] for i in itens_validos)
                st.caption(
                    f"**{len(itens_validos)} item(ns)** nesta venda · "
                    f"Valor total: **{_fmt_moeda(valor_total_itens)}**"
                )

            st.markdown("<div style='height: 1.25rem;'></div>", unsafe_allow_html=True)

            with st.form(form_key, border=False):
                col_salvar, col_cancelar = st.columns(2)
                salvar = col_salvar.form_submit_button(
                    "💾 Salvar", type="secondary", use_container_width=True
                )
                cancelar = col_cancelar.form_submit_button(
                    "Cancelar", use_container_width=True
                )
                excluir = False

        else:
            _resetar_controles_venda(venda, nova_venda, produto_selecionado)
            quantidade, valor_lista, valor_desconto, valor_final = _render_controles_valores_venda()

            with st.form(form_key, border=False):
                # Linha 3: Cliente (1/2) | Data da venda (1/4) | Forma de pagamento (1/4)
                col_cliente, col_data, col_forma = st.columns([2, 1, 1])

                with col_cliente:
                    cliente = campo_cliente(
                        "Cliente",
                        value=venda.get("cliente") or "",
                        opcoes=nomes_clientes,
                        placeholder="Digite ou selecione o cliente...",
                        key=f"venda_cliente_{venda.get('id')}",
                    )

                with col_data:
                    data_venda_texto = campo_mascarado(
                        "Data da venda",
                        value=data_atual.strftime("%d/%m/%Y"),
                        tipo="data",
                        placeholder="DD/MM/YYYY",
                        key=f"venda_data_mask_{venda.get('id')}",
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
        if nova_venda:
            _limpar_itens_venda_nova()
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

        if nova_venda:
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
                    falhas.append(
                        f"{item['produto'].get('codigo_interno') or item['produto']['id']}: {exc}"
                    )

            if falhas:
                st.warning(
                    f"⚠️ {sucesso} de {len(itens_validos)} item(ns) registrados com sucesso "
                    f"(estoque já atualizado para esses). {len(falhas)} falharam:"
                )
                for falha in falhas:
                    st.caption(f"• {falha}")
                # Mantém no popup só os itens que falharam, para o usuário
                # corrigir e tentar salvar novamente sem perder tudo.
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
        clientes = (
            supabase.table("clientes")
            .select("id, nome")
            .order("nome")
            .execute()
        ).data or []
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

        canais, status, formas, feiras, clientes = _carregar_cadastros_popup()
        _dialog_editar_venda(venda, canais, status, formas, feiras, clientes)
    except Exception as e:
        st.error(f"Erro ao abrir a venda #{venda_id}: {e}")


def _secao_listagem():
    _abrir_venda_pendente()
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
                # Garante que nenhum item de uma venda nova anterior (ex.:
                # popup fechado pelo X em vez de Cancelar) sobre para esta
                # abertura — sempre começa com uma única linha em branco.
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
                " valor_final, data_venda, cliente, detalhe_feira_id,"
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

            col_total.write(_fmt_moeda(v.get("valor_final")))
            col_forma.write((v.get("formas_pagamento") or {}).get("descricao") or "-")
            col_status.write((v.get("status_venda") or {}).get("nome") or "-")
            col_cliente.write(v.get("cliente") or "-")

            if col_edicao.button(
                "✏️",
                key=f"editar_venda_{v['id']}",
                help="Editar ou excluir venda",
                use_container_width=True,
            ):
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
        resp = (
            supabase.table("vendas")
            .select(
                "id, data_venda, cliente, quantidade, valor_final, "
                "comissao_percentual, comissao_valor, "
                "produtos(codigo_interno, descricao), status_venda(nome)"
            )
            .eq("canal_venda_id", canal_id)
            .gte("data_venda", data_ini.isoformat())
            .lte("data_venda", data_fim.isoformat())
            .order("data_venda", desc=True)
            .limit(1000)
            .execute()
        )
        vendas = resp.data or []
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

        st.info(
            f"Ciclo **#{ciclo_aberto['numero_ciclo']}** aberto desde "
            f"**{data_inicio_ciclo.strftime('%d/%m/%Y')}**."
        )

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
                    "produtos(descricao)"
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
            return

        st.caption(
            "Desmarque alguma venda para deixá-la de fora deste ciclo — ela "
            "continua disponível para um fechamento futuro."
        )

        linhas_editor = [
            {
                "Incluir": True,
                "id": v["id"],
                "Data": v.get("data_venda"),
                "Cliente": v.get("cliente") or "-",
                "Produto": (v.get("produtos") or {}).get("descricao") or "-",
                "Valor": float(v.get("valor_final") or 0),
                "Comissão %": v.get("comissao_percentual"),
                "Comissão R$": float(v.get("comissao_valor") or 0),
            }
            for v in candidatas
        ]

        editado = st.data_editor(
            linhas_editor,
            key="parceiros_fechamento_editor",
            use_container_width=True,
            hide_index=True,
            disabled=["id", "Data", "Cliente", "Produto", "Valor", "Comissão %", "Comissão R$"],
            column_order=["Incluir", "Data", "Cliente", "Produto", "Valor", "Comissão %", "Comissão R$"],
        )

        selecionadas = [linha for linha in editado if linha["Incluir"]]
        valor_total = sum(l["Valor"] for l in selecionadas)
        comissao_total = sum(l["Comissão R$"] for l in selecionadas)

        col_t1, col_t2 = st.columns(2)
        col_t1.metric("Selecionadas", f"{len(selecionadas)} de {len(editado)}")
        col_t2.metric(
            "Total a fechar",
            f"{_fmt_moeda(valor_total)}  ·  comissão {_fmt_moeda(comissao_total)}",
        )

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
                    registros.append({
                        "fechamento_id": ciclo_aberto["id"],
                        "venda_id": venda["id"],
                        "valor_venda": float(venda.get("valor_final") or 0),
                        "percentual_comissao": float(venda.get("comissao_percentual") or 0),
                        "valor_comissao": float(venda.get("comissao_valor") or 0),
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

    linhas_fechamento = [
        {
            "Ciclo": f["numero_ciclo"],
            "Início": f.get("data_inicio"),
            "Fim": f.get("data_fim") or "-",
            "Status": f.get("status"),
            "Comissão %": f.get("percentual_comissao"),
            "Valor vendas": _fmt_moeda(f.get("valor_total_vendas")),
            "Valor comissão": _fmt_moeda(f.get("valor_total_comissao")),
        }
        for f in fechamentos
    ]
    st.dataframe(linhas_fechamento, use_container_width=True, hide_index=True)


def _secao_parceiros():
    st.markdown("### 🤝 Parceiros")

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
        st.error(f"Não foi possível carregar os parceiros: {e}")
        return

    if not parceiros:
        st.info(
            "Nenhum parceiro cadastrado ainda. Cadastre um parceiro na aba "
            "**Cadastros Auxiliares** para começar."
        )
        return

    nomes_parceiros = [p["nome"] for p in parceiros]
    nome_selecionado = st.selectbox(
        "Selecione um parceiro",
        options=nomes_parceiros,
        index=None,
        placeholder="Escolha um parceiro...",
        key="parceiros_selecao",
    )

    if not nome_selecionado:
        st.caption("Selecione um parceiro acima para ver o fechamento e os produtos enviados.")
        return

    parceiro = next(p for p in parceiros if p["nome"] == nome_selecionado)

    _render_cabecalho_parceiro(parceiro)

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

"""
Importa vendas a partir de uma planilha Excel (.xlsx) para o Supabase.

Formato esperado da planilha (uma linha por venda, sem cabeçalho):
  Canal | Código Interno | Quantidade | Produto | Valor Lista |
  Desconto | Valor Final | Status | Data | Cliente

Também aceita planilhas COM cabeçalho, desde que os nomes das colunas
sejam reconhecíveis (ver `MAPA_CABECALHO` abaixo). Se o cabeçalho usar
nomes muito diferentes, ajuste o mapa.

Para cada linha:
  1. localiza o produto pelo codigo_interno (precisa já existir em `produtos`)
  2. insere o registro em `vendas`
  3. dá baixa no estoque (tabela `estoque`) e grava o movimento no ledger
     (`estoque_movimentos`, tipo="saida")

Linhas com produto não encontrado, ou já importadas antes (mesmo produto +
data + valor + cliente), são puladas e reportadas no resumo, sem interromper
a importação das demais.
"""
from __future__ import annotations
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO

from openpyxl import Workbook, load_workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Font, PatternFill, Protection
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

import pandas as pd
from supabase import Client

from .estoque import registrar_ajuste, registrar_movimento
from .supabase_admin import get_client  # noqa: F401  (re-exportado por conveniência)


# Ordem das colunas quando a planilha NÃO tem cabeçalho
COLUNAS_SEM_CABECALHO = [
    "canal_venda",
    "codigo_interno",
    "quantidade",
    "descricao_produto",
    "valor_lista",
    "valor_desconto",
    "valor_final",
    "status",
    "data_venda",
    "cliente",
]

# Nomes de cabeçalho reconhecidos -> nome interno da coluna
MAPA_CABECALHO = {
    "canal": "canal_venda",
    "canal de venda": "canal_venda",
    "codigo": "codigo_interno",
    "código": "codigo_interno",
    "codigo interno": "codigo_interno",
    "código interno": "codigo_interno",
    "quantidade": "quantidade",
    "qtd": "quantidade",
    "produto": "descricao_produto",
    "descricao": "descricao_produto",
    "descrição": "descricao_produto",
    "valor unitario": "valor_lista",
    "valor lista": "valor_lista",
    "valor_lista": "valor_lista",
    "valor lista (R$)": "valor_lista",
    "valor venda": "valor_lista",
    "valor_venda": "valor_lista",
    "valor unitário": "valor_lista",
    "desconto": "valor_desconto",
    "valor desconto": "valor_desconto",
    "valor total": "valor_final",
    "valor final": "valor_final",
    "valor_final": "valor_final",
    "valor liquido": "valor_final",
    "valor líquido": "valor_final",
    "data_venda": "data_venda",
    "data venda": "data_venda",
    "nome cliente": "cliente",
    "nome_cliente": "cliente",
    "forma pagamento": "forma_pagamento",
    "forma_pagamento": "forma_pagamento",
    "forma de pagamento": "forma_pagamento",
    "feira": "feira",
    "observação": "observacao",
    "observacao": "observacao",
    "status": "status",
    "data": "data_venda",
    "data da venda": "data_venda",
    "cliente": "cliente",
}

COLUNAS_OBRIGATORIAS = ["codigo_interno", "quantidade", "valor_final", "data_venda"]

# O Excel guarda "0024727" apenas como formatação visual: o valor real salvo
# na célula é o número 24727, sem os zeros à esquerda. No banco, porém,
# produtos.codigo_interno é salvo como texto já com o zero-padding (ex.:
# "0024727", 7 dígitos). Sem repor esses zeros, a busca do produto pelo
# código falha. Ajuste este número se o padrão de código mudar.
TAMANHO_CODIGO_INTERNO = 7


COLUNAS_MODELO_VENDAS = [
    "Canal",
    "Código Interno",
    "Quantidade",
    "Valor Lista",
    "Desconto",
    "Valor Final",
    "Forma Pagamento",
    "Status",
    "Feira",
    "data_venda",
    "Nome Cliente",
    "Observação",
]


# Quantidade de linhas vazias preparadas no modelo. É intencionalmente maior
# que uma importação comum, mas sem deixar o arquivo excessivamente pesado.
LINHAS_MODELO_VENDAS = 500

# Coluna reservada na aba "Dados de Apoio" para o ID único de controle da
# planilha (ver `gerar_planilha_modelo_vendas` e `extrair_id_planilha`).
COLUNA_ID_PLANILHA_APOIO = "L"
TITULO_ID_PLANILHA = "ID da Planilha (não alterar)"


class PlanilhaJaImportadaError(Exception):
    """Levantada quando a planilha enviada (identificada pelo ID gravado na
    aba 'Dados de Apoio' no momento da exportação do modelo) já foi
    importada anteriormente.

    Evita que o mesmo arquivo seja processado duas vezes, mesmo que o
    usuário reenvie exatamente o mesmo .xlsx que já foi importado antes.
    """

    def __init__(self, id_planilha: str, importado_em: str | None = None, arquivo: str | None = None):
        self.id_planilha = id_planilha
        self.importado_em = importado_em
        self.arquivo = arquivo
        partes = ["Esta planilha já foi importada anteriormente"]
        if importado_em:
            partes.append(f"em {str(importado_em)[:19].replace('T', ' ')}")
        if arquivo:
            partes.append(f"(arquivo: {arquivo})")
        mensagem = " ".join(partes) + ". Gere uma nova planilha modelo para importar dados adicionais."
        super().__init__(mensagem)


def _consultar_dados_modelo_vendas(sb: Client) -> dict:
    """Busca os cadastros atuais usados pelo modelo de importação.

    A função consulta somente registros ativos nos cadastros auxiliares.
    Clientes não possuem campo ativo no schema atual, portanto todos são
    disponibilizados. Produtos são cruzados com a tabela estoque para que a
    planilha também possa mostrar o saldo atual no apoio.
    """
    canais = (
        sb.table("canais_venda")
        .select("id, nome")
        .eq("ativo", True)
        .order("nome")
        .execute()
    ).data or []

    formas = (
        sb.table("formas_pagamento")
        .select("id, descricao")
        .eq("ativo", True)
        .order("descricao")
        .execute()
    ).data or []

    status = (
        sb.table("status_venda")
        .select("id, nome")
        .eq("ativo", True)
        .order("nome")
        .execute()
    ).data or []

    feiras = (
        sb.table("detalhes_feira")
        .select("id, nome_feira")
        .eq("ativo", True)
        .order("nome_feira")
        .execute()
    ).data or []

    clientes = (
        sb.table("clientes")
        .select("id, nome")
        .order("nome")
        .execute()
    ).data or []

    produtos = (
        sb.table("produtos")
        .select("id, codigo_interno, descricao")
        .order("codigo_interno")
        .execute()
    ).data or []

    estoques = (
        sb.table("estoque")
        .select("produto_id, quantidade_atual")
        .execute()
    ).data or []
    estoque_por_produto = {
        item["produto_id"]: item.get("quantidade_atual") or 0
        for item in estoques
    }

    produtos_modelo = []
    for produto in produtos:
        produtos_modelo.append({
            "codigo_interno": str(produto.get("codigo_interno") or "").strip(),
            "descricao": produto.get("descricao") or "",
            "estoque": estoque_por_produto.get(produto["id"], 0),
        })

    return {
        "canais": [str(x.get("nome") or "").strip() for x in canais if str(x.get("nome") or "").strip()],
        "formas": [str(x.get("descricao") or "").strip() for x in formas if str(x.get("descricao") or "").strip()],
        "status": [str(x.get("nome") or "").strip() for x in status if str(x.get("nome") or "").strip()],
        "feiras": [str(x.get("nome_feira") or "").strip() for x in feiras if str(x.get("nome_feira") or "").strip()],
        "clientes": [str(x.get("nome") or "").strip() for x in clientes if str(x.get("nome") or "").strip()],
        "produtos": [x for x in produtos_modelo if x["codigo_interno"]],
    }


def _sanitizar_nome_intervalo(valor: str, fallback: str) -> str:
    """Gera um nome válido e estável para um intervalo nomeado do Excel."""
    import re
    nome = re.sub(r"[^A-Za-z0-9_]", "_", str(valor or ""))
    if not nome or not re.match(r"^[A-Za-z_]", nome):
        nome = fallback
    return nome[:200]


def gerar_planilha_modelo_vendas(sb: Client | None = None) -> bytes:
    """Gera o Excel modelo de importação de vendas com dados atuais do banco.

    A aba ``Vendas`` contém as colunas de preenchimento, listas suspensas e
    fórmula para valor líquido. A descrição do produto é recuperada pelo sistema na etapa de validação após o upload. A aba ``Dados de Apoio`` contém os
    cadastros usados nas validações. Clientes ficam livres para novos nomes:
    o cadastro do cliente será tratado pelo importador posteriormente.
    """
    if sb is None:
        # A geração do modelo é usada pela aplicação Streamlit, que já possui
        # o cliente configurado em db.py. Importar aqui evita acoplamento no
        # restante do módulo usado também por scripts de importação.
        from db import supabase as sb_app
        sb = sb_app

    dados = _consultar_dados_modelo_vendas(sb)

    wb = Workbook()
    ws = wb.active
    ws.title = "Vendas"
    apoio = wb.create_sheet("Dados de Apoio")

    # ---------- Aba Vendas ----------
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(COLUNAS_MODELO_VENDAS))}{LINHAS_MODELO_VENDAS + 1}"
    ws.row_dimensions[1].height = 28

    cabecalho_fill = PatternFill(fill_type="solid", fgColor="1F4E78")
    cabecalho_font = Font(color="FFFFFF", bold=True)
    for col_idx, titulo in enumerate(COLUNAS_MODELO_VENDAS, start=1):
        cell = ws.cell(row=1, column=col_idx, value=titulo)
        cell.fill = cabecalho_fill
        cell.font = cabecalho_font
        cell.alignment = Alignment(horizontal="center", vertical="center")

    larguras = {
        "A": 18, "B": 17, "C": 12, "D": 15, "E": 15, "F": 16,
        "G": 20, "H": 16, "I": 24, "J": 15, "K": 30, "L": 35,
    }
    for coluna, largura in larguras.items():
        ws.column_dimensions[coluna].width = largura

    # Fórmulas são colocadas em todas as linhas preparadas. O usuário pode
    # colar somente os dados de entrada e a planilha calcula o restante.
    for row in range(2, LINHAS_MODELO_VENDAS + 2):
        # A descrição não faz mais parte da planilha de entrada. Ela será
        # recuperada pelo sistema na tela de validação após o upload.
        ws.cell(row=row, column=6, value=f'=IF(D{row}="","",D{row}-IF(E{row}="",0,E{row}))')
        for col in (4, 5, 6):
            ws.cell(row=row, column=col).number_format = 'R$ #,##0.00'
        ws.cell(row=row, column=3).number_format = '0'
        ws.cell(row=row, column=10).number_format = 'dd/mm/yyyy'
        ws.cell(row=row, column=2).number_format = '@'
        ws.cell(row=row, column=6).protection = Protection(locked=True)

    # ---------- Aba Dados de Apoio ----------
    apoio.freeze_panes = "A2"
    listas = [
        ("A", "Canais", dados["canais"]),
        ("B", "Formas de Pagamento", dados["formas"]),
        ("C", "Status", dados["status"]),
        ("D", "Feiras", dados["feiras"]),
        ("E", "Clientes cadastrados", dados["clientes"]),
    ]
    for col, titulo, valores in listas:
        apoio[f"{col}1"] = titulo
        apoio[f"{col}1"].fill = cabecalho_fill
        apoio[f"{col}1"].font = cabecalho_font
        apoio[f"{col}1"].alignment = Alignment(horizontal="center")
        for i, valor in enumerate(valores, start=2):
            apoio[f"{col}{i}"] = valor
        apoio.column_dimensions[col].width = max(24, min(45, len(titulo) + 8))

    apoio["F1"] = "Código Interno"
    apoio["G1"] = "Descrição"
    apoio["H1"] = "Estoque Atual"
    for cell in apoio["F1:H1"][0]:
        cell.fill = cabecalho_fill
        cell.font = cabecalho_font
        cell.alignment = Alignment(horizontal="center")
    apoio.column_dimensions["F"].width = 18
    apoio.column_dimensions["G"].width = 55
    apoio.column_dimensions["H"].width = 16
    for i, produto in enumerate(dados["produtos"], start=2):
        apoio[f"F{i}"] = produto["codigo_interno"]
        apoio[f"F{i}"].number_format = '@'
        apoio[f"G{i}"] = produto["descricao"]
        apoio[f"H{i}"] = float(produto["estoque"] or 0)
        apoio[f"H{i}"].number_format = '0.##'

    # ---------- Intervalos nomeados para os dropdowns ----------
    from openpyxl.workbook.defined_name import DefinedName

    def nomear(nome, coluna, quantidade):
        if quantidade <= 0:
            return None
        intervalo = f"'Dados de Apoio'!${coluna}$2:${coluna}${quantidade + 1}"
        definido = DefinedName(nome, attr_text=intervalo)
        try:
            wb.defined_names.add(definido)
        except AttributeError:
            wb.defined_names.append(definido)
        return nome

    nomes = {
        "canais": nomear("ListaCanais", "A", len(dados["canais"])),
        "formas": nomear("ListaFormasPagamento", "B", len(dados["formas"])),
        "status": nomear("ListaStatusVenda", "C", len(dados["status"])),
        "feiras": nomear("ListaFeiras", "D", len(dados["feiras"])),
        "clientes": nomear("ListaClientes", "E", len(dados["clientes"])),
        "produtos": nomear("ListaCodigosProduto", "F", len(dados["produtos"])),
    }

    # ---------- Validações de dados ----------
    def adicionar_dropdown(coluna, nome_intervalo, allow_blank=True, mostrar_erro=True):
        if not nome_intervalo:
            return
        dv = DataValidation(
            type="list",
            formula1=f"={nome_intervalo}",
            allow_blank=allow_blank,
        )
        dv.errorTitle = "Valor não cadastrado"
        dv.error = "Selecione uma opção da lista ou corrija o valor informado."
        dv.promptTitle = "Selecione uma opção"
        dv.prompt = "Use a lista para escolher um valor cadastrado."
        dv.showErrorMessage = mostrar_erro
        dv.showInputMessage = True
        ws.add_data_validation(dv)
        dv.add(f"{coluna}2:{coluna}{LINHAS_MODELO_VENDAS + 1}")

    adicionar_dropdown("A", nomes["canais"])
    adicionar_dropdown("B", nomes["produtos"])
    # Clientes ficam livres para digitação de novos nomes; o dropdown serve
    # como atalho para os já cadastrados, sem bloquear novos clientes.
    adicionar_dropdown("K", nomes["clientes"], mostrar_erro=False)
    adicionar_dropdown("G", nomes["formas"])
    adicionar_dropdown("H", nomes["status"])
    adicionar_dropdown("I", nomes["feiras"])

    # ---------- Destaque vermelho para valores sem correspondência ----------
    vermelho_font = Font(color="FF0000", bold=True)

    def destacar_sem_match(coluna, coluna_apoio, quantidade, normalizar_codigo=False):
        """Destaca a célula inteira quando o valor não existe no apoio.

        Usa o intervalo físico da aba de apoio em vez de intervalo nomeado,
        pois isso é mais compatível com a formatação condicional do Excel.
        Para código interno, a comparação normaliza números e textos para
        o padrão de 7 dígitos.
        """
        if quantidade <= 0:
            return
        ultima_linha = quantidade + 1
        intervalo = f"'Dados de Apoio'!${coluna_apoio}$2:${coluna_apoio}${ultima_linha}"
        if normalizar_codigo:
            valor_normalizado = f'IFERROR(TEXT(VALUE(${coluna}2),"0000000"),${coluna}2)'
            formula = f'=AND(${coluna}2<>"",COUNTIF({intervalo},{valor_normalizado})=0)'
        else:
            formula = f'=AND(${coluna}2<>"",COUNTIF({intervalo},${coluna}2)=0)'
        regra = FormulaRule(
            formula=[formula],
            font=vermelho_font,
        )
        ws.conditional_formatting.add(
            f"${coluna}$2:${coluna}${LINHAS_MODELO_VENDAS + 1}", regra
        )

    destacar_sem_match("A", "A", len(dados["canais"]))
    destacar_sem_match("B", "F", len(dados["produtos"]), normalizar_codigo=True)
    destacar_sem_match("G", "B", len(dados["formas"]))
    destacar_sem_match("H", "C", len(dados["status"]))
    destacar_sem_match("I", "D", len(dados["feiras"]))

    # Cliente propositalmente não recebe marcação vermelha: nomes novos são
    # permitidos e serão cadastrados pelo importador.

    # Valores de apoio podem ser consultados pelo usuário, mas a aba não deve
    # ser alterada para não quebrar as listas do modelo.
    for row in apoio.iter_rows():
        for cell in row:
            cell.protection = Protection(locked=True)

    # Uma observação curta no topo da aba de apoio ajuda a explicar a regra
    # especial de clientes sem ocupar espaço na aba de vendas.
    apoio["J1"] = "Observação"
    apoio["J1"].fill = cabecalho_fill
    apoio["J1"].font = cabecalho_font
    apoio["J2"] = "Clientes podem ser novos: basta digitar o nome. Os demais campos de cadastro devem usar as opções existentes."
    apoio["J2"].alignment = Alignment(wrap_text=True, vertical="top")
    apoio.column_dimensions["J"].width = 55
    apoio.row_dimensions[2].height = 45

    # ---------- ID único de controle da planilha ----------
    # Gerado a cada exportação do modelo. Ao importar, o sistema confere se
    # esse ID já foi registrado como importado (tabela `planilhas_importadas`)
    # e bloqueia a reimportação do mesmo arquivo. Fica em coluna oculta,
    # apenas para controle interno — não é destinado ao preenchimento.
    id_planilha = str(uuid.uuid4())
    celula_titulo = apoio[f"{COLUNA_ID_PLANILHA_APOIO}1"]
    celula_titulo.value = TITULO_ID_PLANILHA
    celula_titulo.fill = cabecalho_fill
    celula_titulo.font = cabecalho_font
    celula_titulo.alignment = Alignment(horizontal="center")
    apoio[f"{COLUNA_ID_PLANILHA_APOIO}2"] = id_planilha
    apoio.column_dimensions[COLUNA_ID_PLANILHA_APOIO].width = 38
    apoio.column_dimensions[COLUNA_ID_PLANILHA_APOIO].hidden = True

    # A aba fica visível nesta primeira versão para facilitar o teste. Depois
    # podemos ocultá-la/protegê-la se esse for o comportamento desejado.
    apoio.sheet_view.showGridLines = False
    ws.sheet_view.showGridLines = False

    # Texto explicativo simples no comentário do cabeçalho, sem interferir no
    # formato que o importador vai ler.
    from openpyxl.comments import Comment
    ws["A1"].comment = Comment("Use os dropdowns sempre que possível. Células em vermelho indicam valores sem correspondência no cadastro atual.", "Sistema")
    ws["K1"].comment = Comment("Cliente pode ser novo. Nesse caso, digite o nome; o importador criará o cadastro posteriormente.", "Sistema")

    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def normalizar_codigo_interno(valor) -> str:
    """Normaliza um código interno lido da planilha (ou digitado manualmente)
    para o mesmo formato armazenado no banco: string numérica com zeros à
    esquerda até TAMANHO_CODIGO_INTERNO dígitos.

    Códigos não puramente numéricos (ex.: com letras) são apenas limpos de
    espaços, sem receber padding.
    """
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return ""

    texto = str(valor).strip()

    # Remove ".0" que aparece quando o pandas lê um código inteiro como float
    if texto.endswith(".0"):
        texto = texto[:-2]

    if texto.isdigit():
        texto = texto.zfill(TAMANHO_CODIGO_INTERNO)

    return texto


def _parse_moeda(valor) -> Decimal:
    """Converte 'R$ 79,90', '79,90', 79.9, NaN etc. para Decimal."""
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return Decimal("0")
    if isinstance(valor, (int, float, Decimal)):
        return Decimal(str(valor))
    texto = str(valor).strip().replace("R$", "").strip()
    texto = texto.replace(".", "").replace(",", ".")
    try:
        return Decimal(texto)
    except InvalidOperation:
        return Decimal("0")


def _parse_data(valor) -> str:
    """Converte datas em vários formatos (incluindo datetime do Excel) para 'YYYY-MM-DD'."""
    if isinstance(valor, datetime):
        return valor.date().isoformat()
    if hasattr(valor, "isoformat"):
        return valor.isoformat()
    texto = str(valor).strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"Data em formato não reconhecido: {valor!r}")


def ler_planilha(caminho_arquivo) -> pd.DataFrame:
    """Lê a planilha de vendas e devolve um DataFrame já normalizado
    (colunas com os nomes internos, prontos para importar_vendas_excel)."""
    bruto = pd.read_excel(caminho_arquivo, header=None, nrows=1)
    primeira_linha = " ".join(bruto.iloc[0].astype(str).str.lower().tolist())
    tem_cabecalho = any(
        chave in primeira_linha for chave in ["código", "codigo", "produto", "cliente", "canal"]
    )

    if tem_cabecalho:
        df = pd.read_excel(caminho_arquivo, header=0)
        df.columns = [
            MAPA_CABECALHO.get(str(c).strip().lower(), str(c).strip().lower())
            for c in df.columns
        ]
        faltando = [c for c in COLUNAS_OBRIGATORIAS if c not in df.columns]
        if faltando:
            raise ValueError(
                f"Colunas obrigatórias não encontradas no cabeçalho da planilha: {faltando}. "
                f"Ajuste MAPA_CABECALHO em vendas_import.py se os nomes da sua planilha forem diferentes."
            )
    else:
        df = pd.read_excel(caminho_arquivo, header=None)
        if df.shape[1] < len(COLUNAS_SEM_CABECALHO):
            raise ValueError(
                f"A planilha tem {df.shape[1]} coluna(s); eram esperadas ao menos "
                f"{len(COLUNAS_SEM_CABECALHO)} (na ordem: {', '.join(COLUNAS_SEM_CABECALHO)})."
            )
        df = df.iloc[:, : len(COLUNAS_SEM_CABECALHO)]
        df.columns = COLUNAS_SEM_CABECALHO

    df = df.dropna(how="all").reset_index(drop=True)
    df["codigo_interno"] = df["codigo_interno"].apply(normalizar_codigo_interno)
    return df


def extrair_id_planilha(caminho_arquivo) -> str | None:
    """Lê o ID de controle gravado pelo `gerar_planilha_modelo_vendas` na aba
    'Dados de Apoio'. Retorna None quando a planilha não tem esse ID (ex.:
    arquivo sem cabeçalho no formato legado, aba de apoio apagada pelo
    usuário, ou modelo gerado antes desta trava existir) — nesse caso a
    checagem de reimportação simplesmente não se aplica a essa planilha.
    """
    try:
        wb = load_workbook(caminho_arquivo, data_only=True, read_only=True)
    except Exception:
        return None

    if "Dados de Apoio" not in wb.sheetnames:
        return None

    apoio = wb["Dados de Apoio"]
    coluna_id = None
    try:
        primeira_linha = next(apoio.iter_rows(min_row=1, max_row=1))
    except StopIteration:
        return None
    for cell in primeira_linha:
        titulo = str(cell.value or "").strip().lower()
        if titulo.startswith("id da planilha"):
            coluna_id = cell.column
            break
    if coluna_id is None:
        return None

    valor = apoio.cell(row=2, column=coluna_id).value
    valor = str(valor).strip() if valor else ""
    return valor or None


def buscar_produto_id(sb: Client, codigo_interno: str) -> int | None:
    resp = (
        sb.table("produtos")
        .select("id")
        .eq("codigo_interno", str(codigo_interno).strip())
        .execute()
    )
    return resp.data[0]["id"] if resp.data else None


def venda_ja_existe(sb: Client, produto_id: int, data_venda: str, valor_final: float, cliente: str) -> bool:
    """Checagem de duplicidade: mesma combinação de produto + data + valor + cliente.
    A planilha não tem um identificador único por linha, então essa é uma
    aproximação razoável para evitar reimportar a mesma venda duas vezes."""
    resp = (
        sb.table("vendas")
        .select("id")
        .eq("produto_id", produto_id)
        .eq("data_venda", data_venda)
        .eq("valor_final", valor_final)
        .eq("cliente", cliente or "")
        .execute()
    )
    return len(resp.data) > 0


def planilha_ja_importada(sb: Client, id_planilha: str) -> dict | None:
    """Verifica na tabela `planilhas_importadas` se este ID de planilha já
    foi registrado como importado. Retorna o registro (com data e nome do
    arquivo, quando disponíveis) ou None se ainda não foi importada.

    Requer a tabela `planilhas_importadas` (id_planilha texto único,
    importado_em timestamp, arquivo texto, total_linhas inteiro).
    """
    if not id_planilha:
        return None
    resp = (
        sb.table("planilhas_importadas")
        .select("id_planilha, importado_em, arquivo")
        .eq("id_planilha", id_planilha)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


def registrar_planilha_importada(
    sb: Client,
    id_planilha: str,
    arquivo: str | None = None,
    total_linhas: int | None = None,
) -> None:
    """Registra o ID da planilha como já importado, para bloquear reimportações
    futuras do mesmo arquivo. Chamada só depois que a importação já gravou
    ao menos uma venda com sucesso.
    """
    if not id_planilha:
        return
    try:
        sb.table("planilhas_importadas").insert({
            "id_planilha": id_planilha,
            "arquivo": arquivo,
            "total_linhas": total_linhas,
        }).execute()
    except Exception:
        # Se o registro já existir (ex.: duas abas importando a mesma
        # planilha quase ao mesmo tempo), a restrição de unicidade no banco
        # já garante o bloqueio na próxima tentativa; não há necessidade de
        # interromper uma importação que já foi concluída por causa disso.
        pass


def obter_ou_criar_lista_id(sb: Client, tabela: str, nome: str) -> int:
    """Busca o id de um item em canais_venda/status_venda pelo nome; cadastra
    automaticamente se ainda não existir (ex.: a planilha traz um canal ou
    status que ainda não foi cadastrado na tela de vendas)."""
    nome = (nome or "").strip()
    if not nome:
        raise ValueError(f"Valor vazio para '{tabela}'.")

    resp = sb.table(tabela).select("id").eq("nome", nome).execute()
    if resp.data:
        return resp.data[0]["id"]

    criado = sb.table(tabela).insert({"nome": nome}).execute()
    return criado.data[0]["id"]


def inserir_venda_e_baixar_estoque(
    sb: Client,
    linha: dict,
    produto_id: int,
    motivo_saida: str = "Venda Manual",
) -> int:
    """Insere uma venda e registra sua saída no estoque/ledger.

    Esta é a rotina única usada tanto pela importação de Excel quanto pelo
    cadastro manual da tela de vendas. Os campos novos de forma de pagamento
    e feira são opcionais para manter a importação de planilhas antigas
    compatível; quando informados, ficam gravados na venda.
    """
    dados_venda = {
        "produto_id": produto_id,
        "canal_venda_id": linha["canal_venda_id"],
        "quantidade": float(linha["quantidade"]),
        "valor_lista": float(linha["valor_lista"]),
        "valor_desconto": float(linha["valor_desconto"]),
        "valor_final": float(linha["valor_final"]),
        "status_id": linha["status_id"],
        "data_venda": linha["data_venda"],
        "cliente": linha.get("cliente") or "",
    }

    # Compatibilidade com vendas antigas/importações que não possuem esses
    # dados. A tela manual passa os dois campos quando aplicável.
    if linha.get("forma_pagamento_id") is not None:
        dados_venda["forma_pagamento_id"] = linha["forma_pagamento_id"]
    if linha.get("detalhe_feira_id") is not None:
        dados_venda["detalhe_feira_id"] = linha["detalhe_feira_id"]
    if linha.get("observacao") is not None:
        dados_venda["observacao"] = linha.get("observacao") or ""

    venda_resp = sb.table("vendas").insert(dados_venda).execute()
    venda_id = venda_resp.data[0]["id"]

    quantidade = float(linha["quantidade"])

    # Antes, a atualização de saldo + inserção no ledger estava duplicada
    # aqui (era idêntica à de compras_import.py e estoque_ajuste.py). Agora
    # é uma única implementação em servicos/estoque.py.
    registrar_movimento(
        sb,
        produto_id=produto_id,
        quantidade=-quantidade,
        tipo="saida",
        data_movimento=linha["data_venda"],
        motivo=motivo_saida,
        venda_id=venda_id,
    )

    return venda_id


def editar_venda(sb: Client, venda_antiga: dict, dados_novos: dict) -> None:
    """Atualiza os campos de uma venda existente e ajusta o estoque de acordo
    com a diferença entre a quantidade antiga e a nova.

    `venda_antiga` precisa conter ao menos `id`, `produto_id` e `quantidade`
    (os valores como estavam antes da edição).
    `dados_novos` deve conter todos os campos finais a gravar em `vendas`
    (incluindo `produto_id`, `canal_venda_id`, `status_id` e `quantidade`).
    """
    venda_id = venda_antiga["id"]
    produto_id_antigo = venda_antiga["produto_id"]
    quantidade_antiga = float(venda_antiga.get("quantidade") or 0)

    produto_id_novo = dados_novos["produto_id"]
    quantidade_nova = float(dados_novos["quantidade"])
    hoje = date.today().isoformat()

    if produto_id_novo != produto_id_antigo:
        # devolve a quantidade antiga ao estoque do produto antigo
        registrar_ajuste(
            sb,
            produto_id=produto_id_antigo,
            quantidade=quantidade_antiga,
            motivo=f"Estorno por edição da venda #{venda_id} (produto alterado)",
            data_movimento=hoje,
        )
        # dá baixa da quantidade nova no estoque do produto novo
        registrar_ajuste(
            sb,
            produto_id=produto_id_novo,
            quantidade=-quantidade_nova,
            motivo=f"Ajuste por edição da venda #{venda_id} (produto alterado)",
            data_movimento=hoje,
        )
    else:
        # mesmo produto: só a diferença de quantidade precisa ser ajustada
        delta = quantidade_antiga - quantidade_nova
        if delta != 0:
            registrar_ajuste(
                sb,
                produto_id=produto_id_novo,
                quantidade=delta,
                motivo=f"Ajuste por edição da venda #{venda_id} (quantidade alterada)",
                data_movimento=hoje,
            )

    sb.table("vendas").update(dados_novos).eq("id", venda_id).execute()


def excluir_venda(sb: Client, venda: dict) -> None:
    """Exclui uma venda e devolve a quantidade correspondente ao estoque do
    produto, registrando o estorno no ledger de movimentações.

    As movimentações de saída originais são mantidas no histórico, mas têm o
    vínculo com a venda removido antes da exclusão. Isso é necessário porque
    ``estoque_movimentos.venda_id`` possui uma chave estrangeira para
    ``vendas.id``. O estorno é identificado pelo motivo para que uma nova
    tentativa após uma falha não devolva o estoque duas vezes.

    `venda` precisa conter `id`, `produto_id` e `quantidade`.
    """
    venda_id = venda["id"]
    produto_id = venda["produto_id"]
    quantidade = float(venda.get("quantidade") or 0)

    motivo_estorno = f"Estorno da venda #{venda_id} (exclusão)"
    estorno_existente = (
        sb.table("estoque_movimentos")
        .select("id")
        .eq("produto_id", produto_id)
        .eq("tipo", "ajuste")
        .eq("motivo", motivo_estorno)
        .limit(1)
        .execute()
    )
    if not estorno_existente.data:
        registrar_ajuste(
            sb,
            produto_id=produto_id,
            quantidade=quantidade,
            motivo=motivo_estorno,
            data_movimento=date.today().isoformat(),
        )

    # Preserva o histórico de estoque, mas elimina a referência que impediria
    # a exclusão da venda pela chave estrangeira.
    sb.table("estoque_movimentos").update({"venda_id": None}).eq(
        "venda_id", venda_id
    ).execute()
    sb.table("vendas").delete().eq("id", venda_id).execute()



def _texto_limpo(valor) -> str:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return ""
    return str(valor).strip()


def _normalizar_nome(valor) -> str:
    return " ".join(_texto_limpo(valor).casefold().split())


def consultar_dados_validacao_vendas(sb: Client) -> dict:
    """Carrega os cadastros oficiais usados na tela intermediária."""
    canais = (sb.table("canais_venda").select("id, nome").eq("ativo", True).order("nome").execute()).data or []
    formas = (sb.table("formas_pagamento").select("id, descricao").eq("ativo", True).order("descricao").execute()).data or []
    status = (sb.table("status_venda").select("id, nome").eq("ativo", True).order("nome").execute()).data or []
    feiras = (sb.table("detalhes_feira").select("id, nome_feira").eq("ativo", True).order("nome_feira").execute()).data or []
    clientes = (sb.table("clientes").select("id, nome").order("nome").execute()).data or []
    produtos = (sb.table("produtos").select("id, codigo_interno, descricao").order("codigo_interno").execute()).data or []
    estoques = (sb.table("estoque").select("produto_id, quantidade_atual").execute()).data or []

    estoque_por_produto = {x["produto_id"]: x.get("quantidade_atual") or 0 for x in estoques}
    produtos_por_codigo = {}
    for p in produtos:
        codigo = normalizar_codigo_interno(p.get("codigo_interno"))
        if codigo:
            produtos_por_codigo[codigo] = {
                "id": p["id"],
                "codigo_interno": codigo,
                "descricao": p.get("descricao") or "",
                "estoque": float(estoque_por_produto.get(p["id"], 0) or 0),
            }

    return {
        "canais": canais,
        "formas": formas,
        "status": status,
        "feiras": feiras,
        "clientes": clientes,
        "produtos": produtos_por_codigo,
    }


def validar_planilha_vendas(caminho_arquivo, sb: Client | None = None) -> dict:
    """Valida a planilha contra o banco sem alterar nenhum dado.

    Erros de cadastro bloqueiam o avanço e podem ser corrigidos pelos
    dropdowns da tela. Produto inexistente e estoque insuficiente são
    situações não importáveis: ficam disponíveis para download e suas linhas
    são simplesmente excluídas do lote que será importado.
    Clientes novos são aceitos e marcados para criação.
    """
    if sb is None:
        sb = get_client()
    df = ler_planilha(caminho_arquivo)

    # Trava de reimportação: bloqueia antes mesmo de rodar toda a validação
    # linha a linha, já que se a planilha inteira já foi importada não há
    # motivo para prosseguir.
    id_planilha = extrair_id_planilha(caminho_arquivo)
    if id_planilha:
        registro_existente = planilha_ja_importada(sb, id_planilha)
        if registro_existente:
            raise PlanilhaJaImportadaError(
                id_planilha,
                importado_em=registro_existente.get("importado_em"),
                arquivo=registro_existente.get("arquivo"),
            )

    dados = consultar_dados_validacao_vendas(sb)

    mapas = {
        "canal_venda": {_normalizar_nome(x["nome"]): x for x in dados["canais"]},
        "forma_pagamento": {_normalizar_nome(x["descricao"]): x for x in dados["formas"]},
        "status": {_normalizar_nome(x["nome"]): x for x in dados["status"]},
        "feira": {_normalizar_nome(x["nome_feira"]): x for x in dados["feiras"]},
        "cliente": {_normalizar_nome(x["nome"]): x for x in dados["clientes"]},
    }

    resultados = []
    consumo_estoque = {}
    for idx, row in df.iterrows():
        numero_linha = idx + 2
        codigo = normalizar_codigo_interno(row.get("codigo_interno"))
        produto = dados["produtos"].get(codigo)
        canal = _texto_limpo(row.get("canal_venda"))
        forma = _texto_limpo(row.get("forma_pagamento"))
        status = _texto_limpo(row.get("status")) or "Pendente"
        feira = _texto_limpo(row.get("feira"))
        cliente = _texto_limpo(row.get("cliente"))
        erros = []
        campos_erro = set()
        motivos_nao_importar = []

        if not codigo:
            erros.append("Código interno vazio")
            campos_erro.add("codigo_interno")
        elif produto is None:
            motivos_nao_importar.append("Produto não cadastrado")

        canal_item = mapas["canal_venda"].get(_normalizar_nome(canal))
        if not canal:
            erros.append("Canal de venda vazio")
            campos_erro.add("canal_venda")
        elif canal_item is None:
            erros.append(f"Canal '{canal}' não cadastrado")
            campos_erro.add("canal_venda")

        forma_item = mapas["forma_pagamento"].get(_normalizar_nome(forma))
        if not forma:
            erros.append("Forma de pagamento vazia")
            campos_erro.add("forma_pagamento")
        elif forma_item is None:
            erros.append(f"Forma de pagamento '{forma}' não cadastrada")
            campos_erro.add("forma_pagamento")

        status_item = mapas["status"].get(_normalizar_nome(status))
        if status_item is None:
            erros.append(f"Status '{status}' não cadastrado")
            campos_erro.add("status")

        canal_eh_feira = _normalizar_nome(canal) == "feira"
        feira_item = None
        if canal_eh_feira:
            if not feira:
                erros.append("Canal Feira exige uma feira")
                campos_erro.add("feira")
            else:
                feira_item = mapas["feira"].get(_normalizar_nome(feira))
                if feira_item is None:
                    erros.append(f"Feira '{feira}' não cadastrada")
                    campos_erro.add("feira")
        elif feira:
            feira = ""

        cliente_item = mapas["cliente"].get(_normalizar_nome(cliente)) if cliente else None
        cliente_novo = bool(cliente) and cliente_item is None

        quantidade = None
        try:
            quantidade = Decimal(str(row.get("quantidade")))
            if quantidade <= 0:
                raise ValueError
        except Exception:
            erros.append("Quantidade inválida")
            campos_erro.add("quantidade")

        try:
            data_venda = _parse_data(row.get("data_venda"))
        except Exception as exc:
            data_venda = ""
            erros.append(str(exc))
            campos_erro.add("data_venda")

        valor_lista = _parse_moeda(row.get("valor_lista"))
        valor_desconto = _parse_moeda(row.get("valor_desconto"))
        valor_final = _parse_moeda(row.get("valor_final"))

        duplicada = False
        if produto is not None and data_venda and valor_final is not None:
            try:
                duplicada = venda_ja_existe(sb, produto["id"], data_venda, float(valor_final), cliente)
            except Exception:
                duplicada = False

        estoque_insuficiente = False
        if produto is not None and quantidade is not None and not duplicada:
            estoque = float(produto["estoque"] or 0)
            consumido = consumo_estoque.get(produto["id"], 0.0)
            disponivel = estoque - consumido
            if float(quantidade) > disponivel:
                estoque_insuficiente = True
                motivos_nao_importar.append(
                    f"Estoque insuficiente (disponível {disponivel:g}, informado {float(quantidade):g})"
                )
            else:
                consumo_estoque[produto["id"]] = consumido + float(quantidade)

        if duplicada:
            motivos_nao_importar.append("Venda já cadastrada")

        if produto is None:
            status_linha = "PRODUTO_NAO_CADASTRADO"
        elif estoque_insuficiente:
            status_linha = "ESTOQUE_INSUFICIENTE"
        elif campos_erro:
            status_linha = "ERRO"
        elif duplicada:
            status_linha = "DUPLICADA"
        elif cliente_novo:
            status_linha = "CLIENTE_NOVO"
        else:
            status_linha = "OK"

        resultados.append({
            "linha": numero_linha,
            "canal_venda": canal,
            "codigo_interno": codigo,
            "quantidade": float(quantidade) if quantidade is not None else None,
            "descricao_produto": produto["descricao"] if produto else _texto_limpo(row.get("descricao_produto")),
            "valor_lista": float(valor_lista),
            "valor_desconto": float(valor_desconto),
            "valor_final": float(valor_final),
            "forma_pagamento": forma,
            "status": status,
            "feira": feira,
            "data_venda": data_venda,
            "cliente": cliente,
            "observacao": _texto_limpo(row.get("observacao")),
            "produto_id": produto["id"] if produto else None,
            "estoque_atual": produto["estoque"] if produto else None,
            "estoque_insuficiente": estoque_insuficiente,
            "cliente_novo": cliente_novo,
            "duplicada": duplicada,
            "campos_erro": sorted(campos_erro),
            "erros": erros,
            "motivos_nao_importar": motivos_nao_importar,
            "status_linha": status_linha,
            "canal_id": canal_item["id"] if canal_item else None,
            "forma_pagamento_id": forma_item["id"] if forma_item else None,
            "status_id": status_item["id"] if status_item else None,
            "detalhe_feira_id": feira_item["id"] if feira_item else None,
        })

    bloqueios = sum(1 for x in resultados if x["status_linha"] == "ERRO")
    nao_importaveis = sum(1 for x in resultados if x["motivos_nao_importar"])
    return {
        "linhas": resultados,
        "cadastros": dados,
        "id_planilha": id_planilha,
        "total": len(resultados),
        "erros": bloqueios,
        "produtos_nao_cadastrados": sum(1 for x in resultados if x["status_linha"] == "PRODUTO_NAO_CADASTRADO"),
        "estoque_insuficiente": sum(1 for x in resultados if x["status_linha"] == "ESTOQUE_INSUFICIENTE"),
        "nao_importaveis": nao_importaveis,
        "clientes_novos": sum(1 for x in resultados if x["cliente_novo"]),
        "duplicadas": sum(1 for x in resultados if x["duplicada"]),
        "ok": sum(1 for x in resultados if x["status_linha"] in {"OK", "CLIENTE_NOVO"}),
    }


def importar_linhas_validadas(
    linhas: list[dict],
    sb: Client | None = None,
    id_planilha: str | None = None,
    nome_arquivo: str | None = None,
) -> dict:
    """Grava somente as linhas liberadas pela tela de validação.

    Produtos sem cadastro, estoque insuficiente, duplicadas e erros de
    cadastro nunca entram neste lote. Clientes novos são criados uma única
    vez e a venda guarda o nome, compatível com o schema atual.

    Quando `id_planilha` é informado (vindo de `validar_planilha_vendas`) e
    ao menos uma venda é gravada com sucesso, o ID é registrado em
    `planilhas_importadas` para bloquear uma futura reimportação do mesmo
    arquivo.
    """
    if sb is None:
        sb = get_client()

    importadas = 0
    clientes_criados = 0
    erros = []
    clientes_cache = {}

    for item in linhas:
        if item.get("status_linha") not in {"OK", "CLIENTE_NOVO"}:
            continue
        if item.get("erros") or item.get("motivos_nao_importar"):
            continue
        try:
            cliente = _texto_limpo(item.get("cliente"))
            if cliente:
                chave = _normalizar_nome(cliente)
                if chave not in clientes_cache:
                    existente = sb.table("clientes").select("id, nome").eq("nome", cliente).execute().data or []
                    if existente:
                        clientes_cache[chave] = existente[0]["id"]
                    else:
                        # Cliente novo: além do nome, já grava o canal de
                        # venda desta linha em `clientes.canal_id`, registrando
                        # por qual canal esse cliente entrou pela primeira vez.
                        criado = sb.table("clientes").insert({
                            "nome": cliente,
                            "canal_id": item.get("canal_id"),
                        }).execute()
                        clientes_cache[chave] = criado.data[0]["id"]
                        clientes_criados += 1

            linha = {
                "canal_venda_id": item["canal_id"],
                "status_id": item["status_id"],
                "forma_pagamento_id": item["forma_pagamento_id"],
                "detalhe_feira_id": item["detalhe_feira_id"],
                "quantidade": Decimal(str(item["quantidade"])),
                "valor_lista": Decimal(str(item["valor_lista"])),
                "valor_desconto": Decimal(str(item["valor_desconto"])),
                "valor_final": Decimal(str(item["valor_final"])),
                "data_venda": item["data_venda"],
                "cliente": cliente,
                "observacao": item.get("observacao") or "",
            }
            inserir_venda_e_baixar_estoque(
                sb, linha, item["produto_id"], motivo_saida="Importação XLSX"
            )
            importadas += 1
        except Exception as exc:
            erros.append(f"Linha {item.get('linha')}: {exc}")

    if id_planilha and importadas > 0:
        registrar_planilha_importada(sb, id_planilha, arquivo=nome_arquivo, total_linhas=importadas)

    return {
        "importadas": importadas,
        "clientes_criados": clientes_criados,
        "erros": erros,
        "id_planilha": id_planilha,
    }

def preparar_linhas_corrigidas_validacao(linhas: list[dict]) -> list[dict]:
    """Converte o resultado da validação em linhas compatíveis com a rotina de importação."""
    prontas = []
    for item in linhas:
        if item["status_linha"] == "PRODUTO_NAO_CADASTRADO" or item["erros"]:
            continue
        if item["duplicada"]:
            continue
        prontas.append({
            "produto_id": item["produto_id"],
            "canal_venda_id": item["canal_id"],
            "status_id": item["status_id"],
            "forma_pagamento_id": item["forma_pagamento_id"],
            "detalhe_feira_id": item["detalhe_feira_id"],
            "quantidade": Decimal(str(item["quantidade"])),
            "valor_lista": Decimal(str(item["valor_lista"])),
            "valor_desconto": Decimal(str(item["valor_desconto"])),
            "valor_final": Decimal(str(item["valor_final"])),
            "data_venda": item["data_venda"],
            "cliente": item["cliente"],
            "observacao": item["observacao"],
        })
    return prontas

def importar_vendas_excel(caminho_arquivo) -> dict:
    """Importa todas as linhas válidas de uma planilha de vendas.
    Não interrompe no primeiro erro: cada linha é processada de forma
    independente e o resumo final lista o que foi importado, ignorado
    (duplicado) ou deu erro."""
    df = ler_planilha(caminho_arquivo)
    sb = get_client()

    id_planilha = extrair_id_planilha(caminho_arquivo)
    if id_planilha:
        registro_existente = planilha_ja_importada(sb, id_planilha)
        if registro_existente:
            raise PlanilhaJaImportadaError(
                id_planilha,
                importado_em=registro_existente.get("importado_em"),
                arquivo=registro_existente.get("arquivo"),
            )

    importadas = 0
    duplicadas = 0
    erros: list[str] = []

    for idx, row in df.iterrows():
        numero_linha = idx + 2  # aproximação da linha na planilha original
        try:
            codigo = str(row["codigo_interno"]).strip()
            produto_id = buscar_produto_id(sb, codigo)
            if produto_id is None:
                erros.append(f"Linha {numero_linha}: produto com código '{codigo}' não encontrado.")
                continue

            canal_nome = str(row.get("canal_venda") or "").strip()
            if not canal_nome:
                erros.append(f"Linha {numero_linha}: canal de venda vazio.")
                continue
            status_nome = str(row.get("status") or "Pendente").strip()

            canal_venda_id = obter_ou_criar_lista_id(sb, "canais_venda", canal_nome)
            status_id = obter_ou_criar_lista_id(sb, "status_venda", status_nome)

            linha = {
                "canal_venda_id": canal_venda_id,
                "status_id": status_id,
                "quantidade": Decimal(str(row["quantidade"])),
                "valor_lista": _parse_moeda(row.get("valor_lista")),
                "valor_desconto": _parse_moeda(row.get("valor_desconto")),
                "valor_final": _parse_moeda(row.get("valor_final")),
                "data_venda": _parse_data(row["data_venda"]),
                "cliente": str(row.get("cliente") or "").strip(),
            }

            if venda_ja_existe(sb, produto_id, linha["data_venda"], float(linha["valor_final"]), linha["cliente"]):
                duplicadas += 1
                continue

            inserir_venda_e_baixar_estoque(
                sb, linha, produto_id, motivo_saida="Importação XLSX"
            )
            importadas += 1

        except Exception as e:
            erros.append(f"Linha {numero_linha}: {e}")

    if id_planilha and importadas > 0:
        registrar_planilha_importada(sb, id_planilha, total_linhas=importadas)

    return {
        "total_linhas": len(df),
        "importadas": importadas,
        "duplicadas": duplicadas,
        "erros": erros,
        "id_planilha": id_planilha,
    }


if __name__ == "__main__":
    import sys
    resultado = importar_vendas_excel(sys.argv[1])
    print(resultado)

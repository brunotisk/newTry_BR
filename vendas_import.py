"""
Importa vendas a partir de uma planilha Excel (.xlsx) para o Supabase.

Formato esperado da planilha (uma linha por venda, sem cabeçalho):
  Canal | Código Interno | Quantidade | Produto | Valor Unitário |
  Desconto | Valor Total | Status | Data | Cliente

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

Requer as mesmas variáveis de ambiente de supabase_import.py:
  SUPABASE_URL
  SUPABASE_SERVICE_KEY

Instalar: pip install pandas openpyxl
"""
from __future__ import annotations
import os
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

import pandas as pd
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

# Formato do código interno no banco: string numérica com zeros à esquerda,
# ex.: "0024727". Ajuste a largura abaixo se o padrão do seu banco for diferente.
LARGURA_CODIGO_INTERNO = 7
PADRAO_CODIGO_INTERNO = re.compile(rf"^\d{{{LARGURA_CODIGO_INTERNO}}}$")

# Ordem das colunas quando a planilha NÃO tem cabeçalho
COLUNAS_SEM_CABECALHO = [
    "canal_venda",
    "codigo_interno",
    "quantidade",
    "descricao_produto",
    "valor_unitario",
    "valor_desconto",
    "valor_total",
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
    "valor unitario": "valor_unitario",
    "valor unitário": "valor_unitario",
    "desconto": "valor_desconto",
    "valor desconto": "valor_desconto",
    "valor total": "valor_total",
    "valor liquido": "valor_total",
    "valor líquido": "valor_total",
    "status": "status",
    "data": "data_venda",
    "data da venda": "data_venda",
    "cliente": "cliente",
}

COLUNAS_OBRIGATORIAS = ["codigo_interno", "quantidade", "valor_total", "data_venda"]


def normalizar_codigo_interno(valor) -> str:
    """Normaliza o código interno lido do Excel para o mesmo formato usado no banco:
    string numérica com zeros à esquerda (ex.: "0024727").

    O Excel/pandas costuma guardar/ler esse tipo de código como número, o que
    derruba os zeros à esquerda (0024727 -> 24727, ou até 24727.0). Aqui a gente
    reconstrói o texto original antes de comparar com o banco.
    """
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return ""

    if isinstance(valor, (int, float)):
        texto = str(int(valor))
    else:
        texto = str(valor).strip()
        if texto.endswith(".0"):  # ex.: célula veio como texto "24727.0"
            texto = texto[:-2]

    if texto.isdigit():
        texto = texto.zfill(LARGURA_CODIGO_INTERNO)

    return texto


def validar_codigo_interno(codigo: str) -> bool:
    """Confere se o código já normalizado bate com o formato esperado
    (LARGURA_CODIGO_INTERNO dígitos numéricos)."""
    return bool(PADRAO_CODIGO_INTERNO.fullmatch(codigo))


def get_client() -> Client:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_KEY"]
    return create_client(url, key)


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
    return df


def buscar_produto_id(sb: Client, codigo_interno: str) -> int | None:
    resp = (
        sb.table("produtos")
        .select("id")
        .eq("codigo_interno", str(codigo_interno).strip())
        .execute()
    )
    return resp.data[0]["id"] if resp.data else None


def venda_ja_existe(sb: Client, produto_id: int, data_venda: str, valor_total: float, cliente: str) -> bool:
    """Checagem de duplicidade: mesma combinação de produto + data + valor + cliente.
    A planilha não tem um identificador único por linha, então essa é uma
    aproximação razoável para evitar reimportar a mesma venda duas vezes."""
    resp = (
        sb.table("vendas")
        .select("id")
        .eq("produto_id", produto_id)
        .eq("data_venda", data_venda)
        .eq("valor_total", valor_total)
        .eq("cliente", cliente or "")
        .execute()
    )
    return len(resp.data) > 0


def inserir_venda_e_baixar_estoque(sb: Client, linha: dict, produto_id: int) -> int:
    venda_resp = sb.table("vendas").insert({
        "produto_id": produto_id,
        "canal_venda": linha["canal_venda"],
        "quantidade": float(linha["quantidade"]),
        "valor_unitario": float(linha["valor_unitario"]),
        "valor_desconto": float(linha["valor_desconto"]),
        "valor_total": float(linha["valor_total"]),
        "status": linha["status"] or "Pendente",
        "data_venda": linha["data_venda"],
        "cliente": linha["cliente"],
    }).execute()
    venda_id = venda_resp.data[0]["id"]

    estoque_resp = sb.table("estoque").select("quantidade_atual").eq("produto_id", produto_id).execute()
    saldo_anterior = estoque_resp.data[0]["quantidade_atual"] if estoque_resp.data else 0
    novo_saldo = float(saldo_anterior) - float(linha["quantidade"])

    sb.table("estoque").upsert({
        "produto_id": produto_id,
        "quantidade_atual": novo_saldo,
    }, on_conflict="produto_id").execute()

    sb.table("estoque_movimentos").insert({
        "produto_id": produto_id,
        "venda_id": venda_id,
        "tipo": "saida",
        "quantidade": float(linha["quantidade"]),
        "saldo_apos": novo_saldo,
        "data_movimento": linha["data_venda"],
    }).execute()

    return venda_id


def importar_vendas_excel(caminho_arquivo) -> dict:
    """Importa todas as linhas válidas de uma planilha de vendas.
    Não interrompe no primeiro erro: cada linha é processada de forma
    independente e o resumo final lista o que foi importado, ignorado
    (duplicado) ou deu erro."""
    df = ler_planilha(caminho_arquivo)
    sb = get_client()

    importadas = 0
    duplicadas = 0
    erros: list[str] = []

    for idx, row in df.iterrows():
        numero_linha = idx + 2  # aproximação da linha na planilha original
        try:
            codigo = normalizar_codigo_interno(row["codigo_interno"])

            if not codigo:
                erros.append(f"Linha {numero_linha}: código interno vazio.")
                continue

            if not validar_codigo_interno(codigo):
                erros.append(
                    f"Linha {numero_linha}: código '{codigo}' fora do formato esperado "
                    f"({LARGURA_CODIGO_INTERNO} dígitos numéricos, ex.: '0024727')."
                )
                continue

            produto_id = buscar_produto_id(sb, codigo)
            if produto_id is None:
                erros.append(f"Linha {numero_linha}: produto com código '{codigo}' não encontrado.")
                continue

            linha = {
                "canal_venda": str(row.get("canal_venda") or "").strip(),
                "quantidade": Decimal(str(row["quantidade"])),
                "valor_unitario": _parse_moeda(row.get("valor_unitario")),
                "valor_desconto": _parse_moeda(row.get("valor_desconto")),
                "valor_total": _parse_moeda(row.get("valor_total")),
                "status": str(row.get("status") or "Pendente").strip(),
                "data_venda": _parse_data(row["data_venda"]),
                "cliente": str(row.get("cliente") or "").strip(),
            }

            if venda_ja_existe(sb, produto_id, linha["data_venda"], float(linha["valor_total"]), linha["cliente"]):
                duplicadas += 1
                continue

            inserir_venda_e_baixar_estoque(sb, linha, produto_id)
            importadas += 1

        except Exception as e:
            erros.append(f"Linha {numero_linha}: {e}")

    return {
        "total_linhas": len(df),
        "importadas": importadas,
        "duplicadas": duplicadas,
        "erros": erros,
    }


if __name__ == "__main__":
    import sys
    resultado = importar_vendas_excel(sys.argv[1])
    print(resultado)

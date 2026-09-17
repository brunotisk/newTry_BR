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
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import pandas as pd
from dotenv import load_dotenv
from supabase import create_client, Client

from estoque_ajuste import registrar_ajuste

load_dotenv()

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

# O Excel guarda "0024727" apenas como formatação visual: o valor real salvo
# na célula é o número 24727, sem os zeros à esquerda. No banco, porém,
# produtos.codigo_interno é salvo como texto já com o zero-padding (ex.:
# "0024727", 7 dígitos). Sem repor esses zeros, a busca do produto pelo
# código falha. Ajuste este número se o padrão de código mudar.
TAMANHO_CODIGO_INTERNO = 7


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
    df["codigo_interno"] = df["codigo_interno"].apply(normalizar_codigo_interno)
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


def inserir_venda_e_baixar_estoque(sb: Client, linha: dict, produto_id: int) -> int:
    venda_resp = sb.table("vendas").insert({
        "produto_id": produto_id,
        "canal_venda_id": linha["canal_venda_id"],
        "quantidade": float(linha["quantidade"]),
        "valor_unitario": float(linha["valor_unitario"]),
        "valor_desconto": float(linha["valor_desconto"]),
        "valor_total": float(linha["valor_total"]),
        "status_id": linha["status_id"],
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

    `venda` precisa conter `id`, `produto_id` e `quantidade`.
    """
    venda_id = venda["id"]
    produto_id = venda["produto_id"]
    quantidade = float(venda.get("quantidade") or 0)

    registrar_ajuste(
        sb,
        produto_id=produto_id,
        quantidade=quantidade,
        motivo=f"Estorno da venda #{venda_id} (exclusão)",
        data_movimento=date.today().isoformat(),
    )
    sb.table("vendas").delete().eq("id", venda_id).execute()


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
                "valor_unitario": _parse_moeda(row.get("valor_unitario")),
                "valor_desconto": _parse_moeda(row.get("valor_desconto")),
                "valor_total": _parse_moeda(row.get("valor_total")),
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

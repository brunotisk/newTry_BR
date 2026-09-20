"""
Módulo central do domínio "estoque": leitura de saldo e gravação de
movimentações (ledger). É o único lugar que sabe como atualizar
`estoque.quantidade_atual` e inserir uma linha em `estoque_movimentos`.

Usado por:
  - servicos/compras_import.py  (tipo="entrada", ligado a uma compra)
  - servicos/vendas_import.py   (tipo="saida", ligado a uma venda; e
                                  "ajuste" para estornos de edição/exclusão)
  - telas/estoque_ajuste.py     (tipo="ajuste", lançamento manual)

Antes desta refatoração, essa lógica de "ler saldo, somar, upsert, inserir
no ledger" estava reimplementada de forma quase idêntica em três arquivos
diferentes (estoque_ajuste.py, supabase_import.py e vendas_import.py).
Centralizar aqui evita que uma regra nova (ex.: travar saldo negativo,
adicionar auditoria) precise ser replicada em vários lugares.
"""
from __future__ import annotations
from supabase import Client

from .supabase_admin import get_client  # noqa: F401  (re-exportado por conveniência)


def obter_saldo(sb: Client, produto_id: int) -> float:
    """Saldo atual em estoque do produto (0.0 se ainda não houver linha)."""
    resp = sb.table("estoque").select("quantidade_atual").eq("produto_id", produto_id).execute()
    return float(resp.data[0]["quantidade_atual"]) if resp.data else 0.0


def registrar_movimento(
    sb: Client,
    produto_id: int,
    quantidade: float,
    tipo: str,
    data_movimento: str,
    motivo: str | None = None,
    compra_id: int | None = None,
    venda_id: int | None = None,
) -> int:
    """Atualiza o saldo de estoque do produto e grava o movimento correspondente
    no ledger (`estoque_movimentos`). Retorna o id do movimento criado.

    `quantidade` é sempre um valor com sinal: positivo soma ao saldo (entrada,
    ajuste positivo), negativo subtrai (saída, ajuste negativo/perda).
    `tipo` normalmente é "entrada", "saida" ou "ajuste".
    Passe `compra_id` ou `venda_id` quando o movimento estiver vinculado a um
    desses registros — os dois são opcionais e mutuamente exclusivos; quando
    nenhum é passado (caso de ajuste manual), nenhuma das colunas é enviada.
    """
    saldo_anterior = obter_saldo(sb, produto_id)
    novo_saldo = saldo_anterior + float(quantidade)

    sb.table("estoque").upsert({
        "produto_id": produto_id,
        "quantidade_atual": novo_saldo,
    }, on_conflict="produto_id").execute()

    dados_movimento = {
        "produto_id": produto_id,
        "tipo": tipo,
        "quantidade": float(quantidade),
        "saldo_apos": novo_saldo,
        "data_movimento": data_movimento,
    }
    if motivo is not None:
        dados_movimento["motivo"] = motivo
    if compra_id is not None:
        dados_movimento["compra_id"] = compra_id
    if venda_id is not None:
        dados_movimento["venda_id"] = venda_id

    resp = sb.table("estoque_movimentos").insert(dados_movimento).execute()
    return resp.data[0]["id"]


def registrar_ajuste(
    sb: Client,
    produto_id: int,
    quantidade: float,
    motivo: str,
    data_movimento: str,
) -> int:
    """Atalho para um movimento do tipo "ajuste" — mesma assinatura que a
    tela de ajuste manual (telas/estoque_ajuste.py) já usava, e também usado
    por vendas_import.py para estornar/ajustar estoque ao editar ou excluir
    uma venda.
    """
    return registrar_movimento(
        sb,
        produto_id=produto_id,
        quantidade=quantidade,
        tipo="ajuste",
        data_movimento=data_movimento,
        motivo=motivo,
    )

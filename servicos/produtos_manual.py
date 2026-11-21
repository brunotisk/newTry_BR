"""Cadastro manual de produto e ajuste de estoque/preços.

A quantidade nunca é gravada direto em `estoque`: passa por
`estoque.registrar_movimento` (tipo "ajuste"), para manter ledger, log de
automações e idade do estoque (FIFO) consistentes.
"""
from __future__ import annotations

import uuid
from supabase import Client

from .estoque import obter_saldo, registrar_movimento

MOTIVO_INICIAL = "Cadastro manual - estoque inicial"
MOTIVO_PADRAO_AJUSTE = "Ajuste manual pela tela de produtos"


def codigo_existe(sb: Client, codigo: str) -> bool:
    resp = (
        sb.table("produtos").select("id")
        .eq("codigo_interno", codigo.strip()).limit(1).execute()
    )
    return bool(resp.data)


def preco_venda_sugerido(preco_compra: float | None) -> float | None:
    """Mesma regra da importação de NF: 2x o valor de compra."""
    return round(float(preco_compra) * 2, 2) if preco_compra else None


def cadastrar_produto_manual(
    sb: Client,
    codigo: str,
    descricao: str,
    data_movimento: str,
    categoria_id: int | None = None,
    unidade: str | None = None,
    quantidade: float = 0,
    preco_compra: float | None = None,
    preco_venda: float | None = None,
) -> int:
    """Cria o produto, a linha de estoque (com preços) e o movimento da quantidade inicial.

    Se `preco_venda` vier vazio/zero, usa o sugerido (2x compra).
    Retorna o id do produto.
    """
    codigo = (codigo or "").strip()
    descricao = (descricao or "").strip()
    if not codigo or not descricao:
        raise ValueError("Informe o código interno e a descrição.")
    if float(quantidade or 0) < 0:
        raise ValueError("A quantidade não pode ser negativa.")
    if codigo_existe(sb, codigo):
        raise ValueError(f"Já existe um produto com o código {codigo}.")

    dados = {"codigo_interno": codigo, "descricao": descricao, "nr_compras": 0}
    if categoria_id is not None:
        dados["categoria_id"] = categoria_id
    if unidade and unidade.strip():
        dados["unidade"] = unidade.strip()
    produto_id = sb.table("produtos").insert(dados).execute().data[0]["id"]

    try:
        estoque = {"produto_id": produto_id, "quantidade_atual": 0}
        if preco_compra:
            estoque["estoque_preco_ultima_compra"] = float(preco_compra)
        venda = float(preco_venda) if preco_venda else preco_venda_sugerido(preco_compra)
        if venda:
            estoque["estoque_preco_venda_sugerida"] = venda
        sb.table("estoque").upsert(estoque, on_conflict="produto_id").execute()

        if float(quantidade or 0) > 0:
            registrar_movimento(
                sb,
                produto_id=produto_id,
                quantidade=float(quantidade),
                tipo="ajuste",
                data_movimento=data_movimento,
                motivo=MOTIVO_INICIAL,
                operacao_id=str(uuid.uuid4()),
                fluxo="ajuste_manual",
                realizado_por="Manual",
            )
    except Exception:
        # desfaz o cadastro parcial para não deixar produto "meio criado"
        try:
            sb.table("estoque").delete().eq("produto_id", produto_id).execute()
            sb.table("produtos").delete().eq("id", produto_id).execute()
        except Exception:
            pass
        raise
    return produto_id


def atualizar_estoque_e_precos(
    sb: Client,
    produto_id: int,
    nova_quantidade: float,
    data_movimento: str,
    preco_compra: float | None,
    preco_venda: float | None,
    motivo: str = "",
) -> dict:
    """Leva o saldo para `nova_quantidade` (gera um ajuste com a diferença) e atualiza os preços.

    Preço de venda: segue a regra da tela de Estoque; no primeiro ajuste guarda o valor
    anterior em `estoque_preco_venda_original` e marca `estoque_flag_ajuste_preco_venda`.
    Preço None/0 = não altera.
    """
    if float(nova_quantidade) < 0:
        raise ValueError("A quantidade não pode ser negativa.")

    saldo = obter_saldo(sb, produto_id)
    delta = round(float(nova_quantidade) - saldo, 6)
    if delta != 0:
        registrar_movimento(
            sb,
            produto_id=produto_id,
            quantidade=delta,
            tipo="ajuste",
            data_movimento=data_movimento,
            motivo=(motivo or "").strip() or MOTIVO_PADRAO_AJUSTE,
            operacao_id=str(uuid.uuid4()),
            fluxo="ajuste_manual",
            realizado_por="Manual",
        )

    atual = (
        sb.table("estoque")
        .select(
            "estoque_preco_ultima_compra, estoque_preco_venda_sugerida, "
            "estoque_preco_venda_original"
        )
        .eq("produto_id", produto_id)
        .execute()
        .data
        or [{}]
    )[0]

    upd = {}
    if preco_compra is not None and round(float(preco_compra), 2) != round(
        float(atual.get("estoque_preco_ultima_compra") or 0), 2
    ):
        upd["estoque_preco_ultima_compra"] = round(float(preco_compra), 2)

    if preco_venda:
        novo = round(float(preco_venda), 2)
        venda_atual = atual.get("estoque_preco_venda_sugerida")
        if venda_atual is None or novo != round(float(venda_atual), 2):
            upd["estoque_preco_venda_sugerida"] = novo
            original = atual.get("estoque_preco_venda_original")
            if venda_atual is not None:
                if original is None:
                    upd["estoque_preco_venda_original"] = round(float(venda_atual), 2)
                    upd["estoque_flag_ajuste_preco_venda"] = True
                else:
                    upd["estoque_flag_ajuste_preco_venda"] = novo != round(float(original), 2)

    if upd:
        sb.table("estoque").upsert(
            {"produto_id": produto_id, **upd}, on_conflict="produto_id"
        ).execute()
    return {"delta": delta, "precos_alterados": list(upd)}

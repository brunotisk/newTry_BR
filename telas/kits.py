"""
Kits: receita, montagem e desmontagem.

Um kit é um produto comum, com estoque e preço próprios. A montagem transforma
componentes em kit:

    componentes  -> "ajuste" negativo   (baixa o estoque deles)
    kit          -> "ajuste" positivo   (sobe o estoque do kit)

Todos os movimentos de uma montagem usam o mesmo `operacao_id` (o mesmo do log de
automações) e são gravados por `servicos/estoque.registrar_movimento`, que continua
sendo o único ponto que mexe em `estoque` e `estoque_movimentos`. Assim a venda do
kit, a maleta, o app mobile, o PWA, os relatórios e o cálculo de idade do estoque
(FIFO) funcionam sem alteração.

Custo do kit = soma de (quantidade do componente x preço da última compra dele) no
momento da montagem. Fica em `estoque.estoque_preco_ultima_compra` do kit e em
`kits_montagens.custo_unitario`.

Requer a migration 019_kits_montagem.sql.
"""
from __future__ import annotations

import math
import uuid

from supabase import Client

from .estoque import (
    SaldoInsuficienteError,
    obter_custo_unitario,
    obter_saldo,
    registrar_movimento,
)

FLUXO_MONTAGEM = "montagem_kit"
FLUXO_DESMONTAGEM = "desmontagem_kit"
REALIZADO_POR = "Automático"


# ----------------------------------------------------------------------
# Leituras
# ----------------------------------------------------------------------
def _produtos_por_id(sb: Client, ids) -> dict:
    ids = sorted({i for i in ids if i is not None})
    if not ids:
        return {}
    resp = sb.table("produtos").select("id, codigo_interno, descricao").in_("id", ids).execute()
    return {p["id"]: p for p in (resp.data or [])}


def obter_receita(sb: Client, kit_id: int) -> list[dict]:
    """Componentes do kit: [{componente_id, quantidade (por kit), codigo_interno, descricao}]."""
    linhas = (
        sb.table("kits_composicao")
        .select("componente_id, quantidade")
        .eq("kit_id", kit_id)
        .order("id")
        .execute()
        .data
        or []
    )
    produtos = _produtos_por_id(sb, [l["componente_id"] for l in linhas])
    return [
        {
            "componente_id": l["componente_id"],
            "quantidade": float(l["quantidade"]),
            "codigo_interno": (produtos.get(l["componente_id"]) or {}).get("codigo_interno") or "-",
            "descricao": (produtos.get(l["componente_id"]) or {}).get("descricao") or "-",
        }
        for l in linhas
    ]


def obter_preco_venda(sb: Client, kit_id: int) -> float | None:
    """Preço de venda sugerido atual do kit (None se o kit ainda não tem linha de estoque)."""
    resp = (
        sb.table("estoque")
        .select("estoque_preco_venda_sugerida")
        .eq("produto_id", kit_id)
        .limit(1)
        .execute()
    )
    if not resp.data or resp.data[0].get("estoque_preco_venda_sugerida") is None:
        return None
    return float(resp.data[0]["estoque_preco_venda_sugerida"])


def listar_montagens(sb: Client, limite: int = 100) -> list[dict]:
    """Montagens mais recentes, com o kit em `_kit`."""
    montagens = (
        sb.table("kits_montagens")
        .select("*")
        .order("id", desc=True)
        .limit(limite)
        .execute()
        .data
        or []
    )
    produtos = _produtos_por_id(sb, [m["kit_id"] for m in montagens])
    for m in montagens:
        m["_kit"] = produtos.get(m["kit_id"], {})
    return montagens


# ----------------------------------------------------------------------
# Receita
# ----------------------------------------------------------------------
def definir_receita(sb: Client, kit_id: int, componentes: list[dict]) -> None:
    """Grava (substitui) a receita do kit e marca o produto como kit.

    `componentes`: [{"produto_id": int, "quantidade": número por kit}].
    """
    if not componentes:
        raise ValueError("A receita precisa de ao menos um componente.")

    vistos, linhas = set(), []
    for c in componentes:
        pid = int(c["produto_id"])
        qtd = float(c["quantidade"])
        if pid == kit_id:
            raise ValueError("Um kit não pode ser componente dele mesmo.")
        if pid in vistos:
            raise ValueError("Componente repetido na receita.")
        if qtd <= 0:
            raise ValueError("A quantidade de cada componente deve ser maior que zero.")
        vistos.add(pid)
        linhas.append({"kit_id": kit_id, "componente_id": pid, "quantidade": qtd})

    anterior = (
        sb.table("kits_composicao")
        .select("kit_id, componente_id, quantidade")
        .eq("kit_id", kit_id)
        .execute()
        .data
        or []
    )
    sb.table("kits_composicao").delete().eq("kit_id", kit_id).execute()
    try:
        sb.table("kits_composicao").insert(linhas).execute()
    except Exception:
        if anterior:  # devolve a receita que existia
            try:
                sb.table("kits_composicao").insert(anterior).execute()
            except Exception:
                pass
        raise
    sb.table("produtos").update({"eh_kit": True}).eq("id", kit_id).execute()


# ----------------------------------------------------------------------
# Cálculo (somente leitura)
# ----------------------------------------------------------------------
def calcular_montagem(sb: Client, kit_id: int, quantidade: int, reservado: dict | None = None) -> dict:
    """Quanto de cada componente a montagem usa e se o estoque dá conta.

    `reservado`: {produto_id: peças reservadas em maletas ativas}; elas não podem ser
    usadas na montagem. Não grava nada.
    """
    quantidade = int(quantidade)
    reservado = reservado or {}
    itens = []
    for r in obter_receita(sb, kit_id):
        pid = r["componente_id"]
        por_kit = r["quantidade"]
        saldo = obter_saldo(sb, pid)
        reservado_pid = float(reservado.get(pid, 0.0))
        disponivel = max(saldo - reservado_pid, 0.0)
        necessario = por_kit * quantidade
        itens.append({
            "produto_id": pid,
            "codigo_interno": r["codigo_interno"],
            "descricao": r["descricao"],
            "quantidade_por_kit": por_kit,
            "necessario": necessario,
            "saldo": saldo,
            "reservado": reservado_pid,
            "disponivel": disponivel,
            "falta": max(necessario - disponivel, 0.0),
            "custo_unitario": obter_custo_unitario(sb, pid),
        })

    custo_kit = sum(i["quantidade_por_kit"] * (i["custo_unitario"] or 0.0) for i in itens)
    max_montavel = min(
        (int(math.floor(i["disponivel"] / i["quantidade_por_kit"] + 1e-9)) for i in itens),
        default=0,
    )
    faltas = [
        f"{i['codigo_interno']}: precisa {i['necessario']:g}, disponível {i['disponivel']:g}"
        for i in itens if i["falta"] > 1e-9
    ]
    return {
        "kit_id": kit_id,
        "quantidade": quantidade,
        "itens": itens,
        "custo_unitario": custo_kit,
        "custo_total": custo_kit * quantidade,
        "max_montavel": max_montavel,
        "sem_custo": [i["codigo_interno"] for i in itens if not i["custo_unitario"]],
        "faltas": faltas,
        "ok": bool(itens) and not faltas,
    }


# ----------------------------------------------------------------------
# Montagem e desmontagem
# ----------------------------------------------------------------------
def _desfazer(sb, feitos, motivo, data_movimento, operacao_id, fluxo) -> None:
    """Compensação: reverte, do último para o primeiro, os movimentos já gravados."""
    for produto_id, delta in reversed(feitos):
        try:
            registrar_movimento(
                sb, produto_id, -delta, "ajuste", data_movimento, motivo=motivo,
                operacao_id=operacao_id, fluxo=fluxo, realizado_por=REALIZADO_POR,
            )
        except Exception:
            pass  # o erro original é o que importa; a reversão é melhor esforço


def montar_kit(
    sb: Client,
    kit_id: int,
    quantidade: int,
    data_movimento: str,
    reservado: dict | None = None,
    preco_venda: float | None = None,
    operacao_id: str | None = None,
    usuario: str | None = None,
) -> dict:
    """Monta `quantidade` kits: baixa os componentes, sobe o kit e registra a montagem.

    Recusa a montagem (sem gravar nada) se faltar componente disponível. Se algo falhar
    no meio, desfaz o que já foi gravado. `preco_venda`, se informado, vira o preço de
    venda sugerido do kit. Retorna {montagem_id, operacao_id, quantidade, custo_unitario}.
    """
    if quantidade is None or int(quantidade) != quantidade or quantidade <= 0:
        raise ValueError("A quantidade de kits deve ser um número inteiro maior que zero.")
    quantidade = int(quantidade)

    kit = _produtos_por_id(sb, [kit_id]).get(kit_id)
    if not kit:
        raise ValueError("Kit não encontrado.")

    calc = calcular_montagem(sb, kit_id, quantidade, reservado)
    if not calc["itens"]:
        raise ValueError("Este kit não tem receita cadastrada.")
    if not calc["ok"]:
        raise SaldoInsuficienteError("Saldo insuficiente para montar: " + "; ".join(calc["faltas"]))

    operacao_id = operacao_id or str(uuid.uuid4())
    motivo = f"Montagem do kit {kit['codigo_interno']} ({quantidade} un)"
    feitos: list[tuple[int, float]] = []
    montagem_id = None

    try:
        for it in calc["itens"]:
            registrar_movimento(
                sb, it["produto_id"], -it["necessario"], "ajuste", data_movimento, motivo=motivo,
                operacao_id=operacao_id, fluxo=FLUXO_MONTAGEM, realizado_por=REALIZADO_POR,
            )
            feitos.append((it["produto_id"], -it["necessario"]))

        registrar_movimento(
            sb, kit_id, float(quantidade), "ajuste", data_movimento, motivo=motivo,
            operacao_id=operacao_id, fluxo=FLUXO_MONTAGEM, realizado_por=REALIZADO_POR,
        )
        feitos.append((kit_id, float(quantidade)))

        montagem = sb.table("kits_montagens").insert({
            "kit_id": kit_id,
            "quantidade": quantidade,
            "custo_unitario": round(calc["custo_unitario"], 4),
            "data_montagem": data_movimento,
            "operacao_id": operacao_id,
            "usuario": usuario,
        }).execute().data[0]
        montagem_id = montagem["id"]

        sb.table("kits_montagem_itens").insert([
            {
                "montagem_id": montagem_id,
                "componente_id": it["produto_id"],
                "quantidade_por_kit": it["quantidade_por_kit"],
                "custo_unitario": it["custo_unitario"],
            }
            for it in calc["itens"]
        ]).execute()

        dados = {}
        if calc["custo_unitario"] > 0:
            dados["estoque_preco_ultima_compra"] = round(calc["custo_unitario"], 4)
        if preco_venda is not None:
            dados["estoque_preco_venda_sugerida"] = round(float(preco_venda), 2)
        if dados:
            sb.table("estoque").update(dados).eq("produto_id", kit_id).execute()
    except Exception:
        _desfazer(sb, feitos, f"Reversão: {motivo}", data_movimento, operacao_id, FLUXO_MONTAGEM)
        if montagem_id is not None:
            try:
                sb.table("kits_montagem_itens").delete().eq("montagem_id", montagem_id).execute()
                sb.table("kits_montagens").delete().eq("id", montagem_id).execute()
            except Exception:
                pass
        raise

    return {
        "montagem_id": montagem_id,
        "operacao_id": operacao_id,
        "quantidade": quantidade,
        "custo_unitario": calc["custo_unitario"],
    }


def desmontar_kit(
    sb: Client,
    montagem_id: int,
    quantidade: int,
    data_movimento: str,
    operacao_id: str | None = None,
    usuario: str | None = None,
) -> dict:
    """Desfaz (total ou parcialmente) uma montagem: o kit é baixado e os componentes voltam.

    Usa a receita gravada NA MONTAGEM (não a atual). Só dá para desmontar kits que ainda
    estão em estoque (os já vendidos não voltam). Retorna {operacao_id, quantidade, restante}.
    """
    if quantidade is None or int(quantidade) != quantidade or quantidade <= 0:
        raise ValueError("A quantidade a desmontar deve ser um número inteiro maior que zero.")
    quantidade = int(quantidade)

    achadas = sb.table("kits_montagens").select("*").eq("id", montagem_id).limit(1).execute().data or []
    if not achadas:
        raise ValueError("Montagem não encontrada.")
    m = achadas[0]
    kit_id = m["kit_id"]
    restante = int(m["quantidade"]) - int(m.get("qtd_desmontada") or 0)
    if quantidade > restante:
        raise ValueError(f"Só restam {restante} kit(s) desta montagem para desmontar.")

    saldo_kit = obter_saldo(sb, kit_id)
    if saldo_kit < quantidade:
        raise SaldoInsuficienteError(
            f"Saldo insuficiente do kit: disponível {saldo_kit:g}, desmontagem de {quantidade}."
        )

    itens = (
        sb.table("kits_montagem_itens").select("componente_id, quantidade_por_kit")
        .eq("montagem_id", montagem_id).execute().data or []
    )
    kit = _produtos_por_id(sb, [kit_id]).get(kit_id) or {}
    operacao_id = operacao_id or str(uuid.uuid4())
    motivo = f"Desmontagem do kit {kit.get('codigo_interno') or kit_id} ({quantidade} un) - montagem #{montagem_id}"
    feitos: list[tuple[int, float]] = []

    try:
        registrar_movimento(
            sb, kit_id, -float(quantidade), "ajuste", data_movimento, motivo=motivo,
            operacao_id=operacao_id, fluxo=FLUXO_DESMONTAGEM, realizado_por=REALIZADO_POR,
        )
        feitos.append((kit_id, -float(quantidade)))
        for it in itens:
            devolvido = float(it["quantidade_por_kit"]) * quantidade
            registrar_movimento(
                sb, it["componente_id"], devolvido, "ajuste", data_movimento, motivo=motivo,
                operacao_id=operacao_id, fluxo=FLUXO_DESMONTAGEM, realizado_por=REALIZADO_POR,
            )
            feitos.append((it["componente_id"], devolvido))
        sb.table("kits_montagens").update(
            {"qtd_desmontada": int(m.get("qtd_desmontada") or 0) + quantidade}
        ).eq("id", montagem_id).execute()
    except Exception:
        _desfazer(sb, feitos, f"Reversão: {motivo}", data_movimento, operacao_id, FLUXO_DESMONTAGEM)
        raise

    return {"operacao_id": operacao_id, "quantidade": quantidade, "restante": restante - quantidade}

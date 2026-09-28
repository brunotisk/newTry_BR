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

Por ser o ponto único de gravação, é também aqui que o log de automações
(`servicos/log_automacao.py`) é alimentado: toda vez que o saldo de estoque
é ajustado ("ajusta_estoque") ou um movimento é gravado no ledger
("ajusta_movimentacao"), uma linha é registrada em `logs_automacao`, com
sucesso ou falha. Assim os três fluxos (compra, venda importada, venda
manual) e os fluxos de estorno (edição/exclusão) ganham o log automaticamente,
sem precisar duplicar a chamada em cada tela/serviço.
"""
from __future__ import annotations
import uuid
from supabase import Client

from .supabase_admin import get_client  # noqa: F401  (re-exportado por conveniência)
from .log_automacao import registrar_log

# Fluxo padrão usado quando quem chama registrar_movimento/registrar_ajuste
# não informa explicitamente de qual pipeline se trata (ex.: chamadas antigas
# ou telas/estoque_ajuste.py, que faz lançamento manual).
_FLUXO_PADRAO_POR_TIPO = {
    "entrada": "compra_importacao",
    "saida": "venda_manual",
    "ajuste": "ajuste_manual",
}


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
    operacao_id: str | None = None,
    fluxo: str | None = None,
) -> int:
    """Atualiza o saldo de estoque do produto e grava o movimento correspondente
    no ledger (`estoque_movimentos`). Retorna o id do movimento criado.

    `quantidade` é sempre um valor com sinal: positivo soma ao saldo (entrada,
    ajuste positivo), negativo subtrai (saída, ajuste negativo/perda).
    `tipo` normalmente é "entrada", "saida" ou "ajuste".
    Passe `compra_id` ou `venda_id` quando o movimento estiver vinculado a um
    desses registros — os dois são opcionais e mutuamente exclusivos; quando
    nenhum é passado (caso de ajuste manual), nenhuma das colunas é enviada.

    `operacao_id` agrupa, no log de automações, todas as etapas de uma mesma
    execução (ex.: todos os itens de uma NF-e). Se não for informado, um novo
    id é gerado — a etapa ainda é registrada, só não fica agrupada com outras.
    `fluxo` identifica o pipeline de origem para a tela de administração
    (ex.: "compra_importacao", "venda_manual"); se omitido, é inferido de
    `tipo`.
    """
    operacao_id = operacao_id or str(uuid.uuid4())
    fluxo = fluxo or _FLUXO_PADRAO_POR_TIPO.get(tipo, "outro")

    try:
        saldo_anterior = obter_saldo(sb, produto_id)
        novo_saldo = saldo_anterior + float(quantidade)

        sb.table("estoque").upsert({
            "produto_id": produto_id,
            "quantidade_atual": novo_saldo,
        }, on_conflict="produto_id").execute()
    except Exception as exc:
        registrar_log(
            sb, operacao_id=operacao_id, fluxo=fluxo, etapa="ajusta_estoque",
            sucesso=False, produto_id=produto_id, compra_id=compra_id,
            venda_id=venda_id, quantidade=quantidade, mensagem=str(exc),
        )
        raise

    registrar_log(
        sb, operacao_id=operacao_id, fluxo=fluxo, etapa="ajusta_estoque",
        sucesso=True, produto_id=produto_id, compra_id=compra_id,
        venda_id=venda_id, quantidade=quantidade,
        mensagem=f"Saldo {saldo_anterior:g} -> {novo_saldo:g}",
    )

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

    try:
        resp = sb.table("estoque_movimentos").insert(dados_movimento).execute()
    except Exception as exc:
        registrar_log(
            sb, operacao_id=operacao_id, fluxo=fluxo, etapa="ajusta_movimentacao",
            sucesso=False, produto_id=produto_id, compra_id=compra_id,
            venda_id=venda_id, quantidade=quantidade, mensagem=str(exc),
        )
        raise

    registrar_log(
        sb, operacao_id=operacao_id, fluxo=fluxo, etapa="ajusta_movimentacao",
        sucesso=True, produto_id=produto_id, compra_id=compra_id,
        venda_id=venda_id, quantidade=quantidade, mensagem=motivo or tipo,
    )

    return resp.data[0]["id"]


def registrar_ajuste(
    sb: Client,
    produto_id: int,
    quantidade: float,
    motivo: str,
    data_movimento: str,
    operacao_id: str | None = None,
    fluxo: str | None = None,
) -> int:
    """Atalho para um movimento do tipo "ajuste" — mesma assinatura que a
    tela de ajuste manual (telas/estoque_ajuste.py) já usava, e também usado
    por vendas_import.py e compras_import.py para estornar/ajustar estoque ao
    editar ou excluir uma venda/compra.
    """
    return registrar_movimento(
        sb,
        produto_id=produto_id,
        quantidade=quantidade,
        tipo="ajuste",
        data_movimento=data_movimento,
        motivo=motivo,
        operacao_id=operacao_id,
        fluxo=fluxo or "ajuste_manual",
    )

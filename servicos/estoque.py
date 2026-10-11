"""
Módulo central do domínio "estoque": leitura de saldo e gravação de
movimentações (ledger). É o único lugar que sabe como atualizar
`estoque.quantidade_atual` e inserir uma linha em `estoque_movimentos`.

Usado por:
  - servicos/compras_import.py  (tipo="entrada", ligado a uma compra)
  - servicos/vendas_import.py   (tipo="saida", ligado a uma venda; e
                                  "ajuste" para estornos de edição/exclusão)
  - telas/estoque_ajuste.py     (tipo="ajuste", lançamento manual)
  - telas/estoque_retirada.py   (tipo="retirada_pessoal", baixa para uso próprio; não é venda)

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
    "retirada_pessoal": "retirada_pessoal",
}

# Retirada pessoal: baixa de estoque para uso próprio. Não passa por `vendas`, então não
# entra em faturamento, ticket, comissão nem margem. O estorno é um "ajuste" positivo cujo
# motivo carrega o id da retirada original (é assim que a retirada é marcada como estornada).
TIPO_RETIRADA_PESSOAL = "retirada_pessoal"
PREFIXO_ESTORNO_RETIRADA = "Estorno da retirada pessoal #"


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
    custo_unitario: float | None = None,
    realizado_por: str | None = None,
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
    `custo_unitario` e `realizado_por` são opcionais e só são enviados quando
    informados (o banco usa os valores padrão das colunas nos demais casos).
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
    if custo_unitario is not None:
        dados_movimento["custo_unitario"] = float(custo_unitario)
    if realizado_por is not None:
        dados_movimento["realizado_por"] = realizado_por

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


# ----------------------------------------------------------------------
# Retirada pessoal (baixa de estoque para uso próprio — não é venda)
# ----------------------------------------------------------------------
class SaldoInsuficienteError(ValueError):
    """A retirada pede mais peças do que o saldo disponível."""


_COLUNA_CUSTO_VERIFICADA = False


def _garantir_coluna_custo(sb: Client) -> None:
    """Falha cedo, ANTES de mexer no saldo, se a migração do custo não foi aplicada."""
    global _COLUNA_CUSTO_VERIFICADA
    if _COLUNA_CUSTO_VERIFICADA:
        return
    try:
        sb.table("estoque_movimentos").select("custo_unitario").limit(1).execute()
    except Exception as exc:
        raise RuntimeError(
            "A coluna estoque_movimentos.custo_unitario não existe neste ambiente. "
            "Execute adicionar_retirada_pessoal.sql no Supabase (HML e PRD) e tente de novo."
        ) from exc
    _COLUNA_CUSTO_VERIFICADA = True


def obter_custo_unitario(sb: Client, produto_id: int) -> float | None:
    """Preço da última compra do produto (None se ainda não houver)."""
    resp = (
        sb.table("estoque")
        .select("estoque_preco_ultima_compra")
        .eq("produto_id", produto_id)
        .execute()
    )
    if not resp.data or resp.data[0].get("estoque_preco_ultima_compra") is None:
        return None
    return float(resp.data[0]["estoque_preco_ultima_compra"])


def registrar_retirada_pessoal(
    sb: Client,
    produto_id: int,
    quantidade: float,
    data_movimento: str,
    motivo: str | None = None,
    custo_unitario: float | None = None,
    operacao_id: str | None = None,
    realizado_por: str = "Manual",
    permitir_saldo_negativo: bool = False,
) -> int:
    """Dá baixa no estoque para uso próprio. `quantidade` é POSITIVA (é gravada negativa).

    Não toca em `vendas`. Grava o custo unitário vigente (preço da última compra) no
    movimento, para que o valor retirado não mude se o custo mudar depois. Por padrão
    recusa retirar mais do que o saldo. Retorna o id do movimento.
    """
    quantidade = float(quantidade)
    if quantidade <= 0:
        raise ValueError("A quantidade da retirada deve ser maior que zero.")

    _garantir_coluna_custo(sb)

    saldo = obter_saldo(sb, produto_id)
    if not permitir_saldo_negativo and quantidade > saldo + 1e-9:
        raise SaldoInsuficienteError(
            f"Saldo insuficiente: disponível {saldo:g}, retirada de {quantidade:g}."
        )
    if custo_unitario is None:
        custo_unitario = obter_custo_unitario(sb, produto_id)

    return registrar_movimento(
        sb,
        produto_id=produto_id,
        quantidade=-quantidade,
        tipo=TIPO_RETIRADA_PESSOAL,
        data_movimento=data_movimento,
        motivo=motivo,
        operacao_id=operacao_id,
        fluxo="retirada_pessoal",
        custo_unitario=custo_unitario,
        realizado_por=realizado_por,
    )


def estornar_retirada_pessoal(
    sb: Client,
    movimento_id: int,
    data_movimento: str,
    operacao_id: str | None = None,
) -> int:
    """Devolve ao estoque as peças de uma retirada, sem apagar o histórico.

    Grava um "ajuste" positivo automático com motivo "Estorno da retirada pessoal #<id>".
    Uma retirada só pode ser estornada uma vez. Retorna o id do movimento de estorno.
    """
    resp = (
        sb.table("estoque_movimentos")
        .select("id, produto_id, tipo, quantidade, custo_unitario")
        .eq("id", movimento_id)
        .execute()
    )
    if not resp.data:
        raise ValueError("Retirada não encontrada.")
    mov = resp.data[0]
    if mov.get("tipo") != TIPO_RETIRADA_PESSOAL:
        raise ValueError("O movimento informado não é uma retirada pessoal.")

    motivo = f"{PREFIXO_ESTORNO_RETIRADA}{movimento_id}"
    ja_estornada = (
        sb.table("estoque_movimentos")
        .select("id")
        .eq("tipo", "ajuste")
        .eq("motivo", motivo)
        .limit(1)
        .execute()
    )
    if ja_estornada.data:
        raise ValueError("Esta retirada já foi estornada.")

    return registrar_movimento(
        sb,
        produto_id=mov["produto_id"],
        quantidade=abs(float(mov["quantidade"])),
        tipo="ajuste",
        data_movimento=data_movimento,
        motivo=motivo,
        operacao_id=operacao_id,
        fluxo="retirada_pessoal_estorno",
        custo_unitario=mov.get("custo_unitario"),
        realizado_por="Automático",
    )

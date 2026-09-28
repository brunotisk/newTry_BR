"""
Log das inserções/ajustes automáticos feitos pelo sistema (tabela
`logs_automacao`).

Cada etapa de um pipeline (cadastro de produto, ajuste de estoque, ajuste de
movimentação/ledger) grava uma linha aqui. Todas as etapas de uma mesma
execução (ex.: uma NF-e inteira, ou uma venda) compartilham o mesmo
`operacao_id` (uuid), o que permite reconstruir a esteira completa na tela
de administração:

    Importar compras   -> cadastro_produto -> ajusta_estoque -> ajusta_movimentacao
    Importar vendas     -> ajusta_estoque -> ajusta_movimentacao
    Venda manual         -> ajusta_estoque -> ajusta_movimentacao

O usuário responsável é capturado automaticamente de `st.session_state.user`
(a mesma sessão que auth.py usa para o login) — quem chama registrar_movimento
/registrar_ajuste não precisa passar nada a mais para isso funcionar. Quando
não há sessão Streamlit ativa e o ambiente também não está em modo
SKIP_AUTH=True, o campo fica em branco; em modo SKIP_AUTH=True (bypass de
login para desenvolvimento) a ação é atribuída a "admin".

Importante: uma falha ao gravar o log NUNCA pode interromper a operação de
negócio (não queremos perder uma venda ou uma compra porque a tabela de log
deu erro). Por isso toda exceção aqui é silenciada.
"""
from __future__ import annotations
import os
from supabase import Client

NOME_TABELA = "logs_automacao"

# Rótulos amigáveis usados na tela de administração.
FLUXOS = {
    "compra_importacao": "Importar Compras (XML)",
    "compra_exclusao": "Exclusão de Compra",
    "venda_importacao": "Importar Vendas (Excel)",
    "venda_manual": "Venda Manual",
    "venda_edicao": "Edição de Venda",
    "venda_exclusao": "Exclusão de Venda",
    "ajuste_manual": "Ajuste Manual de Estoque",
    "outro": "Outro",
}

ETAPAS = {
    "cadastro_produto": "Cadastro de produto",
    "ajusta_estoque": "Ajusta estoque",
    "ajusta_movimentacao": "Ajusta movimentação",
}


def _usuario_atual() -> str | None:
    """Lê o e-mail do usuário logado em st.session_state.user (definido em
    auth.py). Fora de uma sessão Streamlit ou sem login retorna None — exceto
    quando o ambiente está rodando com SKIP_AUTH=True (bypass de login para
    desenvolvimento), caso em que a ação é atribuída a "admin"."""
    try:
        import streamlit as st
        user = st.session_state.get("user")
        if user is not None:
            email = user.get("email") if isinstance(user, dict) else getattr(user, "email", None)
            if email:
                return email
    except Exception:
        pass

    if os.getenv("SKIP_AUTH", "").strip().lower() in {"1", "true", "yes"}:
        return "admin"

    return None


def registrar_log(
    sb: Client,
    operacao_id: str,
    fluxo: str,
    etapa: str,
    sucesso: bool,
    produto_id: int | None = None,
    compra_id: int | None = None,
    venda_id: int | None = None,
    quantidade: float | None = None,
    mensagem: str | None = None,
    usuario: str | None = None,
) -> None:
    usuario = usuario or _usuario_atual()
    try:
        sb.table(NOME_TABELA).insert({
            "operacao_id": operacao_id,
            "fluxo": fluxo,
            "etapa": etapa,
            "sucesso": sucesso,
            "produto_id": produto_id,
            "compra_id": compra_id,
            "venda_id": venda_id,
            "quantidade": float(quantidade) if quantidade is not None else None,
            "mensagem": (mensagem or "")[:1000],
            "usuario": usuario,
        }).execute()
    except Exception:
        # Log é observabilidade, não pode derrubar o fluxo principal.
        pass

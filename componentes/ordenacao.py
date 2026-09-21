from pathlib import Path
import streamlit as st
import streamlit.components.v1 as components

_FRONTEND = Path(__file__).parent / "ordenacao_frontend"
_ordenacao = components.declare_component("ordenacao", path=str(_FRONTEND))


def ordenacao(
    colunas,
    label="Ordenação:",
    campo_padrao=None,
    decrescente_padrao=False,
    key="ordenacao",
):
    """Componente de ordenação reutilizável, no mesmo visual do busca_produto:
    label + pílulas "Crescente"/"Decrescente" na linha de cima, e logo abaixo
    uma caixa de seleção (mesmo estilo de .control/.selectbox do componente
    de busca) com o campo pelo qual ordenar.

    `colunas` define o que aparece na caixa de seleção, e é isso que torna o
    componente reaproveitável entre telas diferentes — cada tela passa suas
    próprias colunas, no formato:

        colunas = [
            {"chave": "codigo_interno", "rotulo": "Cod. Produto"},
            {"chave": "saldo", "rotulo": "Qtde. Estoque"},
            {"chave": "preco_venda", "rotulo": "Preço Venda"},
            {"chave": "data_ultima_compra", "rotulo": "Data Ult. Compra"},
        ]

    `campo_padrao`: chave inicialmente selecionada (padrão: a primeira de `colunas`).
    `decrescente_padrao`: direção inicial (padrão: crescente).
    `key`: precisa ser único por instância na página (permite usar o mesmo
    componente em várias tabelas/telas ao mesmo tempo).

    Retorna uma tupla (campo, decrescente):
      - campo: a "chave" da coluna selecionada (ou None se `colunas` vier vazio)
      - decrescente: bool — True quando o modo "Decrescente" está ativo
    """
    if not colunas:
        return None, False

    campo_padrao = campo_padrao or colunas[0]["chave"]

    # O componente em si é "sem estado": a cada rerun, montamos os args a
    # partir do último valor que o próprio componente devolveu (Streamlit já
    # guarda isso automaticamente em st.session_state[key]). Assim o
    # JavaScript nunca precisa adivinhar o estado atual — ele só reflete o
    # que o Python manda em "args" a cada render.
    estado_atual = st.session_state.get(key)
    if isinstance(estado_atual, dict):
        campo_atual = estado_atual.get("campo") or campo_padrao
        decrescente_atual = bool(estado_atual.get("decrescente", decrescente_padrao))
    else:
        campo_atual = campo_padrao
        decrescente_atual = bool(decrescente_padrao)

    resultado = _ordenacao(
        label=label,
        colunas=colunas,
        campo=campo_atual,
        decrescente=decrescente_atual,
        key=key,
        default={"campo": campo_atual, "decrescente": decrescente_atual},
        height=76,
    )

    if isinstance(resultado, dict):
        campo = resultado.get("campo") or campo_atual
        decrescente = bool(resultado.get("decrescente", decrescente_atual))
        return campo, decrescente

    return campo_atual, decrescente_atual

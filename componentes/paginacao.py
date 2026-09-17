import math
import streamlit as st


OPCOES_ITENS_POR_PAGINA = [50, 25, 10]


def _alterar_itens_por_pagina(chave: str) -> None:
    """Sincroniza o widget com o estado persistente e volta para a página 1."""
    widget_key = f"select_itens_por_pagina_{chave}"
    state_key = f"itens_por_pagina_{chave}"
    st.session_state[state_key] = int(st.session_state[widget_key])
    st.session_state[f"pagina_atual_{chave}"] = 1


def _alterar_pagina(chave: str) -> None:
    """Sincroniza a página digitada com o estado usado pela tela."""
    widget_key = f"input_pagina_{chave}"
    state_key = f"pagina_atual_{chave}"
    st.session_state[state_key] = int(st.session_state[widget_key])


def render_paginacao(
    chave: str,
    total_itens: int,
    itens_por_pagina: int = 50,
    *,
    mostrar_contagem_superior: bool = False,
    mostrar_contagem_inferior: bool = True,
    permitir_seletor: bool = True,
) -> int:
    """Renderiza a paginação reutilizável.

    O estado da quantidade de itens por página é separado do estado dos widgets.
    Isso evita que uma mudança de página faça o selectbox voltar para 50.
    """
    state_key = f"pagina_atual_{chave}"
    itens_key = f"itens_por_pagina_{chave}"
    input_key = f"input_pagina_{chave}"
    select_key = f"select_itens_por_pagina_{chave}"

    # Estado persistente da quantidade de itens por página.
    if itens_key not in st.session_state:
        valor_inicial = (
            itens_por_pagina
            if itens_por_pagina in OPCOES_ITENS_POR_PAGINA
            else 50
        )
        st.session_state[itens_key] = valor_inicial

    selecionado = int(st.session_state[itens_key])
    if selecionado not in OPCOES_ITENS_POR_PAGINA:
        selecionado = 50
        st.session_state[itens_key] = selecionado

    # Mantém o valor do widget sincronizado sem sobrescrever uma seleção feita pelo usuário.
    if permitir_seletor and select_key not in st.session_state:
        st.session_state[select_key] = selecionado

    total_paginas = max(1, math.ceil(total_itens / selecionado))

    if state_key not in st.session_state:
        st.session_state[state_key] = 1

    pagina_atual = max(
        1,
        min(int(st.session_state[state_key]), total_paginas),
    )
    st.session_state[state_key] = pagina_atual

    if mostrar_contagem_superior:
        st.caption(
            f"Exibindo página {pagina_atual} de {total_paginas} "
            f"({total_itens} registros no total)."
        )

    if not mostrar_contagem_inferior:
        return pagina_atual

    # Sem divider: a separação visual da tabela já é suficiente.
    col_info, col_nav, col_itens = st.columns(
        [2.5, 1.0, 1.0],
        vertical_alignment="bottom",
    )

    with col_info:
        st.caption(
            f"Exibindo página {pagina_atual} de {total_paginas} "
            f"({total_itens} registros no total)."
        )

    with col_nav:
        # Mantém o widget em uma largura menor, sem ocupar toda a coluna.
        st.number_input(
            f"Página ({pagina_atual} de {total_paginas})",
            min_value=1,
            max_value=total_paginas,
            value=pagina_atual,
            step=1,
            key=input_key,
            on_change=_alterar_pagina,
            args=(chave,),
        )

    with col_itens:
        if permitir_seletor:
            st.selectbox(
                "Itens por página",
                options=OPCOES_ITENS_POR_PAGINA,
                key=select_key,
                on_change=_alterar_itens_por_pagina,
                args=(chave,),
            )
        else:
            st.caption(f"Itens por página: {selecionado}")

    return int(st.session_state[state_key])


def get_itens_por_pagina(chave: str, padrao: int = 50) -> int:
    """Retorna a quantidade de itens atualmente selecionada para uma seção."""
    valor = st.session_state.get(f"itens_por_pagina_{chave}", padrao)
    return int(valor) if valor in OPCOES_ITENS_POR_PAGINA else padrao


def reset_paginacao(chave: str) -> None:
    """Volta uma paginação específica para a primeira página."""
    st.session_state[f"pagina_atual_{chave}"] = 1

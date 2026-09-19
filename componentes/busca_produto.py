from pathlib import Path
import streamlit as st
import streamlit.components.v1 as components

_FRONTEND = Path(__file__).parent / "busca_produto_frontend"
_busca_produto = components.declare_component("busca_produto", path=str(_FRONTEND))

# Mesmo limite que antes era aplicado no JavaScript (options.slice(0, 100)).
_LIMITE_OPCOES = 100


def _normalizar_codigo(valor):
    texto = str(valor or "").strip()
    if texto.isdigit():
        return texto.lstrip("0") or "0"
    return texto.casefold()


def _normalizar_texto(valor):
    return str(valor or "").casefold()


def _montar_opcoes(produtos, mostrar_saldo):
    opcoes = []
    for produto in produtos or []:
        codigo = str(produto.get("codigo_interno") or "")
        descricao = str(produto.get("descricao") or "")
        saldo = produto.get("saldo", 0)
        try:
            saldo_txt = f"{float(saldo):g}"
        except (TypeError, ValueError):
            saldo_txt = str(saldo)

        rotulo = f"{codigo} — {descricao}"
        if mostrar_saldo:
            rotulo += f" (saldo: {saldo_txt})"

        opcoes.append({
            "id": produto.get("id"),
            "codigo": codigo,
            "descricao": descricao,
            "rotulo": rotulo,
        })
    return opcoes


def _filtrar_opcoes(opcoes, termo, buscar_descricao):
    """Reproduz exatamente a regra de busca que antes rodava no navegador:
    prefixo do código interno (ignorando zeros à esquerda) no modo padrão,
    ou busca flexível na descrição quando buscar_descricao=True.

    A diferença é que agora isso roda aqui no servidor, então só enviamos ao
    componente as opções que realmente batem com o termo digitado — em vez
    do catálogo inteiro a cada tecla.
    """
    termo = str(termo or "").strip()
    if not termo:
        return opcoes

    if buscar_descricao:
        alvo = _normalizar_texto(termo)
        return [o for o in opcoes if alvo in _normalizar_texto(o["descricao"])]

    alvo = _normalizar_codigo(termo)
    return [o for o in opcoes if _normalizar_codigo(o["codigo"]).startswith(alvo)]


def busca_produto(
    produtos,
    label="Produto",
    placeholder=None,
    key="busca_produto",
    buscar_descricao=False,
    mostrar_saldo=True,
    valor_selecionado=None,
    termo="",
):
    """Dropdown pesquisável com visual de selectbox e busca de produto precisa,
    com o toggle "Buscar descrição" embutido no próprio componente (ao lado
    do rótulo).

    No modo padrão, o termo digitado filtra pelo início do código interno.
    A comparação ignora zeros à esquerda (24340 == 0024340).
    No modo descrição (alternado pelo usuário dentro do componente), a busca
    passa a ser flexível pela descrição. A filtragem roda no servidor
    (Python); só o resultado relevante é enviado ao componente.

    `buscar_descricao` aqui é só o valor INICIAL/persistido entre execuções
    (ex.: vindo de st.session_state) — o usuário troca o modo clicando no
    toggle dentro do componente, e o valor atual fica disponível depois da
    chamada em `st.session_state[f"{key}_buscar_descricao"]`.

    `placeholder` é opcional: se não for informado, o próprio componente
    escolhe o texto padrão de acordo com o modo de busca atual (ver
    index.html).

    Retorna o ID do produto selecionado ou None.
    """
    opcoes = _montar_opcoes(produtos, mostrar_saldo)

    # O parâmetro "termo" recebido aqui normalmente vem um passo atrasado:
    # é o texto de ANTES da última tecla digitada, porque quem chama esta
    # função (produtos.py) o lê de uma cópia em session_state que só é
    # atualizada no FINAL desta mesma função, na execução anterior.
    # Para filtrar com o termo realmente digitado agora, usamos o valor mais
    # recente que o próprio Streamlit já guarda para este componente (por
    # causa do "key") — ele reflete exatamente o que acabou de chegar do
    # navegador e disparou esta execução.
    estado_atual = st.session_state.get(key)
    termo_busca = estado_atual.get("termo") if isinstance(estado_atual, dict) else None
    if termo_busca is None:
        termo_busca = termo

    # Mesma lógica do termo: o modo de busca (código/descrição) agora é
    # alternado dentro do próprio componente, então o valor mais recente
    # (inclusive o clique que disparou esta execução) já está em
    # st.session_state[key] antes mesmo de chamarmos o componente de novo.
    buscar_descricao_atual = estado_atual.get("buscar_descricao") if isinstance(estado_atual, dict) else None
    if buscar_descricao_atual is None:
        buscar_descricao_atual = buscar_descricao

    # Resolvido a partir da lista completa (não da lista já filtrada pela
    # busca), para que o campo continue mostrando o produto selecionado
    # mesmo quando o termo atual (ex.: texto de uma busca anterior) não
    # bate mais com ele.
    rotulo_selecionado = next(
        (o["rotulo"] for o in opcoes if valor_selecionado is not None and o["id"] == valor_selecionado),
        None,
    )

    opcoes_filtradas = _filtrar_opcoes(opcoes, termo_busca, buscar_descricao_atual)[:_LIMITE_OPCOES]

    resultado = _busca_produto(
        label=label,
        placeholder=placeholder,
        opcoes=opcoes_filtradas,
        rotulo_selecionado=rotulo_selecionado,
        buscar_descricao=bool(buscar_descricao_atual),
        valor_selecionado=valor_selecionado,
        termo=termo,
        key=key,
        default={"id": valor_selecionado, "termo": "", "buscar_descricao": bool(buscar_descricao), "aberto": False},
        height=48,
    )

    if isinstance(resultado, dict):
        st.session_state[f"{key}_termo"] = resultado.get("termo", "")
        st.session_state[f"{key}_aberto"] = bool(resultado.get("aberto", False))
        st.session_state[f"{key}_buscar_descricao"] = bool(
            resultado.get("buscar_descricao", buscar_descricao_atual)
        )
        return resultado.get("id")
    return resultado

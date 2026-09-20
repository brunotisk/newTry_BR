from pathlib import Path
import streamlit as st
import streamlit.components.v1 as components

_FRONTEND = Path(__file__).parent / "contador_quantidade_frontend"
_contador = components.declare_component("contador_quantidade", path=str(_FRONTEND))


def contador_quantidade(
    valor=1,
    min_valor=1,
    bloqueado=True,
    label="Quantidade",
    key="contador_quantidade",
):
    """Contador de quantidade com - / valor / + / cadeado.

    O estado do valor e do bloqueio fica dentro do componente, evitando a
    alteração de st.session_state depois que o widget já foi instanciado.
    Retorna {"valor": int, "bloqueado": bool}.
    """
    try:
        valor = max(int(valor), int(min_valor))
    except (TypeError, ValueError):
        valor = int(min_valor)

    try:
        min_valor = int(min_valor)
    except (TypeError, ValueError):
        min_valor = 1
    min_valor = max(1, min_valor)
    valor = max(valor, min_valor)

    resultado = _contador(
        valor=valor,
        min_valor=min_valor,
        bloqueado=bool(bloqueado),
        label=label,
        key=key,
        default={"valor": valor, "bloqueado": bool(bloqueado)},
        height=68,
    )

    if isinstance(resultado, dict):
        try:
            resultado_valor = max(min_valor, int(resultado.get("valor", valor)))
        except (TypeError, ValueError):
            resultado_valor = valor
        return {
            "valor": resultado_valor,
            "bloqueado": bool(resultado.get("bloqueado", bloqueado)),
        }

    return {"valor": valor, "bloqueado": bool(bloqueado)}

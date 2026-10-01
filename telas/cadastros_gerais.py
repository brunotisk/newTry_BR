"""Telas padronizadas para os cadastros de apoio do sistema."""
from __future__ import annotations

import streamlit as st

from db import supabase

_dialog = getattr(st, "dialog", None) or st.experimental_dialog


CADASTROS = {
    "categorias": {
        "tabela": "categorias_produtos",
        "titulo": "Categorias de produtos",
        "singular": "categoria",
        "ordem": "ordem_exibicao",
        "adicionar_em_popup": True,
        "editar_em_popup": True,
        "campos": [
            {"nome": "categoria", "rotulo": "Categoria", "obrigatorio": True,
             "placeholder": "Ex.: Brinco"},
            {"nome": "banho", "rotulo": "Banho", "placeholder": "Ex.: Ouro 18K"},
            {"nome": "ordem_exibicao", "rotulo": "Ordem de exibição", "tipo": "numero", "padrao": 1},
            {"nome": "ativo", "rotulo": "Ativo", "tipo": "booleano", "padrao": True},
        ],
    },
    "canais": {
        "tabela": "canais_venda",
        "titulo": "Canais de venda",
        "singular": "canal de venda",
        "ordem": "ordem_exibicao",
        "ordem_secundaria": "nome",
        "adicionar_em_popup": True,
        "editar_em_popup": True,
        "campos": [
            {"nome": "nome", "rotulo": "Nome", "obrigatorio": True,
             "placeholder": "Ex.: Salão, Feira, Online..."},
            {"nome": "ordem_exibicao", "rotulo": "Ordem de exibição", "tipo": "numero_opcional"},
            {"nome": "ativo", "rotulo": "Ativo", "tipo": "booleano", "padrao": True},
        ],
    },
    "status": {
        "tabela": "status_venda",
        "titulo": "Status de venda",
        "singular": "status de venda",
        "ordem": "ordem_exibicao",
        "ordem_secundaria": "nome",
        "adicionar_em_popup": True,
        "editar_em_popup": True,
        "campos": [
            {"nome": "nome", "rotulo": "Nome", "obrigatorio": True,
             "placeholder": "Ex.: Pendente, Pago, Cancelado..."},
            {"nome": "ordem_exibicao", "rotulo": "Ordem de exibição", "tipo": "numero_opcional"},
            {"nome": "ativo", "rotulo": "Ativo", "tipo": "booleano", "padrao": True},
        ],
    },
    "feiras": {
        "tabela": "detalhes_feira",
        "titulo": "Detalhes de feira",
        "singular": "feira",
        "ordem": "ordem_exibicao",
        "ordem_secundaria": "nome_feira",
        "adicionar_em_popup": True,
        "editar_em_popup": True,
        "campos": [
            {"nome": "nome_feira", "rotulo": "Nome da feira", "obrigatorio": True},
            {"nome": "endereco_feira", "rotulo": "Endereço da feira"},
            {"nome": "pessoa_contato_feira", "rotulo": "Pessoa de contato"},
            {"nome": "tel_contato_feira", "rotulo": "Telefone de contato"},
            {"nome": "ordem_exibicao", "rotulo": "Ordem de exibição", "tipo": "numero_opcional"},
            {"nome": "ativo", "rotulo": "Ativo", "tipo": "booleano", "padrao": True},
        ],
    },
    "formas_pagamento": {
        "tabela": "formas_pagamento",
        "titulo": "Formas de pagamento",
        "singular": "forma de pagamento",
        "ordem": "ordem_exibicao",
        "ordem_secundaria": "descricao",
        "adicionar_em_popup": True,
        "editar_em_popup": True,
        "campos": [
            {"nome": "descricao", "rotulo": "Descrição", "obrigatorio": True,
             "placeholder": "Ex.: Pix, Dinheiro, Cartão de crédito..."},
            {"nome": "ordem_exibicao", "rotulo": "Ordem de exibição", "tipo": "numero_opcional"},
            {"nome": "ativo", "rotulo": "Ativo", "tipo": "booleano", "padrao": True},
        ],
    },
    "parceiros": {
        "tabela": "parceiros",
        "titulo": "Parceiros",
        "singular": "parceiro",
        "ordem": "nome",
        "adicionar_em_popup": True,
        "editar_em_popup": True,
        "campos": [
            {"nome": "nome", "rotulo": "Nome", "obrigatorio": True,
             "placeholder": "Ex.: Maria Revendedora"},
            {"nome": "telefone", "rotulo": "Telefone"},
            {"nome": "email", "rotulo": "E-mail"},
            {"nome": "observacao", "rotulo": "Observação"},
            {"nome": "canal_id", "rotulo": "Canal de venda", "tipo": "selecao_fk",
             "opcoes_tabela": "canais_venda", "opcoes_rotulo_campo": "nome",
             "unico_em": "parceiros", "obrigatorio": True},
            {"nome": "percentual_comissao", "rotulo": "% Comissão",
             "tipo": "numero_decimal", "padrao": 0},
            {"nome": "ativo", "rotulo": "Ativo", "tipo": "booleano", "padrao": True},
        ],
    },
}


def _opcoes_fk(tabela: str, campo_rotulo: str) -> list[dict]:
    """Opções (id + rótulo) de uma tabela referenciada, para campos do tipo
    'selecao_fk'."""
    try:
        resp = (
            supabase.table(tabela)
            .select(f"id, {campo_rotulo}")
            .order(campo_rotulo)
            .execute()
        )
        return resp.data or []
    except Exception:
        return []


def _ids_fk_em_uso(tabela: str, campo_fk: str, excluir_id: int | None) -> set:
    """Ids já usados em `campo_fk` de `tabela` — usado para esconder, no
    selectbox, opções que violariam uma constraint UNIQUE (ex.: cada canal
    só pode ter um parceiro)."""
    try:
        query = supabase.table(tabela).select(f"id, {campo_fk}")
        resp = query.execute()
        registros = resp.data or []
    except Exception:
        return set()
    return {
        r[campo_fk]
        for r in registros
        if r.get(campo_fk) is not None and r["id"] != excluir_id
    }


def _chave_edicao(chave: str) -> str:
    return f"cadastro_geral_edicao_{chave}"


def _valor_campo(campo: dict, registro: dict | None):
    if registro is not None and campo["nome"] in registro:
        valor = registro[campo["nome"]]
    else:
        valor = campo.get("padrao", False if campo.get("tipo") == "booleano" else "")

    if campo.get("tipo") == "numero":
        return int(valor or 0)
    if campo.get("tipo") == "numero_decimal":
        return float(valor or 0)
    if campo.get("tipo") == "booleano":
        return bool(valor)
    return valor or ""


def _renderizar_formulario(
    chave: str, config: dict, registro: dict | None, em_popup: bool = False
):
    editando = registro is not None
    acao = "Editar" if editando else "Adicionar"
    if not em_popup:
        st.subheader(f"{acao} {config['singular']}")

    with st.form(f"form_cadastro_geral_{chave}"):
        valores = {}
        campos = config["campos"]
        # Campos descritivos ocupam a largura toda. Ordem e ativo ficam juntos
        # na última linha, seguindo o padrão visual dos popups de cadastro.
        for campo in (campo for campo in campos if campo.get("tipo") is None):
            valores[campo["nome"]] = st.text_input(
                campo["rotulo"], value=_valor_campo(campo, registro),
                placeholder=campo.get("placeholder", ""),
            )

        campos_controle = [campo for campo in campos if campo.get("tipo") is not None]
        if campos_controle:
            colunas = st.columns(len(campos_controle), vertical_alignment="center")
            for coluna, campo in zip(colunas, campos_controle):
                with coluna:
                    valor = _valor_campo(campo, registro)
                    if campo.get("tipo") == "booleano":
                        # O toggle ocupa apenas o centro da sua coluna para
                        # manter o alinhamento visual com Ordem de exibição.
                        margem_esq, centro, margem_dir = st.columns([1, 2, 1])
                        with centro:
                            valores[campo["nome"]] = st.toggle(campo["rotulo"], value=valor)
                    elif campo.get("tipo") == "numero":
                        valores[campo["nome"]] = st.number_input(
                            campo["rotulo"], min_value=0, step=1, value=valor
                        )
                    elif campo.get("tipo") == "numero_decimal":
                        valores[campo["nome"]] = st.number_input(
                            campo["rotulo"], min_value=0.0, max_value=100.0,
                            step=0.1, format="%.2f", value=valor,
                        )
                    elif campo.get("tipo") == "selecao_fk":
                        opcoes = _opcoes_fk(campo["opcoes_tabela"], campo["opcoes_rotulo_campo"])
                        if campo.get("unico_em"):
                            em_uso = _ids_fk_em_uso(
                                campo["unico_em"], campo["nome"],
                                excluir_id=(registro["id"] if registro else None),
                            )
                            opcoes = [o for o in opcoes if o["id"] not in em_uso]
                        rotulos = [o[campo["opcoes_rotulo_campo"]] for o in opcoes]
                        ids = [o["id"] for o in opcoes]
                        valor_atual_id = registro.get(campo["nome"]) if registro else None
                        indice_atual = ids.index(valor_atual_id) if valor_atual_id in ids else None
                        rotulo_escolhido = st.selectbox(
                            campo["rotulo"], options=rotulos, index=indice_atual,
                            placeholder=f"Selecione {campo['rotulo'].lower()}...",
                        )
                        valores[campo["nome"]] = (
                            ids[rotulos.index(rotulo_escolhido)]
                            if rotulo_escolhido is not None else None
                        )
                    elif campo.get("tipo") == "numero_opcional":
                        valores[campo["nome"]] = st.text_input(
                            f"{campo['rotulo']} (opcional)", value=str(valor),
                            placeholder="Ex.: 1",
                        )

        salvar = st.form_submit_button(
            "💾 Salvar alterações" if editando else "➕ Adicionar",
            use_container_width=True,
        )

    sufixo_cancelar = "popup" if em_popup else "pagina"
    if st.button("Cancelar", key=f"cancelar_cadastro_{chave}_{sufixo_cancelar}"):
        st.session_state[_chave_edicao(chave)] = None
        st.rerun()

    if not salvar:
        return

    dados = {}
    for campo in config["campos"]:
        nome = campo["nome"]
        valor = valores[nome]
        if campo.get("tipo") is None:
            valor = valor.strip()
            if campo.get("obrigatorio") and not valor:
                st.error(f"Informe {campo['rotulo'].lower()}.")
                return
        elif campo.get("tipo") == "numero_opcional":
            valor = valor.strip()
            if valor:
                try:
                    valor = int(valor)
                except ValueError:
                    st.error(f"{campo['rotulo']} deve ser um número inteiro.")
                    return
            else:
                valor = None
        elif campo.get("tipo") == "selecao_fk":
            if campo.get("obrigatorio") and valor is None:
                st.error(f"Selecione {campo['rotulo'].lower()}.")
                return
        dados[nome] = valor

    try:
        consulta = supabase.table(config["tabela"])
        if editando:
            consulta.update(dados).eq("id", registro["id"]).execute()
            mensagem = f"{config['titulo']} atualizado com sucesso!"
        else:
            consulta.insert(dados).execute()
            mensagem = f"{config['titulo']} adicionado com sucesso!"
        st.session_state[_chave_edicao(chave)] = None
        st.success(mensagem)
        st.rerun()
    except Exception as erro:
        st.error(f"Erro ao salvar {config['singular']}: {erro}")


def _mostrar_valor(campo: dict, valor) -> str:
    if campo.get("tipo") == "booleano":
        return "Ativo" if valor else "Inativo"
    return str(valor) if valor not in (None, "") else "-"


def _renderizar_status_ativo(valor: bool):
    """Exibe ativo/inativo como selo, em vez de texto solto na tabela."""
    classe = "cadastro-status--ativo" if valor else "cadastro-status--inativo"
    rotulo = "● Ativo" if valor else "● Inativo"
    st.markdown(
        f'<div class="cadastro-status-container"><span class="cadastro-status {classe}">{rotulo}</span></div>',
        unsafe_allow_html=True,
    )


def _abrir_popup_formulario(chave: str, config: dict, registro: dict | None = None):
    """Abre inclusão ou edição sem deslocar a tabela ao fundo."""
    acao = "Editar" if registro is not None else "Adicionar"

    @_dialog(f"{acao} {config['singular']}")
    def popup():
        _renderizar_formulario(chave, config, registro, em_popup=True)

    popup()


def renderizar_cadastro(chave: str):
    """Renderiza a tabela e o formulário sob demanda de um cadastro."""
    config = CADASTROS[chave]
    estado = _chave_edicao(chave)
    if estado not in st.session_state:
        st.session_state[estado] = None

    st.subheader(config["titulo"])
    if st.button(f"➕ Adicionar {config['singular']}", key=f"adicionar_cadastro_{chave}"):
        if config.get("adicionar_em_popup"):
            _abrir_popup_formulario(chave, config)
        else:
            st.session_state[estado] = "novo"
            st.rerun()

    try:
        nomes_campos = ", ".join(["id", *(campo["nome"] for campo in config["campos"])])
        consulta = supabase.table(config["tabela"]).select(nomes_campos).order(config["ordem"])
        if config.get("ordem_secundaria"):
            consulta = consulta.order(config["ordem_secundaria"])
        registros = consulta.execute().data or []
    except Exception as erro:
        st.error(f"Erro ao carregar {config['titulo'].lower()}: {erro}")
        return

    registro_em_edicao = None
    if isinstance(st.session_state[estado], int):
        registro_em_edicao = next(
            (item for item in registros if item["id"] == st.session_state[estado]), None
        )
        if registro_em_edicao is None:
            st.session_state[estado] = None

    if st.session_state[estado] == "novo" or registro_em_edicao is not None:
        _renderizar_formulario(chave, config, registro_em_edicao)
        st.divider()

    if not registros:
        st.info(f"Nenhum(a) {config['singular']} cadastrado(a) ainda.")
        return

    campo_ordem = next(
        (campo for campo in config["campos"] if campo["nome"] == "ordem_exibicao"), None
    )
    if campo_ordem:
        campos = [
            campo_ordem,
            *(campo for campo in config["campos"] if campo["nome"] != "ordem_exibicao"),
        ]
        # A primeira coluna mantém a largura compacta que antes era usada pelo ID.
        pesos = [1, *([2] * (len(campos) - 1)), 1]
    else:
        campos = list(config["campos"])
        pesos = [*([2] * len(campos)), 1]

    # Campos do tipo selecao_fk mostram o rótulo (ex.: nome do canal) em vez
    # do id cru salvo no banco.
    mapas_fk = {
        campo["nome"]: {
            o["id"]: o[campo["opcoes_rotulo_campo"]]
            for o in _opcoes_fk(campo["opcoes_tabela"], campo["opcoes_rotulo_campo"])
        }
        for campo in campos
        if campo.get("tipo") == "selecao_fk"
    }
    with st.container(border=True):
        st.markdown(
            """
            <style>
            .cadastro-status-container { display: flex; justify-content: center; }
            .cadastro-status {
                display: inline-flex; align-items: center; border-radius: 999px;
                padding: 0.15rem 0.55rem; font-size: 0.78rem; font-weight: 600;
            }
            .cadastro-status--ativo { background: rgba(34, 197, 94, 0.16); color: #55d68a; }
            .cadastro-status--inativo { background: rgba(148, 163, 184, 0.16); color: #aeb8c7; }
            </style>
            """,
            unsafe_allow_html=True,
        )
        cabecalho = st.columns(pesos)
        for coluna, campo in zip(cabecalho, campos):
            coluna.markdown(f"**{campo['rotulo']}**")
        cabecalho[-1].markdown("**Ações**")
        st.divider()

        for registro in registros:
            colunas = st.columns(pesos)
            for coluna, campo in zip(colunas, campos):
                with coluna:
                    valor = registro.get(campo["nome"])
                    if campo.get("tipo") == "booleano":
                        _renderizar_status_ativo(bool(valor))
                    elif campo.get("tipo") == "selecao_fk":
                        st.write(mapas_fk.get(campo["nome"], {}).get(valor, "-"))
                    else:
                        st.write(_mostrar_valor(campo, valor))
            if colunas[-1].button("✏️ Editar", key=f"editar_cadastro_{chave}_{registro['id']}"):
                if config.get("editar_em_popup"):
                    _abrir_popup_formulario(chave, config, registro)
                else:
                    st.session_state[estado] = registro["id"]
                    st.rerun()


def tela_categorias():
    renderizar_cadastro("categorias")


def tela_cadastros_gerais():
    st.header("🧩 Cadastros Gerais")
    st.caption("Gerencie as listas de apoio usadas nos formulários de venda.")

    aba_canais, aba_status, aba_feiras, aba_formas, aba_parceiros = st.tabs(
        ["Canais de Venda", "Status de Venda", "Detalhes Feira", "Formas de Pagamento", "Parceiros"]
    )
    with aba_canais:
        renderizar_cadastro("canais")
    with aba_status:
        renderizar_cadastro("status")
    with aba_feiras:
        renderizar_cadastro("feiras")
    with aba_formas:
        renderizar_cadastro("formas_pagamento")
    with aba_parceiros:
        renderizar_cadastro("parceiros")

"""Estoque > Kits: receitas, montagem e desmontagem.

Um kit é um produto comum, com estoque e preço próprios. Aqui você:
  - Montar:     transforma componentes em kits (baixa os componentes, sobe o kit);
  - Receitas:   cria um kit novo ou edita do que ele é feito;
  - Histórico:  vê as montagens e desmonta, se preciso.

Toda a regra está em servicos/kits.py; esta tela só coleta os dados e mostra o resultado.
Requer a migration 019_kits_montagem.sql.
"""
from datetime import date

import streamlit as st

from db import supabase
from servicos import kits as kits_srv
from servicos.estoque import SaldoInsuficienteError, obter_custo_unitario
from servicos.supabase_admin import get_client
from servicos.vendas_import import normalizar_codigo_interno

_dialog = getattr(st, "dialog", None) or st.experimental_dialog

CHAVE_MSG_OK = "kits_msg_ok"
CHAVE_RASCUNHO = "kits_rascunho"
CHAVE_RASCUNHO_REF = "kits_rascunho_ref"
CHAVE_AVISO = "kits_aviso_item"


# ----------------------------------------------------------------------
# Formatação e utilidades
# ----------------------------------------------------------------------
def _fmt_qtd(valor) -> str:
    return f"{float(valor or 0):g}".replace(".", ",")


def _fmt_moeda(valor) -> str:
    return "R$ " + f"{float(valor or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _data_br(valor) -> str:
    texto = str(valor or "")[:10]
    try:
        return date.fromisoformat(texto).strftime("%d/%m/%Y")
    except ValueError:
        return "-"


def _usuario() -> str | None:
    return getattr(st.session_state.get("user"), "email", None)


def _rotulo_kit(k: dict) -> str:
    return f"{k['codigo_interno']} — {k['descricao']}"


def _listar_kits() -> list:
    return (
        supabase.table("produtos")
        .select("id, codigo_interno, descricao")
        .eq("eh_kit", True)
        .order("codigo_interno")
        .execute()
        .data
        or []
    )


def _reservado(atualizar: bool = False) -> dict:
    """{produto_id: peças reservadas em maletas ativas}: não podem ir para um kit.
    Usa o mesmo cálculo da tela de Parceiros; se ele falhar, segue sem reservas."""
    try:
        from telas.parceiros.parceiros_maleta import _carregar_produtos_disponiveis
        if atualizar:
            _carregar_produtos_disponiveis.clear()
        return {
            pid: float(p.get("comprometido") or 0)
            for pid, p in _carregar_produtos_disponiveis().items()
        }
    except Exception:
        return {}


def _limpar_cache_busca() -> None:
    """O produto novo (kit) deve aparecer logo na busca da tela de Produtos."""
    try:
        from telas.produtos import _produtos_busca_cache
        _produtos_busca_cache.clear()
    except Exception:
        pass


# ----------------------------------------------------------------------
# Montar
# ----------------------------------------------------------------------
def _secao_montar():
    try:
        kits = _listar_kits()
    except Exception as e:
        st.error(f"Erro ao carregar os kits (a migration 019 foi aplicada?): {e}")
        return
    if not kits:
        st.info("Nenhum kit cadastrado ainda. Crie o primeiro na seção **Receitas**.")
        return

    por_id = {k["id"]: k for k in kits}
    kit_id = st.selectbox(
        "Kit", list(por_id), format_func=lambda i: _rotulo_kit(por_id[i]), key="kits_montar_kit"
    )

    try:
        receita = kits_srv.obter_receita(supabase, kit_id)
    except Exception as e:
        st.error(f"Erro ao carregar a receita: {e}")
        return
    if not receita:
        st.warning("Este kit não tem receita. Cadastre os componentes na seção **Receitas**.")
        return

    c_qtd, c_data = st.columns([1, 1.4])
    qtd = int(c_qtd.number_input("Quantidade de kits", min_value=1, value=1, step=1, key="kits_montar_qtd"))
    data_mov = c_data.date_input(
        "Data da montagem", value=date.today(), key="kits_montar_data", format="DD/MM/YYYY"
    )

    try:
        calc = kits_srv.calcular_montagem(supabase, kit_id, qtd, _reservado())
        preco_atual = kits_srv.obter_preco_venda(supabase, kit_id)
    except Exception as e:
        st.error(f"Erro ao calcular a montagem: {e}")
        return

    st.dataframe(
        [
            {
                "Código": i["codigo_interno"],
                "Componente": i["descricao"],
                "Por kit": _fmt_qtd(i["quantidade_por_kit"]),
                "Necessário": _fmt_qtd(i["necessario"]),
                "Saldo": _fmt_qtd(i["saldo"]),
                "Reservado (maleta)": _fmt_qtd(i["reservado"]),
                "Disponível": _fmt_qtd(i["disponivel"]),
                "Custo unit.": _fmt_moeda(i["custo_unitario"]) if i["custo_unitario"] else "sem custo",
                "Situação": "✅ ok" if i["falta"] <= 1e-9 else f"🔴 falta {_fmt_qtd(i['falta'])}",
            }
            for i in calc["itens"]
        ],
        use_container_width=True,
        hide_index=True,
    )
    if calc["sem_custo"]:
        st.warning(
            "Sem preço de última compra: " + ", ".join(calc["sem_custo"])
            + ". Eles entram com custo zero e o custo do kit fica subestimado."
        )

    custo = calc["custo_unitario"]
    preco = float(st.number_input(
        "Preço de venda do kit (R$)",
        min_value=0.0,
        value=float(preco_atual or round(custo * 2, 2)),
        step=1.0,
        format="%.2f",
        key=f"kits_montar_preco_{kit_id}",
        help="Vira o preço de venda sugerido no estoque do kit ao montar. "
             "Sem preço anterior, começa em 2x o custo.",
    ))

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Custo por kit", _fmt_moeda(custo))
    m2.metric("Custo do lote", _fmt_moeda(calc["custo_total"]))
    m3.metric("Margem sobre o preço", f"{(preco - custo) / preco * 100:.1f}%".replace(".", ",") if preco else "-")
    m4.metric("Máx. que dá para montar", str(calc["max_montavel"]))

    if calc["faltas"]:
        st.error("Faltam componentes disponíveis: " + "; ".join(calc["faltas"]))

    if st.button(
        f"🎁 Montar {qtd} kit(s)", type="primary", use_container_width=True,
        disabled=not calc["ok"], key="kits_montar_ok",
    ):
        novo_preco = preco if preco > 0 and abs(preco - float(preco_atual or 0)) > 0.004 else None
        try:
            r = kits_srv.montar_kit(
                get_client(), kit_id, qtd, data_mov.isoformat(),
                reservado=_reservado(atualizar=True), preco_venda=novo_preco, usuario=_usuario(),
            )
        except SaldoInsuficienteError as e:
            st.error(str(e))
            return
        except Exception as e:
            st.error(f"Não foi possível montar: {type(e).__name__}: {e}")
            return
        st.session_state[CHAVE_MSG_OK] = (
            f"{r['quantidade']} kit(s) montado(s) (montagem #{r['montagem_id']}). "
            f"Custo por kit: {_fmt_moeda(r['custo_unitario'])}."
        )
        st.rerun()


# ----------------------------------------------------------------------
# Receitas
# ----------------------------------------------------------------------
def _carregar_rascunho(kit_id: int) -> list:
    return [
        {
            "produto_id": r["componente_id"],
            "codigo": r["codigo_interno"],
            "descricao": r["descricao"],
            "qtd": int(r["quantidade"]),
            "custo": obter_custo_unitario(supabase, r["componente_id"]),
        }
        for r in kits_srv.obter_receita(supabase, kit_id)
    ]


def _adicionar_componente(codigo_txt: str, qtd: int, kit_id, rascunho: list) -> None:
    ss = st.session_state
    ss.pop(CHAVE_AVISO, None)
    codigo = normalizar_codigo_interno(codigo_txt)
    if not codigo:
        ss[CHAVE_AVISO] = ("warning", "Informe o código do componente.")
        return
    try:
        achados = (
            supabase.table("produtos").select("id, codigo_interno, descricao")
            .eq("codigo_interno", codigo).limit(1).execute().data or []
        )
        if not achados:
            ss[CHAVE_AVISO] = ("warning", f"Produto não encontrado: {codigo}.")
            return
        prod = achados[0]
        if kit_id and prod["id"] == kit_id:
            ss[CHAVE_AVISO] = ("warning", "Um kit não pode ser componente dele mesmo.")
            return
        if any(i["produto_id"] == prod["id"] for i in rascunho):
            ss[CHAVE_AVISO] = ("warning", f"{codigo} já está na receita: altere a quantidade na lista.")
            return
        rascunho.append({
            "produto_id": prod["id"],
            "codigo": prod["codigo_interno"],
            "descricao": prod.get("descricao") or "-",
            "qtd": int(qtd),
            "custo": obter_custo_unitario(supabase, prod["id"]),
        })
        ss[CHAVE_AVISO] = ("success", f"{prod['codigo_interno']} — {prod.get('descricao') or '-'} adicionado.")
    except Exception as e:
        ss[CHAVE_AVISO] = ("error", f"Erro ao buscar o produto: {e}")


def _secao_receitas():
    ss = st.session_state
    modo = st.radio(
        "Ação", ["Novo kit", "Editar receita"], horizontal=True,
        key="kits_receita_modo", label_visibility="collapsed",
    )

    kit_id = None
    if modo == "Editar receita":
        try:
            kits = _listar_kits()
        except Exception as e:
            st.error(f"Erro ao carregar os kits (a migration 019 foi aplicada?): {e}")
            return
        if not kits:
            st.info('Nenhum kit cadastrado ainda. Use "Novo kit".')
            return
        por_id = {k["id"]: k for k in kits}
        kit_id = st.selectbox(
            "Kit", list(por_id), format_func=lambda i: _rotulo_kit(por_id[i]), key="kits_receita_kit"
        )

    # O rascunho da receita é refeito sempre que muda o kit (ou o modo).
    ref = ("editar", kit_id) if kit_id else ("novo", None)
    if ss.get(CHAVE_RASCUNHO_REF) != ref:
        ss[CHAVE_RASCUNHO_REF] = ref
        ss.pop(CHAVE_AVISO, None)
        try:
            ss[CHAVE_RASCUNHO] = _carregar_rascunho(kit_id) if kit_id else []
        except Exception as e:
            ss[CHAVE_RASCUNHO] = []
            st.error(f"Erro ao carregar a receita: {e}")
    rascunho = ss[CHAVE_RASCUNHO]
    n = ss.get("kits_novo_n", 0)  # entra nas keys: limpa os campos depois de criar um kit

    codigo_txt = descricao = desc_site = ""
    if not kit_id:
        c_cod, c_desc = st.columns([1, 3])
        codigo_txt = c_cod.text_input("Código do kit *", placeholder="Ex.: 90001", key=f"kits_novo_codigo_{n}")
        descricao = c_desc.text_input("Descrição *", placeholder="Ex.: Kit Natal Colar + Bola", key=f"kits_novo_desc_{n}")
        desc_site = st.text_input("Descrição do site (opcional)", key=f"kits_novo_site_{n}")
        st.caption("Use um código que ainda não exista. Códigos numéricos ganham zeros à esquerda (7 dígitos).")

    st.markdown("**Componentes do kit**")
    with st.form(f"kits_form_comp_{ref[0]}_{ref[1]}_{n}", clear_on_submit=True):
        c_cod, c_qtd, c_btn = st.columns([3, 1.3, 1.5], vertical_alignment="bottom")
        cod = c_cod.text_input("Código do componente", placeholder="Ex.: 11389")
        qtd = c_qtd.number_input("Qtd por kit", min_value=1, value=1, step=1)
        adicionar = c_btn.form_submit_button("➕ Adicionar", use_container_width=True)
    if adicionar:
        _adicionar_componente(cod, int(qtd), kit_id, rascunho)

    aviso = ss.get(CHAVE_AVISO)
    if aviso:
        getattr(st, aviso[0])(aviso[1])

    if not rascunho:
        st.info("Nenhum componente na receita ainda.")
    else:
        colunas = [1.3, 3.4, 1.2, 1.4, 0.7]
        with st.container(border=True):
            for col, titulo in zip(st.columns(colunas), ["Código", "Componente", "Qtd/kit", "Custo unit.", ""]):
                col.markdown(f"**{titulo}**")
            remover = None
            for item in rascunho:
                c_cod, c_desc, c_q, c_cu, c_rm = st.columns(colunas, vertical_alignment="center")
                c_cod.write(item["codigo"])
                c_desc.write(item["descricao"])
                item["qtd"] = int(c_q.number_input(
                    "Qtd", min_value=1, value=int(item["qtd"]), step=1,
                    key=f"kits_rq_{ref[0]}_{ref[1]}_{item['produto_id']}", label_visibility="collapsed",
                ))
                c_cu.write(_fmt_moeda(item["custo"]) if item["custo"] else "sem custo")
                if c_rm.button("🗑️", key=f"kits_rm_{ref[0]}_{ref[1]}_{item['produto_id']}", help="Tirar da receita"):
                    remover = item
            if remover is not None:
                rascunho.remove(remover)
                st.rerun()

        custo_est = sum(i["qtd"] * (i["custo"] or 0) for i in rascunho)
        sem_custo = sum(1 for i in rascunho if not i["custo"])
        st.caption(
            f"{len(rascunho)} componente(s) · custo estimado por kit {_fmt_moeda(custo_est)}"
            + (f" · {sem_custo} sem custo cadastrado" if sem_custo else "")
        )

    if not st.button(
        "💾 Criar kit" if not kit_id else "💾 Salvar receita",
        type="primary", use_container_width=True, disabled=not rascunho, key="kits_receita_salvar",
    ):
        return

    componentes = [{"produto_id": i["produto_id"], "quantidade": i["qtd"]} for i in rascunho]

    if kit_id:
        try:
            kits_srv.definir_receita(get_client(), kit_id, componentes)
        except Exception as e:
            st.error(f"Não foi possível salvar a receita: {e}")
            return
        ss[CHAVE_MSG_OK] = "Receita atualizada. As próximas montagens já usam a nova receita."
        ss.pop(CHAVE_RASCUNHO_REF, None)
        st.rerun()
        return

    codigo = normalizar_codigo_interno(codigo_txt)
    descricao = descricao.strip()
    if not codigo:
        st.error("Informe o código do kit.")
        return
    if not descricao:
        st.error("Informe a descrição do kit.")
        return
    if any(i["codigo"] == codigo for i in rascunho):
        st.error("O kit não pode ser componente dele mesmo.")
        return

    try:
        existente = (
            supabase.table("produtos").select("id, descricao")
            .eq("codigo_interno", codigo).limit(1).execute().data or []
        )
        if existente:
            st.error(f"Já existe o produto {codigo} — {existente[0].get('descricao')}.")
            return
        novo = supabase.table("produtos").insert({
            "codigo_interno": codigo,
            "descricao": descricao,
            "produto_descricao_site": desc_site.strip() or None,
            "unidade": "UN",
            "eh_kit": True,
            "nr_compras": 0,
        }).execute().data[0]
        try:
            kits_srv.definir_receita(get_client(), novo["id"], componentes)
        except Exception:
            try:  # não deixa um kit sem receita para trás
                supabase.table("produtos").delete().eq("id", novo["id"]).execute()
            except Exception:
                pass
            raise
    except Exception as e:
        st.error(f"Não foi possível criar o kit: {e}")
        return

    _limpar_cache_busca()
    ss[CHAVE_MSG_OK] = (
        f"Kit {codigo} criado. Monte o primeiro lote na seção Montar; "
        "a foto pode ser enviada em Produtos (✏️ da linha)."
    )
    ss["kits_novo_n"] = n + 1
    ss.pop(CHAVE_RASCUNHO_REF, None)
    st.rerun()


# ----------------------------------------------------------------------
# Histórico e desmontagem
# ----------------------------------------------------------------------
@_dialog("↩️ Desmontar kit", width="small")
def _dialog_desmontar(m: dict):
    kit = m.get("_kit") or {}
    restante = int(m["quantidade"]) - int(m.get("qtd_desmontada") or 0)
    st.write(f"**{kit.get('codigo_interno') or '-'}** — {kit.get('descricao') or '-'}")
    st.caption(
        f"Montagem #{m['id']} de {_data_br(m.get('data_montagem'))}: {m['quantidade']} kit(s); "
        f"{restante} ainda não desmontado(s)."
    )
    qtd = int(st.number_input(
        "Quantidade a desmontar", min_value=1, max_value=restante, value=restante, step=1,
        key=f"kits_dm_qtd_{m['id']}",
    ))
    data_mov = st.date_input(
        "Data da desmontagem", value=date.today(), key=f"kits_dm_data_{m['id']}", format="DD/MM/YYYY"
    )
    st.caption(
        "Os componentes voltam ao estoque e o kit é baixado. Só dá para desmontar kits que "
        "ainda estão em estoque (os já vendidos não voltam)."
    )
    if st.button("Confirmar desmontagem", type="primary", use_container_width=True, key=f"kits_dm_ok_{m['id']}"):
        try:
            kits_srv.desmontar_kit(get_client(), m["id"], qtd, data_mov.isoformat(), usuario=_usuario())
        except SaldoInsuficienteError as e:
            st.error(str(e))
            return
        except Exception as e:
            st.error(f"Não foi possível desmontar: {type(e).__name__}: {e}")
            return
        st.session_state[CHAVE_MSG_OK] = f"{qtd} kit(s) desmontado(s); os componentes voltaram ao estoque."
        st.rerun()


def _secao_historico():
    try:
        montagens = kits_srv.listar_montagens(supabase, limite=100)
    except Exception as e:
        st.error(f"Erro ao carregar as montagens (a migration 019 foi aplicada?): {e}")
        return
    if not montagens:
        st.info("Nenhuma montagem registrada ainda.")
        return

    st.caption("Últimas 100 montagens.")
    colunas = [1.1, 3.2, 1.0, 1.2, 1.3, 1.5, 0.8]
    with st.container(border=True):
        for col, titulo in zip(
            st.columns(colunas),
            ["Data", "Kit", "Montados", "Desmontados", "Custo unit.", "Custo do lote", "Ação"],
        ):
            col.markdown(f"**{titulo}**")
        st.divider()

        for m in montagens:
            kit = m.get("_kit") or {}
            desmontados = int(m.get("qtd_desmontada") or 0)
            restante = int(m["quantidade"]) - desmontados
            c_data, c_kit, c_q, c_dm, c_cu, c_ct, c_ac = st.columns(colunas, vertical_alignment="center")
            c_data.write(_data_br(m.get("data_montagem")))
            c_kit.write(f"{kit.get('codigo_interno') or '-'} — {kit.get('descricao') or '-'}")
            c_q.write(str(m["quantidade"]))
            c_dm.write(str(desmontados) if desmontados else "-")
            c_cu.write(_fmt_moeda(m.get("custo_unitario")))
            c_ct.write(_fmt_moeda(float(m.get("custo_unitario") or 0) * int(m["quantidade"])))
            if restante > 0:
                if c_ac.button("↩️", key=f"kits_dm_{m['id']}", help="Desmontar", use_container_width=True):
                    _dialog_desmontar(m)
            else:
                c_ac.button(
                    "🔒", key=f"kits_dm_bloq_{m['id']}", disabled=True,
                    help="Montagem já totalmente desmontada.", use_container_width=True,
                )


# ----------------------------------------------------------------------
# Tela
# ----------------------------------------------------------------------
def tela_estoque_kits():
    st.subheader("🎁 Kits")
    st.caption(
        "Um kit é um produto com estoque e preço próprios, montado a partir de outros produtos. "
        "A montagem baixa os componentes e sobe o kit (ajustes automáticos no ledger); "
        "a venda do kit é uma venda comum."
    )

    msg = st.session_state.pop(CHAVE_MSG_OK, None)
    if msg:
        st.success(msg)

    secao = st.radio(
        "Seção", ["Montar", "Receitas", "Histórico"], horizontal=True,
        key="kits_secao", label_visibility="collapsed",
    )
    if secao == "Montar":
        _secao_montar()
    elif secao == "Receitas":
        _secao_receitas()
    else:
        _secao_historico()

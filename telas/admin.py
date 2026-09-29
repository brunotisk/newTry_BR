import os
import subprocess
from pathlib import Path
import re
import streamlit as st
from datetime import datetime, timedelta
from db import supabase
from auth import usuario_e_admin
from servicos.compras_import import excluir_compra
from servicos.log_automacao import FLUXOS, ETAPAS


def _fmt_moeda(valor) -> str:
    try:
        v = float(valor or 0)
        return (
            f"R$ {v:,.2f}"
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )
    except Exception:
        return "R$ 0,00"


def _fmt_data(data_iso) -> str:
    if not data_iso:
        return "-"
    try:
        s = str(data_iso)[:10]
        ano, mes, dia = s.split("-")
        return f"{dia}/{mes}/{ano}"
    except Exception:
        return str(data_iso)


def _secao_exclusao_compras():
    st.subheader("🗑️ Exclusão de Compras (XML)")
    st.caption(
        "Esta rotina estorna as quantidades dos itens comprados no estoque (com registro no ledger de movimentações) "
        "e exclui a compra, liberando a chave de acesso para eventual reimportação."
    )

    try:
        resp_compras = (
            supabase.table("compras")
            .select(
                "id, numero_nf, data_emissao, valor_total, chave_acesso, "
                "compra_origem, fornecedor_id, fornecedores(razao_social, nome_fantasia)"
            )
            # No módulo de exclusão, a ordenação padrão é pelo ID mais recente.
            .order("id", desc=True)
            .execute()
        )
        compras = resp_compras.data or []
    except Exception as e:
        st.error(f"Erro ao carregar lista de compras: {e}")
        return

    if not compras:
        st.info("Nenhuma compra encontrada no banco de dados.")
        return

    def _rotulo_compra(c: dict) -> str:
        nf = c.get("numero_nf") or "Sem NF"
        forn = (c.get("fornecedores") or {}).get("nome_fantasia") or (c.get("fornecedores") or {}).get("razao_social") or "Fornecedor não identificado"
        dt = _fmt_data(c.get("data_emissao"))
        vt = _fmt_moeda(c.get("valor_total"))
        return f"NF {nf} | {forn} | {dt} | {vt} (ID #{c['id']})"

    # Por padrão, nenhuma compra fica selecionada.
    # O usuário pode localizar uma NF pelo filtro e então escolher a compra no dropdown.
    col_dropdown, col_filtro_nf = st.columns([3.2, 1.3])

    with col_filtro_nf:
        filtro_nf = st.text_input(
            "Filtrar por NF",
            value="",
            placeholder="Digite a NF...",
            key="admin_filtro_nf_excluir",
        ).strip()

    compras_filtradas = compras
    if filtro_nf:
        termo_nf = filtro_nf.casefold()
        compras_filtradas = [
            c for c in compras
            if termo_nf in str(c.get("numero_nf") or "").casefold()
        ]

    opcoes = {c["id"]: c for c in compras_filtradas}
    opcoes_dropdown = [None] + list(opcoes.keys())

    with col_dropdown:
        compra_id_selecionado = st.selectbox(
            "Selecione a compra que deseja excluir",
            options=opcoes_dropdown,
            index=0,
            format_func=lambda cid: (
                "Selecione uma compra..."
                if cid is None
                else _rotulo_compra(opcoes[cid])
            ),
            key="admin_select_compra_excluir",
        )

    if compra_id_selecionado is None:
        if filtro_nf and not compras_filtradas:
            st.info(f"Nenhuma compra encontrada para a NF **{filtro_nf}**.")
        else:
            st.caption(
                "Nenhuma compra selecionada. Use o filtro de NF ou escolha uma compra no dropdown."
            )
        return

    compra = opcoes[compra_id_selecionado]
    forn_info = compra.get("fornecedores") or {}
    nome_fornecedor = forn_info.get("nome_fantasia") or forn_info.get("razao_social") or "-"

    # Card com resumo da compra selecionada
    with st.container(border=True):
        c1, c2, c3, c4 = st.columns(4)
        c1.markdown(f"**Número NF:** {compra.get('numero_nf') or '-'}")
        c2.markdown(f"**Data Emissão:** {_fmt_data(compra.get('data_emissao'))}")
        c3.markdown(f"**Valor Total:** {_fmt_moeda(compra.get('valor_total'))}")
        c4.markdown(f"**Origem:** {compra.get('compra_origem') or 'Importação XML'}")

        st.caption(f"**Fornecedor:** {nome_fornecedor} | **Chave:** `{compra.get('chave_acesso') or '-'}`")

    # Carrega itens da compra e consulta estoque atual de cada um
    try:
        resp_itens = (
            supabase.table("compras_itens")
            .select(
                "id, produto_id, quantidade, valor_unitario, valor_total, "
                "produtos(codigo_interno, descricao)"
            )
            .eq("compra_id", compra_id_selecionado)
            .execute()
        )
        itens = resp_itens.data or []
    except Exception as e:
        st.error(f"Erro ao consultar itens da compra: {e}")
        return

    if not itens:
        # Compras órfãs/incompletas podem existir quando uma importação foi
        # interrompida depois da criação do cabeçalho e antes da gravação dos itens.
        # Nesse caso não há estoque a estornar, mas o cabeçalho deve continuar
        # podendo ser excluído pela rotina administrativa.
        st.warning(
            "Esta compra não possui itens vinculados. Nenhum estoque será estornado; "
            "a exclusão removerá apenas o cabeçalho (e eventuais anexos/movimentos vinculados)."
        )
        produto_ids = []
    else:
        # Consulta saldos em estoque dos produtos afetados
        produto_ids = [it["produto_id"] for it in itens]
    saldos_atuais = {}
    if produto_ids:
        try:
            resp_est = (
                supabase.table("estoque")
                .select("produto_id, quantidade_atual")
                .in_("produto_id", produto_ids)
                .execute()
            )
            for row in (resp_est.data or []):
                saldos_atuais[row["produto_id"]] = float(row.get("quantidade_atual") or 0)
        except Exception as e:
            st.error(f"Erro ao consultar saldos atuais de estoque: {e}")

    # Monta lista para a tabela de impacto
    dados_tabela = []
    tem_negativo = False

    for it in itens:
        prod = it.get("produtos") or {}
        pid = it["produto_id"]
        qtd_comprada = float(it.get("quantidade") or 0)
        saldo_atual = saldos_atuais.get(pid, 0.0)
        saldo_pos = saldo_atual - qtd_comprada

        if saldo_pos < 0:
            tem_negativo = True
            status = "🔴 Ficará negativo"
        elif saldo_pos == 0:
            status = "🟡 Ficará zerado"
        else:
            status = "🟢 Positivo"

        dados_tabela.append({
            "Código": prod.get("codigo_interno") or "-",
            "Descrição": prod.get("descricao") or "-",
            "Qtd na NF (Estorno)": f"-{qtd_comprada:g}",
            "Saldo Atual": f"{saldo_atual:g}",
            "Saldo Projetado": f"{saldo_pos:g}",
            "Status Pós-Exclusão": status,
        })

    st.markdown("#### Prévia de Impacto no Estoque")

    if not itens:
        st.info("Sem itens vinculados: não haverá alteração de saldo de estoque.")
    elif tem_negativo:
        st.warning(
            "⚠️ **Atenção:** Um ou mais produtos ficarão com **saldo de estoque negativo**. "
            "Isso significa que já houve saídas ou vendas registradas para esses itens após a importação desta compra."
        )

    st.dataframe(dados_tabela, use_container_width=True, hide_index=True)

    st.divider()

    # Confirmação e ação de exclusão
    st.markdown("#### Confirmar Exclusão Definitiva")
    if itens:
        st.write(
            "Ao confirmar, o sistema irá:  \n"
            "1. Gravar movimentos de ajuste no ledger estornando a quantidade de cada produto.  \n"
            "2. Recalcular os dados de última compra e número de compras dos produtos afetados.  \n"
            "3. Excluir anexos vinculados e remover o registro da compra e de seus itens."
        )
    else:
        st.write(
            "Ao confirmar, o sistema irá remover os anexos vinculados, eventuais "
            "movimentos vinculados e o cabeçalho da compra. Como não há itens, "
            "nenhum ajuste de estoque será realizado."
        )

    nf_esperada = str(compra.get("numero_nf") or "").strip()
    col_conf, col_btn = st.columns([1.5, 1])

    with col_conf:
        confirmacao_texto = st.text_input(
            f"Digite o número da NF ({nf_esperada}) para habilitar o botão de exclusão:",
            key=f"admin_confirm_nf_{compra_id_selecionado}",
        )

    pode_excluir = confirmacao_texto.strip() == nf_esperada

    with col_btn:
        st.write("")  # Espaçamento vertical
        st.write("")
        if st.button(
            "🗑️ Excluir Compra e Estornar Estoque" if itens else "🗑️ Excluir Compra sem Itens",
            type="primary",
            disabled=not pode_excluir,
            use_container_width=True,
            key=f"btn_admin_excluir_{compra_id_selecionado}",
        ):
            with st.spinner("Excluindo compra e ajustando estoque..."):
                try:
                    resultado = excluir_compra(supabase, compra_id_selecionado)
                    st.success(
                        f"✅ Compra #{resultado['compra_id']} (NF {resultado['numero_nf']}) excluída com sucesso! "
                        f"{resultado['itens_estornados']} itens estornados no estoque e ledger."
                    )
                    st.rerun()
                except Exception as e:
                    st.error(f"Erro ao excluir compra: {e}")


_ICONE_ETAPA = {
    "cadastro_produto": "📇",
    "ajusta_estoque": "📦",
    "ajusta_movimentacao": "🔄",
}

_ORDEM_ETAPA = {"cadastro_produto": 0, "ajusta_estoque": 1, "ajusta_movimentacao": 2}

_PERIODOS = {
    "Últimas 24 horas": 1,
    "Últimos 7 dias": 7,
    "Últimos 30 dias": 30,
    "Tudo": None,
}


def _fmt_hora(valor_iso) -> str:
    if not valor_iso:
        return "-"
    try:
        dt = datetime.fromisoformat(str(valor_iso).replace("Z", "+00:00"))
        return dt.strftime("%d/%m/%Y %H:%M:%S")
    except Exception:
        return str(valor_iso)


def _rotulo_referencia(grupo: dict) -> str:
    if grupo.get("compra_id"):
        return f"Compra #{grupo['compra_id']}"
    if grupo.get("venda_id"):
        return f"Venda #{grupo['venda_id']}"
    return f"Operação {str(grupo['operacao_id'])[:8]}"


def _secao_log_automacoes():
    st.subheader("📋 Log de Automações")
    st.caption(
        "Histórico das inserções e ajustes que o sistema faz automaticamente: "
        "cadastro de produto (só em importação de compra), ajuste de saldo de "
        "estoque e gravação do movimento no ledger (`estoque_movimentos`)."
    )

    col_f1, col_f2, col_f3 = st.columns([2, 2, 1.3])

    with col_f1:
        opcoes_fluxo = ["Todos"] + list(FLUXOS.keys())
        fluxo_selecionado = st.selectbox(
            "Fluxo",
            options=opcoes_fluxo,
            format_func=lambda f: "Todos os fluxos" if f == "Todos" else FLUXOS.get(f, f),
            key="admin_log_filtro_fluxo",
        )

    with col_f2:
        periodo_selecionado = st.selectbox(
            "Período",
            options=list(_PERIODOS.keys()),
            index=1,
            key="admin_log_filtro_periodo",
        )

    with col_f3:
        st.write("")
        apenas_falhas = st.toggle("Somente falhas", value=False, key="admin_log_filtro_falhas")

    try:
        query = (
            supabase.table("logs_automacao")
            .select("*")
            .order("criado_em", desc=True)
            .limit(1000)
        )

        if fluxo_selecionado != "Todos":
            query = query.eq("fluxo", fluxo_selecionado)

        dias = _PERIODOS[periodo_selecionado]
        if dias is not None:
            cutoff = (datetime.utcnow() - timedelta(days=dias)).isoformat()
            query = query.gte("criado_em", cutoff)

        if apenas_falhas:
            query = query.eq("sucesso", False)

        registros_base = query.execute().data or []
    except Exception as e:
        st.error(
            "Erro ao carregar o log de automações. Verifique se a tabela "
            f"`logs_automacao` já foi criada no banco. Detalhe: {e}"
        )
        return

    if not registros_base:
        st.info("Nenhum registro encontrado para os filtros selecionados.")
        return

    # Filtro de usuário: as opções vêm do próprio resultado já carregado
    # (não dá pra oferecer antes de saber quem aparece no período/fluxo).
    usuarios_distintos = sorted({r.get("usuario") for r in registros_base if r.get("usuario")})
    if usuarios_distintos:
        usuario_selecionado = st.selectbox(
            "Usuário",
            options=["Todos"] + usuarios_distintos,
            key="admin_log_filtro_usuario",
        )
        registros = (
            registros_base if usuario_selecionado == "Todos"
            else [r for r in registros_base if r.get("usuario") == usuario_selecionado]
        )
    else:
        registros = registros_base

    if not registros:
        st.info("Nenhum registro encontrado para os filtros selecionados.")
        return

    total = len(registros)
    total_falhas = sum(1 for r in registros if not r.get("sucesso"))

    col_k1, col_k2 = st.columns(2)
    with col_k1:
        with st.container(border=True):
            st.caption("Etapas registradas no período")
            st.title(f"{total}")
    with col_k2:
        with st.container(border=True):
            st.caption("Etapas com falha")
            st.title(f"{total_falhas}")

    st.markdown("---")

    aba_resumo, aba_detalhe = st.tabs(["📊 Log Resumo", "📄 Log Detalhe"])

    grupos = _agrupar_registros(registros)

    with aba_resumo:
        _renderizar_log_resumo(grupos)

    with aba_detalhe:
        _renderizar_log_detalhe(grupos)


_COLUNAS_RESUMO_CONDICAO = {
    "Compras": lambda r: r.get("compra_id") is not None,
    "Vendas": lambda r: r.get("venda_id") is not None,
    "Produto": lambda r: r["etapa"] == "cadastro_produto",
    "Mov. Estoque": lambda r: r["etapa"] == "ajusta_movimentacao",
    "Saldo Estoque": lambda r: r["etapa"] == "ajusta_estoque",
}


def _farol_coluna(etapas: list[dict], condicao) -> str:
    """🟢 todas as ocorrências dessa área tiveram sucesso, 🔴 pelo menos uma
    falhou, '—' quando essa área nem se aplica a esta execução (nenhum
    registro correspondente)."""
    filtrados = [r for r in etapas if condicao(r)]
    if not filtrados:
        return "—"
    return "🔴" if any(not r.get("sucesso") for r in filtrados) else "🟢"


def _agrupar_registros(registros: list[dict]) -> list[dict]:
    """Agrupa as etapas de uma mesma execução: por compra_id, por venda_id,
    ou por operacao_id quando o registro não pertence a nenhuma compra/venda
    específica. Cada grupo representa UMA atividade individual — uma
    importação de XML, uma venda (manual ou importada), uma edição, uma
    exclusão — na ordem em que ocorreu."""
    grupos: dict = {}
    for r in registros:
        if r.get("compra_id"):
            chave = ("compra", r["compra_id"])
        elif r.get("venda_id"):
            chave = ("venda", r["venda_id"])
        else:
            chave = ("op", r["operacao_id"])

        if chave not in grupos:
            grupos[chave] = {
                "fluxo": r["fluxo"],
                "compra_id": r.get("compra_id"),
                "venda_id": r.get("venda_id"),
                "operacao_id": r.get("operacao_id"),
                "etapas": [],
                "criado_em_max": r["criado_em"],
            }
        grupos[chave]["etapas"].append(r)
        if r["criado_em"] > grupos[chave]["criado_em_max"]:
            grupos[chave]["criado_em_max"] = r["criado_em"]

    return sorted(grupos.values(), key=lambda g: g["criado_em_max"], reverse=True)


def _renderizar_log_resumo(grupos: list[dict]):
    st.caption(
        "Uma linha por execução — cada importação de XML, cada venda "
        "(importada ou manual), cada edição/exclusão gera sua própria linha. "
        "Cada coluna mostra o farol daquela área: 🟢 tudo certo, 🔴 houve "
        "falha, — a área não se aplica a essa atividade."
    )

    tabela = []
    for grupo in grupos:
        etapas = grupo["etapas"]
        usuarios = sorted({e.get("usuario") for e in etapas if e.get("usuario")})
        sucesso_geral = all(e.get("sucesso") for e in etapas)

        tabela.append({
            "Status": "🟢" if sucesso_geral else "🔴",
            "Atividade": FLUXOS.get(grupo["fluxo"], grupo["fluxo"]),
            "Referência": _rotulo_referencia(grupo),
            **{
                coluna: _farol_coluna(etapas, condicao)
                for coluna, condicao in _COLUNAS_RESUMO_CONDICAO.items()
            },
            "Usuário": ", ".join(usuarios) if usuarios else "—",
            "Quando": _fmt_hora(grupo["criado_em_max"]),
        })

    st.dataframe(tabela, use_container_width=True, hide_index=True)


def _renderizar_log_detalhe(grupos: list[dict]):
    for grupo in grupos:
        etapas = sorted(
            grupo["etapas"], key=lambda e: _ORDEM_ETAPA.get(e["etapa"], 99)
        )
        sucesso_geral = all(e.get("sucesso") for e in etapas)
        icone_geral = "✅" if sucesso_geral else "❌"
        rotulo_fluxo = FLUXOS.get(grupo["fluxo"], grupo["fluxo"])
        rotulo_ref = _rotulo_referencia(grupo)

        usuarios_grupo = sorted({e.get("usuario") for e in etapas if e.get("usuario")})
        rotulo_usuario = f" — 👤 {usuarios_grupo[0]}" if len(usuarios_grupo) == 1 else (
            f" — 👤 {len(usuarios_grupo)} usuários" if len(usuarios_grupo) > 1 else ""
        )

        titulo = (
            f"{icone_geral} {rotulo_fluxo} — {rotulo_ref} — "
            f"{_fmt_hora(grupo['criado_em_max'])} ({len(etapas)} etapa(s)){rotulo_usuario}"
        )

        with st.expander(titulo):
            for e in etapas:
                icone_etapa = _ICONE_ETAPA.get(e["etapa"], "•")
                icone_status = "✅" if e.get("sucesso") else "❌"
                rotulo_etapa = ETAPAS.get(e["etapa"], e["etapa"])

                partes = [f"{icone_status} {icone_etapa} **{rotulo_etapa}**"]
                if e.get("produto_id"):
                    partes.append(f"produto #{e['produto_id']}")
                if e.get("quantidade") is not None:
                    partes.append(f"qtd {e['quantidade']:g}")
                if e.get("usuario"):
                    partes.append(f"👤 {e['usuario']}")
                partes.append(_fmt_hora(e["criado_em"]))

                st.write(" · ".join(partes))
                if e.get("mensagem"):
                    st.caption(e["mensagem"])


def _localizar_docs() -> Path:
    candidatos = [
        Path(__file__).resolve().parent / "docs",
        Path(__file__).resolve().parent.parent / "docs",
    ]
    for caminho in candidatos:
        if caminho.exists():
            return caminho
    return candidatos[0]


def _ler_versao() -> str:
    arquivo = _localizar_docs() / "version.py"
    try:
        conteudo = arquivo.read_text(encoding="utf-8")
        match = re.search(r'APP_VERSION\s*=\s*["\\\']([^"\\\']+)["\\\']', conteudo)
        return match.group(1) if match else "Não informada"
    except Exception:
        return "Não informada"


def _ler_changelog() -> str:
    try:
        return (_localizar_docs() / "changelog.md").read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _informacoes_git() -> tuple[str, str]:
    try:
        raiz = Path(__file__).resolve().parent
        h = subprocess.run(
            ["git", "-C", str(raiz), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=3, check=True,
        ).stdout.strip()
        d = subprocess.run(
            ["git", "-C", str(raiz), "log", "-1", "--format=%cI"],
            capture_output=True, text=True, timeout=3, check=True,
        ).stdout.strip()
        data = "-"
        if d:
            data = datetime.fromisoformat(d.replace("Z", "+00:00")).strftime("%d/%m/%Y %H:%M")
        return h or "-", data
    except Exception:
        return "-", "-"


def _secao_info_sistema():
    st.subheader("ℹ️ Informações do Sistema")

    nome_aplicacao = "Sistema ERP Light - BR"
    ambiente = os.getenv("APP_ENV", "").strip().upper()
    if not ambiente:
        ambiente = "DEV" if os.getenv("SKIP_AUTH", "").strip().lower() == "true" else "NÃO INFORMADO"

    versao = _ler_versao()
    commit, ultima_atualizacao = _informacoes_git()
    changelog = _ler_changelog()

    col1, col2, col3 = st.columns(3)

    with col1:
        with st.container(border=True):
            st.caption("Aplicação")
            st.markdown(f"### {nome_aplicacao}")
            st.caption("Versão")
            st.markdown(f"### `v{versao}`")

    with col2:
        with st.container(border=True):
            st.caption("Ambiente")
            icone = {"PROD": "🟢", "HOMOLOG": "🟡", "DEV": "🔵"}.get(ambiente, "⚪")
            st.markdown(f"### {icone} {ambiente}")
            st.caption("Última atualização")
            st.markdown(f"### {ultima_atualizacao}")

    with col3:
        with st.container(border=True):
            st.caption("Informações Administrativas")
            st.markdown(
                "Área destinada a módulos operacionais e de manutenção avançada do sistema."
            )
            st.caption(
                "Acesso restrito ao administrador (`bruno.ishikawa@gmail.com`) "
                "ou ambiente de desenvolvimento (`SKIP_AUTH=True`)."
            )

    st.caption(f"Commit: `{commit}`")
    st.caption("A data de atualização corresponde ao último commit disponível no Git.")

    if changelog:
        with st.expander("📋 Histórico de versões"):
            st.markdown(changelog)


def tela_admin():
    if not usuario_e_admin():
        st.error("⛔ Acesso não autorizado. Esta área é restrita a administradores.")
        st.stop()

    st.header("⚙️ Painel de Administração")

    aba_compras, aba_log, aba_info = st.tabs([
        "🗑️ Exclusão de Compras (XML)",
        "📋 Log de Automações",
        "ℹ️ Informações do Sistema",
    ])

    with aba_compras:
        _secao_exclusao_compras()

    with aba_log:
        _secao_log_automacoes()

    with aba_info:
        _secao_info_sistema()

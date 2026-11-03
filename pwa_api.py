"""API e servidor de arquivos do PWA de vendas.

Executar com: uvicorn pwa_api:app --host 0.0.0.0 --port 8000
O service role do Supabase permanece exclusivamente no servidor.
"""
from __future__ import annotations

import os
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from servicos.supabase_admin import get_client
from servicos.vendas_import import calcular_comissao_venda, inserir_venda_e_baixar_estoque
from docs.version import APP_VERSION


BASE_DIR = Path(__file__).resolve().parent
PWA_DIR = BASE_DIR / "pwa"
LOGGER = logging.getLogger(__name__)
COOKIE_ACCESS = "sbyac_access"
COOKIE_REFRESH = "sbyac_refresh"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365


def _json_erro(mensagem: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": mensagem}, status_code=status)


def _erro_com_sessao(mensagem: str, status: int, session) -> JSONResponse:
    response = _json_erro(mensagem, status)
    if session is not None:
        _set_session_cookies(response, session)
    return response


def _set_session_cookies(response: Response, session) -> None:
    secure = os.getenv("PWA_COOKIE_SECURE", "true").lower() not in {"0", "false", "no"}
    options = {
        "httponly": True,
        "secure": secure,
        "samesite": "lax",
        "path": "/",
        "max_age": COOKIE_MAX_AGE,
    }
    response.set_cookie(COOKIE_ACCESS, session.access_token, **options)
    response.set_cookie(COOKIE_REFRESH, session.refresh_token, **options)


def _clear_session_cookies(response: Response) -> None:
    secure = os.getenv("PWA_COOKIE_SECURE", "true").lower() not in {"0", "false", "no"}
    for name in (COOKIE_ACCESS, COOKIE_REFRESH):
        response.delete_cookie(name, path="/", httponly=True, secure=secure, samesite="lax")


def _session_from_request(request: Request):
    access = request.cookies.get(COOKIE_ACCESS)
    refresh = request.cookies.get(COOKIE_REFRESH)
    if not access or not refresh:
        return None, None, None
    client = get_client()  # novo cliente por requisição: não compartilha auth entre usuários
    try:
        auth_response = client.auth.set_session(access, refresh)
        session = getattr(auth_response, "session", None) or client.auth.get_session()
        if not session or not getattr(session, "user", None):
            return None, None
        return session.user, client, session
    except Exception:
        return None, None, None


def _rows(query_factory, page_size: int = 1000) -> list[dict]:
    result = []
    offset = 0
    while True:
        batch = query_factory().range(offset, offset + page_size - 1).execute().data or []
        result.extend(batch)
        if len(batch) < page_size:
            return result
        offset += page_size


def _active_rows(sb, table: str, columns: str, order: str) -> list[dict]:
    return (
        sb.table(table).select(columns).eq("ativo", True)
        .order("ordem_exibicao", nullsfirst=False).order(order).execute().data or []
    )


async def health(_request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


async def login(request: Request) -> Response:
    try:
        body = await request.json()
        email = str(body.get("email") or "").strip()
        password = str(body.get("password") or "")
        if not email or not password:
            return _json_erro("Informe e-mail e senha.")
        client = get_client()
        auth_response = client.auth.sign_in_with_password({"email": email, "password": password})
        session = getattr(auth_response, "session", None)
        user = getattr(auth_response, "user", None)
        if not session or not user:
            return _json_erro("Não foi possível iniciar a sessão.", 401)
        response = JSONResponse({"email": user.email})
        _set_session_cookies(response, session)
        return response
    except Exception:
        return _json_erro("E-mail ou senha inválidos.", 401)


async def logout(request: Request) -> Response:
    response = JSONResponse({"ok": True})
    _clear_session_cookies(response)
    access = request.cookies.get(COOKIE_ACCESS)
    refresh = request.cookies.get(COOKIE_REFRESH)
    if access and refresh:
        try:
            client = get_client()
            client.auth.set_session(access, refresh)
            client.auth.sign_out()
        except Exception:
            pass
    return response


async def current_user(request: Request) -> Response:
    user, _client, session = _session_from_request(request)
    if user is None:
        response = _json_erro("Sessão expirada. Entre novamente.", 401)
        _clear_session_cookies(response)
        return response
    response = JSONResponse({"email": user.email})
    _set_session_cookies(response, session)
    return response


async def catalog(request: Request) -> Response:
    user, _auth_client, session = _session_from_request(request)
    if user is None:
        response = _json_erro("Sessão expirada. Entre novamente.", 401)
        _clear_session_cookies(response)
        return response
    try:
        sb = get_client()
        products_raw = _rows(lambda: sb.table("estoque").select(
            "produto_id, quantidade_atual, estoque_preco_venda_sugerida, "
            "produtos(id, codigo_interno, descricao, foto_url)"
        ).gt("quantidade_atual", 0).order("produto_id"))
        products = []
        for row in products_raw:
            product = row.get("produtos")
            if isinstance(product, list):
                product = product[0] if product else None
            if product:
                products.append({
                    **product,
                    "saldo": float(row.get("quantidade_atual") or 0),
                    "preco": float(row.get("estoque_preco_venda_sugerida") or 0),
                })
        clients = _rows(lambda: sb.table("clientes").select(
            "id, nome, telefone, canal_id"
        ).order("nome").order("id"))
        data = {
            "canais": _active_rows(sb, "canais_venda", "id, nome", "nome"),
            "formas": _active_rows(sb, "formas_pagamento", "id, descricao", "descricao"),
            "status": _active_rows(sb, "status_venda", "id, nome", "nome"),
            "feiras": _active_rows(sb, "detalhes_feira", "id, nome_feira", "nome_feira"),
            "clientes": clients,
            "produtos": products,
        }
        response = JSONResponse(data)
        _set_session_cookies(response, session)
        return response
    except Exception:
        LOGGER.exception("Falha ao carregar cadastros do PWA")
        return _erro_com_sessao("Não foi possível carregar os cadastros agora.", 500, session)


def _active_id(sb, table: str, raw_id, label: str) -> int:
    try:
        row_id = int(raw_id)
    except (TypeError, ValueError):
        raise ValueError(f"Selecione {label}.")
    rows = sb.table(table).select("id").eq("id", row_id).eq("ativo", True).limit(1).execute().data or []
    if not rows:
        raise ValueError(f"{label.capitalize()} não está ativo ou não existe.")
    return row_id


def _decimal(value, label: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{label} inválido.")
    if not result.is_finite():
        raise ValueError(f"{label} inválido.")
    return result


def _resolve_client(sb, body: dict, canal_id: int, feira_id: int | None) -> tuple[int | None, str]:
    client_id = body.get("cliente_id")
    novo = body.get("cliente_novo") or {}
    if client_id:
        rows = sb.table("clientes").select("id, nome").eq("id", int(client_id)).limit(1).execute().data or []
        if not rows:
            raise ValueError("Cliente selecionado não existe.")
        return rows[0]["id"], rows[0]["nome"]
    nome = str(novo.get("nome") or "").strip()
    if not nome:
        raise ValueError("Selecione um cliente ou informe o nome do novo cliente.")
    like = nome.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    found = sb.table("clientes").select("id, nome").eq("canal_id", canal_id).ilike("nome", like).execute().data or []
    for row in found:
        if str(row.get("nome") or "").strip().casefold() == nome.casefold():
            return row["id"], row["nome"]
    data = {"nome": nome, "canal_id": canal_id}
    if feira_id:
        data["detalhe_feira_id"] = feira_id
    digits = "".join(ch for ch in str(novo.get("telefone") or "") if ch.isdigit())[:11]
    if len(digits) <= 2:
        phone = f"({digits}" if digits else ""
    elif len(digits) <= 7:
        phone = f"({digits[:2]}) {digits[2:]}"
    else:
        phone = f"({digits[:2]}) {digits[2:7]}-{digits[7:]}"
    if phone:
        data["telefone"] = phone
    result = sb.table("clientes").insert(data).execute().data or []
    if not result:
        raise RuntimeError("Não foi possível cadastrar o cliente.")
    return result[0]["id"], nome


async def create_sale(request: Request) -> Response:
    user, _auth_client, session = _session_from_request(request)
    if user is None:
        response = _json_erro("Sessão expirada. Entre novamente.", 401)
        _clear_session_cookies(response)
        return response
    try:
        body = await request.json()
        sb = get_client()
        canal_id = _active_id(sb, "canais_venda", body.get("canal_id"), "canal")
        forma_id = _active_id(sb, "formas_pagamento", body.get("forma_id"), "forma de pagamento")
        status_id = _active_id(sb, "status_venda", body.get("status_id"), "status")
        feira_id = None
        if str((sb.table("canais_venda").select("nome").eq("id", canal_id).single().execute().data or {}).get("nome") or "").strip().casefold() == "feira":
            feira_id = _active_id(sb, "detalhes_feira", body.get("feira_id"), "feira")
        sale_date = date.fromisoformat(str(body.get("data_venda") or ""))
        items = body.get("items") or []
        if not items:
            raise ValueError("Adicione pelo menos um produto.")

        # Valida todo o carrinho com saldos atuais antes de cadastrar o cliente ou gravar itens.
        prepared = []
        requested_by_product: dict[int, Decimal] = {}
        for item in items:
            try:
                product_id = int(item.get("product_id"))
            except (TypeError, ValueError):
                raise ValueError("Um dos produtos selecionados é inválido.")
            quantity = _decimal(item.get("quantity"), "Quantidade")
            price = _decimal(item.get("unit_price"), "Preço")
            discount = _decimal(item.get("discount", 0), "Desconto")
            discount_type = item.get("discount_type", "value")
            if discount_type not in {"value", "percent"}:
                raise ValueError("Tipo de desconto inválido.")
            if quantity <= 0 or quantity != quantity.to_integral_value():
                raise ValueError("A quantidade deve ser um número inteiro maior que zero.")
            if price < 0 or discount < 0:
                raise ValueError("Preço e desconto não podem ser negativos.")
            subtotal = quantity * price
            discount_value = (subtotal * discount / Decimal(100)) if discount_type == "percent" else discount
            discount_value = discount_value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            final_value = (subtotal - discount_value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if final_value < 0:
                raise ValueError("O desconto não pode superar o total do produto.")
            product_rows = sb.table("estoque").select(
                "quantidade_atual, produtos(id, codigo_interno, descricao)"
            ).eq("produto_id", product_id).limit(1).execute().data or []
            if not product_rows:
                raise ValueError("Um dos produtos não existe no estoque.")
            stock_row = product_rows[0]
            product = stock_row.get("produtos")
            if isinstance(product, list):
                product = product[0] if product else None
            if not product:
                raise ValueError("Não foi possível localizar um dos produtos.")
            stock = Decimal(str(stock_row.get("quantidade_atual") or 0))
            requested_by_product[product_id] = requested_by_product.get(product_id, Decimal(0)) + quantity
            prepared.append({
                "product": product,
                "stock": stock,
                "quantity": quantity,
                "price": price,
                "discount": discount_value,
                "final": final_value,
            })
        for product_id, required in requested_by_product.items():
            available = next(item["stock"] for item in prepared if item["product"]["id"] == product_id)
            if required > available:
                code = next(item["product"].get("codigo_interno") for item in prepared if item["product"]["id"] == product_id)
                raise ValueError(f"Saldo insuficiente para o produto {code}: disponível {available:g}.")

        cliente_id, cliente_nome = _resolve_client(sb, body, canal_id, feira_id)
        header = {
            "canal_venda_id": canal_id,
            "status_id": status_id,
            "data_venda": sale_date.isoformat(),
            "cliente": cliente_nome,
            "cliente_id": cliente_id,
            "forma_pagamento_id": forma_id,
            "detalhe_feira_id": feira_id,
        }
        created, failures = [], []
        for index, item in enumerate(prepared):
            product = item["product"]
            try:
                commission_pct, commission_value = calcular_comissao_venda(sb, canal_id, float(item["final"]))
                sale_id = inserir_venda_e_baixar_estoque(
                    sb,
                    {
                        **header,
                        "produto_id": product["id"],
                        "quantidade": float(item["quantity"]),
                        "valor_lista": float(item["price"]),
                        "valor_desconto": float(item["discount"]),
                        "valor_final": float(item["final"]),
                        "comissao_percentual": commission_pct,
                        "comissao_valor": commission_value,
                    },
                    product["id"],
                    motivo_saida="Venda Manual",
                )
                created.append({"index": index, "sale_id": sale_id})
            except Exception as exc:
                LOGGER.exception("Falha ao registrar item de venda no PWA")
                failures.append({"index": index, "message": "Falha ao registrar este item."})
        response = JSONResponse({"created": created, "failures": failures})
        _set_session_cookies(response, session)
        return response
    except (ValueError, InvalidOperation) as exc:
        return _erro_com_sessao(str(exc), 400, session)
    except Exception:
        LOGGER.exception("Falha ao registrar venda pelo PWA")
        return _erro_com_sessao("Não foi possível registrar a venda agora.", 500, session)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def list_clients(request: Request) -> Response:
    user, _auth_client, session = _session_from_request(request)
    if user is None:
        response = _json_erro("Sessão expirada. Entre novamente.", 401)
        _clear_session_cookies(response)
        return response
    try:
        sb = get_client()
        params = request.query_params
        search = str(params.get("search") or "").strip()
        channel = params.get("channel_id")
        try:
            offset = max(0, int(params.get("offset", "0")))
            limit = min(50, max(1, int(params.get("limit", "20"))))
        except ValueError:
            raise ValueError("Paginação inválida.")
        query = sb.table("clientes").select(
            "id, nome, telefone, canal_id, detalhe_feira_id, data_nascimento, data_casamento",
            count="exact",
        )
        if search:
            query = query.ilike("nome", f"%{_escape_like(search)}%")
        if channel:
            query = query.eq("canal_id", int(channel))
        result = query.order("nome").order("id").range(offset, offset + limit - 1).execute()
        data = {"items": result.data or [], "total": result.count or 0, "offset": offset, "limit": limit}
        response = JSONResponse(data)
        _set_session_cookies(response, session)
        return response
    except ValueError as exc:
        return _erro_com_sessao(str(exc), 400, session)
    except Exception:
        LOGGER.exception("Falha ao consultar clientes no PWA")
        return _erro_com_sessao("Não foi possível consultar os clientes agora.", 500, session)


def _format_phone(value) -> str | None:
    digits = "".join(char for char in str(value or "") if char.isdigit())[:11]
    if not digits:
        return None
    if len(digits) <= 2:
        return f"({digits}"
    if len(digits) <= 7:
        return f"({digits[:2]}) {digits[2:]}"
    return f"({digits[:2]}) {digits[2:7]}-{digits[7:]}"


def _client_payload(sb, body: dict, existing: dict | None = None) -> dict:
    name = str(body.get("nome") or "").strip()
    if not name:
        raise ValueError("Informe o nome do cliente.")
    channel_id = _active_id(sb, "canais_venda", body.get("canal_id"), "canal")
    channel_rows = sb.table("canais_venda").select("nome").eq("id", channel_id).limit(1).execute().data or []
    channel_name = channel_rows[0].get("nome", "") if channel_rows else ""
    fair_id = None
    if channel_name.strip().casefold() == "feira":
        fair_id = _active_id(sb, "detalhes_feira", body.get("detalhe_feira_id"), "feira")
    payload = {
        "nome": name,
        "telefone": _format_phone(body.get("telefone")),
        "canal_id": channel_id,
        "detalhe_feira_id": fair_id,
        "data_nascimento": body.get("data_nascimento") or None,
        "data_casamento": body.get("data_casamento") or None,
    }
    today = date.today()
    for field, label in (("data_nascimento", "Data de nascimento"), ("data_casamento", "Data de casamento")):
        if payload[field]:
            try:
                date_value = date.fromisoformat(payload[field])
            except (TypeError, ValueError):
                raise ValueError(f"{label} inválida.")
            if date_value > today:
                raise ValueError(f"{label} não pode ser futura.")
    # Regra da base: o nome não pode se repetir no mesmo canal.
    duplicate_query = sb.table("clientes").select("id, nome").eq("canal_id", channel_id).ilike("nome", _escape_like(name))
    for duplicate in duplicate_query.execute().data or []:
        if duplicate.get("nome", "").strip().casefold() == name.casefold() and duplicate.get("id") != (existing or {}).get("id"):
            raise ValueError(f'Já existe um cliente chamado "{name}" neste canal.')
    return payload


async def save_client(request: Request) -> Response:
    user, _auth_client, session = _session_from_request(request)
    if user is None:
        response = _json_erro("Sessão expirada. Entre novamente.", 401)
        _clear_session_cookies(response)
        return response
    try:
        body = await request.json()
        sb = get_client()
        payload = _client_payload(sb, body)
        rows = sb.table("clientes").insert(payload).execute().data or []
        if not rows:
            raise RuntimeError("Cadastro não retornou o cliente criado.")
        response = JSONResponse(rows[0], status_code=201)
        _set_session_cookies(response, session)
        return response
    except ValueError as exc:
        return _erro_com_sessao(str(exc), 400, session)
    except Exception:
        LOGGER.exception("Falha ao cadastrar cliente pelo PWA")
        return _erro_com_sessao("Não foi possível cadastrar o cliente agora.", 500, session)


async def update_client(request: Request) -> Response:
    user, _auth_client, session = _session_from_request(request)
    if user is None:
        response = _json_erro("Sessão expirada. Entre novamente.", 401)
        _clear_session_cookies(response)
        return response
    try:
        client_id = int(request.path_params["client_id"])
        body = await request.json()
        sb = get_client()
        old_rows = sb.table("clientes").select("id, nome, canal_id").eq("id", client_id).limit(1).execute().data or []
        if not old_rows:
            return _erro_com_sessao("Cliente não encontrado.", 404, session)
        payload = _client_payload(sb, body, old_rows[0])
        rows = sb.table("clientes").update(payload).eq("id", client_id).execute().data or []
        response = JSONResponse(rows[0] if rows else {"id": client_id, **payload})
        _set_session_cookies(response, session)
        return response
    except ValueError as exc:
        return _erro_com_sessao(str(exc), 400, session)
    except Exception:
        LOGGER.exception("Falha ao atualizar cliente pelo PWA")
        return _erro_com_sessao("Não foi possível atualizar o cliente agora.", 500, session)


def _relation(value):
    if isinstance(value, list):
        return value[0] if value else {}
    return value or {}


async def client_purchases(request: Request) -> Response:
    user, _auth_client, session = _session_from_request(request)
    if user is None:
        response = _json_erro("Sessão expirada. Entre novamente.", 401)
        _clear_session_cookies(response)
        return response
    try:
        client_id = int(request.path_params["client_id"])
        sb = get_client()
        client_rows = sb.table("clientes").select("id, nome").eq("id", client_id).limit(1).execute().data or []
        if not client_rows:
            return _erro_com_sessao("Cliente não encontrado.", 404, session)
        columns = "id, data_venda, cliente, quantidade, valor_final, produtos(codigo_interno, descricao), formas_pagamento(descricao)"
        by_id = _rows(lambda: sb.table("vendas").select(columns).eq("cliente_id", client_id).order("id"))
        purchases = {row["id"]: row for row in by_id}
        name = str(client_rows[0].get("nome") or "").strip()
        if name:
            like = _escape_like(name)
            legacy = _rows(lambda: sb.table("vendas").select(
                columns.replace("id, ", "id, cliente, ", 1)
            ).is_("cliente_id", "null").ilike("cliente", f"%{like}%").order("id"))
            for row in legacy:
                if str(row.get("cliente") or "").strip().casefold() == name.casefold():
                    purchases.setdefault(row["id"], row)
        ordered = sorted(purchases.values(), key=lambda row: (str(row.get("data_venda") or ""), row["id"]), reverse=True)
        total_value = sum(float(row.get("valor_final") or 0) for row in ordered)
        latest = ordered[0].get("data_venda") if ordered else None
        response = JSONResponse({
            "count": len(ordered),
            "total": total_value,
            "latest": latest,
            "items": [{
                **row,
                "produto": _relation(row.get("produtos")),
                "pagamento": _relation(row.get("formas_pagamento")),
            } for row in ordered[:30]],
        })
        _set_session_cookies(response, session)
        return response
    except Exception:
        LOGGER.exception("Falha ao consultar compras do cliente no PWA")
        return _erro_com_sessao("Não foi possível consultar as compras agora.", 500, session)


def _history_dates(params) -> tuple[str | None, str | None]:
    period = params.get("period", "month")
    today = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    if period == "today":
        start = end = today
    elif period == "7days":
        start, end = today - timedelta(days=6), today
    elif period == "month":
        start, end = today.replace(day=1), today
    elif period == "year":
        start, end = today.replace(month=1, day=1), today
    elif period == "all":
        return None, None
    elif period == "custom":
        try:
            start = date.fromisoformat(params.get("start", "")) if params.get("start") else None
            end = date.fromisoformat(params.get("end", "")) if params.get("end") else None
        except ValueError:
            raise ValueError("Período de datas inválido.")
        if start and end and start > end:
            raise ValueError("A data inicial deve ser anterior à data final.")
    else:
        raise ValueError("Período inválido.")
    return start.isoformat() if start else None, end.isoformat() if end else None


def _filter_history(query, start, end, channel_id, client_search):
    if start:
        query = query.gte("data_venda", start)
    if end:
        query = query.lte("data_venda", end)
    if channel_id:
        query = query.eq("canal_venda_id", channel_id)
    if client_search:
        query = query.ilike("cliente", f"%{_escape_like(client_search)}%")
    return query


async def history(request: Request) -> Response:
    user, _auth_client, session = _session_from_request(request)
    if user is None:
        response = _json_erro("Sessão expirada. Entre novamente.", 401)
        _clear_session_cookies(response)
        return response
    try:
        params = request.query_params
        start, end = _history_dates(params)
        channel_id = int(params["channel_id"]) if params.get("channel_id") else None
        client_search = str(params.get("client") or "").strip()
        offset = max(0, int(params.get("offset", "0")))
        limit = min(50, max(1, int(params.get("limit", "20"))))
        sb = get_client()
        columns = (
            "id, data_venda, cliente, quantidade, valor_lista, valor_desconto, valor_final, comissao_valor, "
            "produtos(codigo_interno, descricao), canais_venda(nome), status_venda(nome), formas_pagamento(descricao)"
        )
        query = _filter_history(
            sb.table("vendas").select(columns, count="exact"), start, end, channel_id, client_search
        )
        rows = query.order("data_venda", desc=True).order("id", desc=True).range(offset, offset + limit - 1).execute()
        totals = _rows(lambda: _filter_history(
            sb.table("vendas").select("valor_final, comissao_valor"), start, end, channel_id, client_search
        ).order("id"))
        data = {
            "items": [{
                **row,
                "produto": _relation(row.get("produtos")),
                "canal": _relation(row.get("canais_venda")),
                "status": _relation(row.get("status_venda")),
                "pagamento": _relation(row.get("formas_pagamento")),
            } for row in (rows.data or [])],
            "count": rows.count or 0,
            "offset": offset,
            "limit": limit,
            "summary": {
                "items": len(totals),
                "value": sum(float(row.get("valor_final") or 0) for row in totals),
                "commission": sum(float(row.get("comissao_valor") or 0) for row in totals),
            },
        }
        response = JSONResponse(data)
        _set_session_cookies(response, session)
        return response
    except ValueError as exc:
        return _erro_com_sessao(str(exc), 400, session)
    except Exception:
        LOGGER.exception("Falha ao consultar histórico no PWA")
        return _erro_com_sessao("Não foi possível carregar o histórico agora.", 500, session)


async def app_config(request: Request) -> Response:
    user, _auth_client, session = _session_from_request(request)
    if user is None:
        response = _json_erro("Sessão expirada. Entre novamente.", 401)
        _clear_session_cookies(response)
        return response
    bd = os.getenv("BD", "").strip().upper()
    url_var = "SUPABASE_URL_HOMOLOG" if bd == "HML" else "SUPABASE_URL_PRD"
    from urllib.parse import urlparse

    host = urlparse(os.getenv(url_var, "")).hostname or "-"
    labels = {"HML": "Homologação", "PRD": "Produção"}
    manual = os.getenv("APP_ENV", "").strip()
    environment = manual.capitalize() if manual else labels.get(bd, "Não identificado")
    response = JSONResponse({"environment": environment, "database_host": host, "app_version": APP_VERSION})
    _set_session_cookies(response, session)
    return response


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: https:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; manifest-src 'self'; worker-src 'self'",
        )
        return response


routes = [
    Route("/api/health", health, methods=["GET"]),
    Route("/api/auth/login", login, methods=["POST"]),
    Route("/api/auth/logout", logout, methods=["POST"]),
    Route("/api/me", current_user, methods=["GET"]),
    Route("/api/catalog", catalog, methods=["GET"]),
    Route("/api/sales", create_sale, methods=["POST"]),
    Route("/api/clients", list_clients, methods=["GET"]),
    Route("/api/clients", save_client, methods=["POST"]),
    Route("/api/clients/{client_id:int}", update_client, methods=["PUT"]),
    Route("/api/clients/{client_id:int}/purchases", client_purchases, methods=["GET"]),
    Route("/api/history", history, methods=["GET"]),
    Route("/api/config", app_config, methods=["GET"]),
    Mount("/", app=StaticFiles(directory=PWA_DIR, html=True), name="pwa"),
]
app = Starlette(debug=False, routes=routes, middleware=[Middleware(SecurityHeaders)])

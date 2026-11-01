"""API e servidor de arquivos do PWA de vendas.

Executar com: uvicorn pwa_api:app --host 0.0.0.0 --port 8000
O service role do Supabase permanece exclusivamente no servidor.
"""
from __future__ import annotations

import os
import logging
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from servicos.supabase_admin import get_client
from servicos.vendas_import import calcular_comissao_venda, inserir_venda_e_baixar_estoque


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
    Mount("/", app=StaticFiles(directory=PWA_DIR, html=True), name="pwa"),
]
app = Starlette(debug=False, routes=routes, middleware=[Middleware(SecurityHeaders)])

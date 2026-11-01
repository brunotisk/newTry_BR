# PWA de vendas

Interface instalável para login e lançamento de vendas online. A aplicação Streamlit existente continua disponível; este PWA é servido pela API Starlette no mesmo domínio para que a sessão use cookies `HttpOnly` e `Secure`.

## Executar localmente

1. Configure no `.env` as mesmas variáveis Supabase que o backend atual usa: `BD`, `SUPABASE_URL_HOMOLOG`, `SUPABASE_SERVICE_KEY_HOMOLOG`, `SUPABASE_URL_PRD` e `SUPABASE_SERVICE_KEY_PRD`.
2. Instale as dependências do projeto: `pip install -r requirements.txt`.
3. Para HTTP local, defina `PWA_COOKIE_SECURE=false`.
4. Inicie com `uvicorn pwa_api:app --host 127.0.0.1 --port 8000` e abra `http://localhost:8000`.

O login cria cookies `HttpOnly`, e os tokens de sessão nunca ficam acessíveis ao JavaScript. O servidor valida e renova a sessão pelo Supabase em cada chamada protegida. Em produção, use HTTPS e mantenha `PWA_COOKIE_SECURE=true`.

## Deploy

O blueprint `render.yaml` cria um serviço web que serve a API e o PWA na mesma origem. Crie um Web Service a partir deste repositório, aplique o blueprint e informe as variáveis `BD` e as credenciais do ambiente Supabase nos Environment Variables do serviço. A chave `SUPABASE_SERVICE_KEY_*` fica somente no servidor.

## Operação online

O service worker armazena apenas os arquivos estáticos da interface. Cadastros, autenticação e salvamento de vendas exigem conexão; o PWA informa quando está offline e bloqueia o envio. Vendas não são enfileiradas localmente.

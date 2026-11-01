streamlit run app.py
streamlit run app_mobile.py

O app mobile mantém o login após atualizar a página usando um cookie de sessão
criptografado. Em produção, configure `AUTH_COOKIE_PASSWORD` como um segredo
forte e estável (por exemplo, nos Secrets do Streamlit); sem essa configuração,
o sistema usa a chave Supabase do ambiente atual para criptografar o cookie.

https://appacsj.streamlit.app/
https://acsjsistema.streamlit.app/

https://github.com/brunotisk/newTry_BR
https://share.streamlit.io/deploy


pip install -r requirements-dev.txt
pytest --cov=. --cov-report=term-missing   # cobertura

# Mostra no Terminal
pytest -v
# Gera TXT
pytest -q > pytest_log.txt 2>&1 

select count(*) from produtos where foto_url is not null
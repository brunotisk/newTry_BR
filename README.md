streamlit run app.py
streamlit run app_mobile.py


https://github.com/brunotisk/newTry_BR
https://share.streamlit.io/deploy
https://acsjsistema.streamlit.app/

pip install -r requirements-dev.txt
pytest --cov=. --cov-report=term-missing   # cobertura

# Mostra no Terminal
pytest -v
# Gera TXT
pytest -q > pytest_log.txt 2>&1 

select count(*) from produtos where foto_url is not null
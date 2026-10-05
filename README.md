streamlit run app.py

https://github.com/brunotisk/newTry_BR
https://share.streamlit.io/deploy
https://acsjsistema.streamlit.app/

pip install -r requirements-dev.txt
pytest --cov=. --cov-report=term-missing   # cobertura

# Mostra no Terminal
pytest -v
# Gera TXT
pytest -q > pytest_log.txt 2>&1 
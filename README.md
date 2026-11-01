streamlit run app.py
streamlit run app_mobile.py
python.exe -m uvicorn pwa_api:app --host 127.0.0.1 --port 8000


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

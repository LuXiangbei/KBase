PY := .venv/Scripts/python.exe
PORT ?= 8010

.PHONY: install seed run test eval demo screenshots report clean

install:
	$(PY) -m pip install -r requirements.txt

seed:
	$(PY) scripts/seed_demo.py --reset

run:
	$(PY) -m uvicorn app.main:app --host 127.0.0.1 --port $(PORT)

test:
	$(PY) -m pytest -q

eval:
	$(PY) scripts/run_eval.py

report:
	$(PY) scripts/run_eval.py --write-report docs/eval-report.md

demo:
	$(PY) scripts/e2e_demo.py --base http://127.0.0.1:$(PORT)

screenshots:
	powershell -NoProfile -ExecutionPolicy Bypass -File scripts/screenshots.ps1 -Base http://127.0.0.1:$(PORT)

clean:
	rm -rf data screenshots .pytest_cache

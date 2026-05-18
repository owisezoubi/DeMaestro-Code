# DeMaestro Backend

FastAPI orchestration server for DeMaestro.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                # then fill in real values
mkdir -p secrets                    # place service-account JSON here
./.venv/bin/uvicorn app.main:app --reload --reload-dir app
```

Visit `http://localhost:8000/docs` for the auto-generated Swagger UI.

## Run tests

```bash
pytest -q
```

## Folder layout

See the root [README.md](../README.md) and the [Phase B Architecture Guide PDF](../../DeMaestro_Architecture_Guide.pdf) for the full layered architecture.

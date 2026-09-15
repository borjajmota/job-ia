# job-ia v2

Agente diario de búsqueda de empleo en LinkedIn. Madrid, últimas 24h, solo lo nuevo.

**Lee `DISENO.md` antes de tocar nada.** Y `CLAUDE.md` si trabajas con Claude Code.

## Arranque

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env        # rellena GROQ_API_KEY y las de Gmail

# PUERTA 0 — obligatoria antes de desarrollar nada
python scripts/gate0_linkedin.py
```

Tres corridas VERDE separadas >=4h y se sigue. Si sale ROJO, el problema es la
IP o el endpoint, y ninguna cantidad de código lo arregla.

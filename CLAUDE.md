# CLAUDE.md — instrucciones para Claude Code

## Que es esto
Agente que busca ofertas en LinkedIn (Madrid, ultimas 24h), se queda solo con
las nuevas, las filtra en embudo y manda un email diario con las mejores.
Reescritura de `borjajmota/job-ia`, que fallo por bloqueo de IP no detectado.

## Regla numero uno
**Una lista vacia NUNCA es "no hay ofertas".** Es bloqueo hasta que se demuestre
lo contrario. Cualquier codigo que capture una excepcion de red y siga adelante
con `[]` esta reintroduciendo el bug que mato a la v1. Usa `SourceBlocked`.

## Orden de trabajo (no lo alteres)
1. **Puerta 0** — `python scripts/gate0_linkedin.py`. Tres corridas VERDE
   separadas >=4h. Sin esto, no escribas nada mas.
2. `sources/linkedin.py` — ya escrito. Verificalo contra el resultado real
   de la Puerta 0 y ajusta los selectores si el HTML cambio.
3. `store.py` — ya escrito. Test: dos corridas seguidas, la segunda da 0 nuevas.
4. `pipeline/rules.py` (L2) — filtros de `config/rules.yaml`. Deterministas.
5. `pipeline/semantic.py` (L3) — embeddings locales, top_k. Sin red.
6. `pipeline/scoring.py` (L4) — Groq, salida `ScoredJob` validada con pydantic.
7. `notify/email.py` — plantilla Jinja2, HTML sobrio.
8. `graph.py` — cablear los nodos. **Al final, no al principio.**

## Invariantes
- Las descripciones completas se bajan **solo** en `enrich_details`, para <=20
  ofertas. Bajarlas para 200 es lo que gana un bloqueo.
- Todo lo que sale del LLM se valida con pydantic antes de tocar la BD.
- `JOBIA_REPLAY=1` desarrolla contra la cache en disco. Usalo siempre que
  iteres en L2/L3/L4: no hay razon para pegarle a LinkedIn 40 veces seguidas.
- `JOBIA_DRY_RUN=1` imprime el email en consola en vez de enviarlo.
- La BD vive fuera del repo. Nunca la commitees.

## Estilo
- Python 3.11+, type hints, pydantic v2, ruff (linea 100).
- Sin dependencias nuevas sin justificarlo en el PR.
- Comentarios en castellano, codigo en ingles.
- Un commit por nivel del embudo.

## Definicion de hecho (v1 entregable)
Corre solo de lunes a viernes, manda un email con entre 3 y 8 ofertas nuevas
relevantes con enlace directo, no repite ninguna, y si LinkedIn corta el grifo
me llega una alarma el mismo dia.

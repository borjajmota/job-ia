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
- Las descripciones completas se bajan **solo** en `enrich_details`, para
  todo lo que haya sobrevivido L2 -- sin tope fijo (decision del
  2026-09-16: antes era <=20/<=18, pero eso descartaba en base al ranking
  semantico de L3, que es la señal mas debil del embudo, tirando candidatas
  buenas que R0-R6 en L2 ya habia validado). La seguridad ya no viene de un
  numero pequeño: viene de que `enrich_details` para el bucle en el acto en
  cuanto aparece un `SourceBlocked` (no sigue intentando el resto) y de que
  L2 filtra mucho mas fuerte que antes. Si algun dia L2 deja pasar
  cientos de ofertas de verdad, eso es señal de que L2 se rompio, no una
  razon para bajar el tope otra vez sin mirar por que.
- Todo lo que sale del LLM se valida con pydantic antes de tocar la BD.
- `JOBIA_REPLAY=1` desarrolla contra la cache en disco -- pero tambien
  afecta a `enrich_details` (descripciones), no solo a la busqueda: si
  necesitas descripciones reales de verdad con busqueda en cache, hazlo en
  dos pasos (nodos por separado), no con el CLI de una sola pasada.
- `JOBIA_DRY_RUN=1` imprime el email en consola en vez de enviarlo.
- La BD vive fuera del repo. Nunca la commitees.
- `jobia run` no corre dos veces el mismo dia si ya hubo una corrida sin
  bloqueo (`Store.already_ran_today()`, guard en `cli.py`). `--force` lo
  salta.
- El checkpointer de LangGraph (`SqliteSaver`, cableado en `cli.py`) permite
  reanudar una corrida cortada a mitad sin repetir las queries ya hechas.
- `jobia` carga `.env` si existe (python-dotenv, declarado desde el
  principio pero sin usar hasta el 2026-09-17). No afecta a Actions (los
  secrets llegan como variables de entorno reales, no hay `.env` en el
  checkout del runner).

## Dashboard
`pip install -e ".[dashboard]"` (streamlit/ruamel.yaml son un extra
aparte: ni daily.yml ni ci.yml los necesitan, no tiene sentido pagar ese
peso ahi). Luego `streamlit run src/jobia/dashboard/app.py` o
`scripts/dashboard.ps1` (ya apunta `JOBIA_DB_PATH`/`JOBIA_CHECKPOINT_PATH`
a la BD real del runner). Solo escucha en localhost
(`.streamlit/config.toml`) -- no lo cambies sin querer exponerlo fuera de
este PC. Historial de corridas (con aviso si la ultima se bloqueo o hace
demasiado que no corre ninguna), KPIs (con % de conversion por paso, no
solo el conteo absoluto), lanzar una corrida manual (ventana 24h/7d/30d,
con o sin guardar en `job_seen`) y configuracion de
`searches.yaml`/`rules.yaml` (los valores simples; el arbol completo de
`title_rules` R0-R6 sigue siendo solo de archivo).

Subido a origin/main el 2026-09-17, confirmado por el usuario.

## Estilo
- Python 3.11+, type hints, pydantic v2, ruff (linea 100).
- Sin dependencias nuevas sin justificarlo en el PR.
- Comentarios en castellano, codigo en ingles.
- Un commit por nivel del embudo.

## Definicion de hecho (v1 entregable)
Corre solo de lunes a viernes, manda un email con entre 3 y 8 ofertas nuevas
relevantes con enlace directo, no repite ninguna, y si LinkedIn corta el grifo
me llega una alarma el mismo dia.

# Cómo arrancar, paso a paso

## Paso 1 — Llevarte el scaffold al repo (5 min)

```bash
cd ~/proyectos
git clone https://github.com/borjajmota/job-ia.git
cd job-ia
git checkout -b v2

# archiva la v1: los logs son la evidencia del diagnóstico, no los borres
mkdir -p v1_archivo
git mv job_agent.py profile.yaml seen_jobs.txt application_queue.json logs v1_archivo/

unzip ~/Descargas/job-ia-v2.zip -d /tmp/
cp -r /tmp/v2/. .
rm -rf /tmp/v2

git add -A && git commit -m "v2: scaffold, perfil mapeado al embudo, archivo de v1"
```

## Paso 2 — Puerta 0 (30 min de reloj, 5 de trabajo)

**Desde tu máquina. No desde Actions.**

```bash
python -m venv .venv && source .venv/bin/activate
pip install python-jobspy beautifulsoup4 curl_cffi
python scripts/gate0_linkedin.py
```

Repítelo dos veces más, separado ≥4h. Tres VERDE y se sigue.

Si sale ROJO en el typeahead: tu IP está marcada. Prueba desde el móvil
compartiendo datos. Si desde ahí va, es tu IP fija; si no, toca Apify.

**No avances de paso hasta tener el veredicto.**

## Paso 3 — Abrir Claude Code

```bash
claude
```

Lee `CLAUDE.md` solo. Primer mensaje sugerido:

> Lee CLAUDE.md y DISENO.md. La Puerta 0 salió VERDE con estos resultados:
> [pega el JSON de gate0_runs/]. Empieza por el punto 2 del orden de trabajo:
> verifica sources/linkedin.py contra ese resultado real y ajusta los selectores
> si hacen falta. No toques nada más todavía.

## Paso 4 — Un commit por nivel

Un mensaje de Claude Code por cada uno. No los juntes: si algo se rompe,
quieres saber en qué nivel.

| # | Qué | Criterio de hecho |
|---|---|---|
| 1 | `sources/linkedin.py` verificado | Trae ofertas reales y guarda caché |
| 2 | `store.py` | Dos corridas seguidas: la 2ª da 0 nuevas |
| 3 | `pipeline/rules.py` (L2) | Filtra ETTs y data scientist puro |
| 4 | `pipeline/semantic.py` (L3) | Top-18 con `aggregation: max` |
| 5 | `pipeline/scoring.py` (L4) | `ScoredJob` validado, coherente con `calibration` |
| 6 | `notify/email.py` | `JOBIA_DRY_RUN=1` imprime el HTML |
| 7 | `graph.py` | Cablea todo + `route_after_fetch` |
| 8 | Runner self-hosted | Corre solo un día laborable |

Trabaja con `JOBIA_REPLAY=1` en los pasos 3 a 6. No hay razón para pegarle a
LinkedIn mientras ajustas prompts.

## Paso 5 — Runner self-hosted

En GitHub: Settings → Actions → Runners → New self-hosted runner.
Al configurarlo, pon las etiquetas `self-hosted`, `linux` y **`jobia`** —
el workflow las busca por nombre.

Secrets a dar de alta: `GROQ_API_KEY`, `GMAIL_USER`, `GMAIL_APP_PASSWORD`, `EMAIL_TO`.

## Paso 6 — Validación en vivo

Una semana corriendo. Lo que miras no es si encuentra ofertas buenas, sino:

- ¿Llegó email todos los días, o silencio algún día?
- Si hubo silencio, ¿llegó la alarma?
- ¿Alguna oferta repetida?
- De las que llegaron, ¿cuántas habrías abierto?

Esa última cifra es tu métrica. Si es menos de la mitad, el problema está en
`scoring_guidance`, no en el código.

---

## Qué revisar tú del perfil antes de empezar

Lo he rellenado con lo que tengo. Tres cosas quiero que confirmes:

1. **Inglés**: C1 o B2. Está sin cerrar desde la revisión del CV.
2. **`relocation_spain: true`** — lo he puesto en `true`. Cámbialo si San
   Sebastián fue una excepción y no la regla.
3. **`penalty_signals`** incluye "gobierno corporativo puro". Es lo que te ha
   ido apartando de los puestos que no te llenaban. Si en algún momento sí
   quieres ver esos, quítalo.

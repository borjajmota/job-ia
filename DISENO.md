# job-ia v2 — Diseño

---

## 1. Por qué falló la v1 (con tus propios datos)

Tus logs contestan la pregunta. 26 corridas registradas:

| Corrida | Ofertas |
|---|---|
| 1ª (31 mar) | **83** |
| 2ª a 26ª (abr) | 0, 1, 1, 0, 0, 1, 0, 0, 1, 2, 2, 1, 1, 0, 1, 1, 1, 0, 0, 0, 0, 0, 1, 1, 0 |

**Tu búsqueda nunca estuvo rota.** El día 1 trajo 83 ofertas con el endpoint
correcto (`seeMoreJobPostings` + `geoId` + `f_TPR=r86400`). Lo que se rompió fue
la infraestructura, y tres cosas lo hicieron invisible:

| # | Causa | Evidencia en tu repo |
|---|---|---|
| 1 | Runner `ubuntu-latest` → IP de datacenter, bloqueada tras el primer día | `job_agent.yml` |
| 2 | `requests` pelado → huella TLS no-navegador | `job_agent.py:83` |
| 3 | `except Exception` en el scraping → el 429 se registró como `total: 0` | `job_agent.py:119` |

El resultado: 25 días creyendo que Madrid no publicaba empleo. Y el `cron` acabó
comentado, que es la conclusión lógica cuando un sistema no te da señal.

> **No he podido verificar el scraping desde aquí**: mi entorno solo alcanza
> dominios de paquetes, `linkedin.com` está bloqueado. Pero tus 83 ofertas del
> día 1 son mejor evidencia que cualquier prueba mía. La Puerta 0 confirma que
> sigue vivo hoy, desde tu IP.

---

## 2. Las tres decisiones que lo arreglan

| Decisión | Por qué |
|---|---|
| Runner **self-hosted** en tu máquina | IP residencial. Mantienes Actions como orquestador, a 0 € |
| `curl_cffi` con huella Chrome | El bloqueo por fingerprint TLS desaparece |
| `SourceBlocked` explícito | Un 429 **grita**. Nunca más silencio |

---

## 3. El embudo

Barato primero, caro al final. Solo las supervivientes cuestan dinero.

| Nivel | Qué hace | Coste | Volumen |
|---|---|---|---|
| **L0** Consultas | 4 queries × Madrid × 24h | 0 | → 100-200 |
| **L1** Novedad | `job_id` + `repost_key` contra SQLite | 0 | → 20-40 |
| **L2** Reglas | títulos, ETTs, seniority (`rules.yaml`) | 0 | → 15-25 |
| **L3** Semántico | embeddings locales vs tu perfil | 0 | → 18 |
| **L4** LLM | **aquí y solo aquí** se bajan descripciones | ~0 € | → 3-8 al email |

> La frontera L3→L4 es la de seguridad: bajar 200 descripciones te bloquea,
> bajar 18 no.

---

## 4. Lo que hace que mejore uso a uso

Esto es lo que le faltaba a la v1 y lo que convierte un script en un sistema.

### 4.1 Alarma de silencio ★
Si **todas** las queries vuelven vacías, eso no es "hoy no hay ofertas": es
bloqueo. Te llega un email de alarma. Es la corrección directa de tus 25 días
perdidos, y es la línea de código más valiosa del proyecto.

### 4.2 Salud por query
Cada query se vigila por separado. Si `data_ia` lleva 3 corridas a cero mientras
`cloud_architect` trae 12, no es bloqueo: esa query ha muerto y hay que
reformularla. Sin esto, se diluye en el total.

### 4.3 Umbral adaptativo
Un corte fijo en 75 te da 0 ofertas una semana y 40 la siguiente. El umbral es
el percentil 80 de los últimos 30 días, con suelo en 65. El sistema se calibra
solo a medida que acumula historia.

### 4.4 Detección de republicaciones
Los recruiters resubén la misma oferta con `job_id` nuevo. La `repost_key`
(hash de empresa+título+ubicación normalizados) la caza. Sin esto, la misma
oferta te llega cada lunes.

### 4.5 Modo replay
`JOBIA_REPLAY=1` trabaja contra caché en disco. Puedes iterar en las reglas y
los prompts 50 veces sin tocar LinkedIn ni una vez. Es lo que te permite
desarrollar sin quemar la IP — justamente lo que te pasó.

### 4.6 Feedback humano *(fase 2, esquema ya listo)*
Respondes al email con los IDs que te interesan. Un job lee la respuesta por
IMAP y llena la tabla `feedback`. Esos ejemplos se inyectan como few-shot en L4.
El sistema aprende tu criterio real, no el que declaraste en el perfil.
La tabla existe desde el día 1 para no migrar después.

---

## 5. Tu historia de usuario

```
09:00  Llega un email. O no llega nada.
       Si no llega nada, es que no había nada nuevo que merezca tu tiempo.
       Si LinkedIn nos ha cortado, llega una alarma. Nunca hay silencio ambiguo.

       El email trae 3-8 ofertas. Por cada una:
         score · 2 razones de encaje · 1 gap · señal roja si la hay
         [Ver en LinkedIn]

09:03  Has terminado. Abres las 2 que te interesan y te inscribes tú.

(fase 2) Respondes "4383283189, 4383793671" y el sistema aprende.
```

Tres minutos al día. Si pide más, está mal diseñado.

---

## 6. Stack

| Capa | Elección | Coste |
|---|---|---|
| Ingesta | JobSpy + endpoint guest de respaldo, tras puerto `JobSource` | 0 € |
| Orquestación | GitHub Actions, runner self-hosted | 0 € |
| Estado | SQLite en disco del runner | 0 € |
| Grafo | LangGraph + `SqliteSaver` | 0 € |
| L3 | `multilingual-e5-base` local | 0 € |
| L4 | Groq, tier gratuito | 0 € |
| Email | SMTP Gmail, app password | 0 € |
| | **Total** | **0 €/mes** |

Si algún día te bloquean pese a todo: cambias `LinkedInSource` por un adaptador
de Apify (~2 €/mes) y no tocas nada más. Para eso está el puerto.

---

## 7. Riesgos

| Riesgo | Probabilidad | Mitigación |
|---|---|---|
| LinkedIn bloquea tu IP residencial | Media | 4 queries/día con pausas. Alarma inmediata. Apify de reserva |
| Cambia el HTML del endpoint guest | Media | Dos vías (JobSpy + crudo): difícil que caigan a la vez |
| Máquina apagada a las 09:00 | Alta | Actions reintenta; ejecución manual con `workflow_dispatch` |
| Términos de uso de LinkedIn | — | Uso personal, bajo volumen, sin republicar. El riesgo real es un bloqueo, no una demanda |

---

## 8. Hacia dónde crece

Ya cabe sin rediseñar nada:

- **CV a medida**: segunda entrada al grafo por checkpoint, sobre una oferta
  concreta. Solo puede afirmar hechos con `source_doc_id` de tu perfil.
- **Perfil madurable**: `/profile/raw/` → extracción a hechos tipados con
  trazabilidad → `profile_version` → re-scoring cuando madura.
- **Motor de reglas**: `on(score>=85 and remoto) => generar_cv`.
- **Portal**: FastAPI de solo lectura sobre la misma SQLite. Al final, no antes.

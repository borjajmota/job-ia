# Agente Diario de Empleo — Borja Jiménez Mota

Busca ofertas en LinkedIn cada día laboral, las analiza con IA y te manda
un informe ordenado por match a tu email. Coste total: **0€**.

---

## Estructura del repo

```
├── job_agent.py           ← Script principal
├── profile.yaml           ← Tu perfil (editable)
├── requirements.txt       ← Dependencias Python
├── seen_jobs.txt          ← IDs procesados (auto-generado)
├── application_queue.json ← Cola de aplicación (auto-generado)
└── .github/
    └── workflows/
        └── job_agent.yml  ← Cron de GitHub Actions
```

---

## Setup paso a paso

### 1. Crear el repositorio en GitHub

1. Ve a github.com → New repository
2. Nombre: `job-agent` (o el que quieras)
3. **Privado** — importante, aquí irán tus credenciales
4. Sube todos los ficheros de este proyecto

### 2. Obtener la cookie `li_at` de LinkedIn

Esta cookie es la que autentica el scraper con tu sesión de LinkedIn Premium.

1. Abre LinkedIn en Chrome y haz login
2. Abre DevTools → Application → Cookies → `https://www.linkedin.com`
3. Busca la cookie llamada `li_at`
4. Copia su valor (es una cadena larga)

> Esta cookie expira periódicamente. Si el scraper deja de funcionar, renuévala.

### 3. Obtener la Gemini API Key (gratuita)

1. Ve a [https://aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)
2. Crea una API Key gratuita
3. El tier gratuito da 1.500 requests/día — más que suficiente

### 4. Obtener Gmail App Password

Para enviar emails con tu cuenta de Gmail sin usar tu contraseña principal:

1. Ve a tu cuenta de Google → Seguridad → Verificación en dos pasos (actívala si no la tienes)
2. Busca "Contraseñas de aplicaciones"
3. Crea una nueva: nombre "job-agent"
4. Guarda los 16 caracteres que te da

### 5. Configurar los Secrets en GitHub

Ve a tu repo en GitHub → Settings → Secrets and variables → Actions → New repository secret

Añade estos 5 secrets:

| Secret               | Valor                                        |
|----------------------|----------------------------------------------|
| `GEMINI_API_KEY`     | Tu API key de Google AI Studio               |
| `GMAIL_USER`         | Tu dirección Gmail (borja.jmota@gmail.com)   |
| `GMAIL_APP_PASSWORD` | Los 16 caracteres del App Password           |
| `EMAIL_TO`           | Dirección donde recibir el informe (puede ser la misma) |
| `LI_AT_COOKIE`       | El valor de la cookie `li_at` de LinkedIn    |

### 6. Activar el workflow

1. Ve a tu repo → Actions
2. Si ves un aviso de "workflows disabled", haz clic en "Enable"
3. Puedes ejecutarlo manualmente con "Run workflow" para probar antes del primer cron

---

## Personalización

### Cambiar hora de ejecución

En `.github/workflows/job_agent.yml` edita el cron:
```yaml
- cron: '0 8 * * 1-5'   # 08:00 UTC lunes-viernes
- cron: '0 7 * * 1-5'   # 07:00 UTC (09:00 Madrid invierno)
- cron: '0 6 * * *'     # 06:00 UTC todos los días
```

### Cambiar búsquedas

En `job_agent.py` edita `SEARCH_QUERIES`:
```python
SEARCH_QUERIES = [
    "Data AI Cloud",
    "Head of Data",
    "Solutions Architect",
    # añade o quita las que quieras
]
```

### Ajustar el perfil

Edita `profile.yaml` directamente. Cuanto más rico sea el perfil,
mejor será el análisis de match. Especialmente útil actualizar:
- `ideal_role.titles_of_interest` — si cambias de objetivo
- `disqualifiers` — para afinar el pre-filtro
- `compensable_gaps` — para que la IA sea más justa con los matices

---

## Cola de aplicación (`application_queue.json`)

El fichero acumula todas las ofertas válidas ordenadas por score.
Para marcar una como aplicada, edita el campo `"applied": true`.

Ejemplo de entrada:
```json
{
  "date_found": "2025-03-29",
  "applied": false,
  "classification": "VÁLIDA",
  "score": 9,
  "title": "Head of Data & AI",
  "company": "Empresa X",
  "link": "https://linkedin.com/jobs/...",
  "match_summary": "Encaje muy alto. Rol híbrido arquitectura + liderazgo, sector tech."
}
```

---

## Troubleshooting

**El scraper no encuentra ofertas**
→ La cookie `li_at` ha expirado. Renuévala en GitHub Secrets.

**Error de Gemini**
→ Verifica que la API key está bien copiada. El tier gratuito tiene límite de RPM;
si procesas muchas ofertas a la vez, añade un `time.sleep(2)` entre llamadas.

**No llega el email**
→ Verifica que el App Password es correcto y que tienes verificación en 2 pasos activa en Google.

**GitHub Actions no se ejecuta**
→ Los repos sin actividad durante 60 días tienen el cron pausado.
Haz un commit cualquiera para reactivarlo.

---

## Coste

| Servicio         | Plan    | Coste |
|------------------|---------|-------|
| GitHub Actions   | Free    | 0€    |
| Gemini API       | Free    | 0€    |
| Gmail SMTP       | Free    | 0€    |
| LinkedIn Premium | Ya tienes | — |
| **Total**        |         | **0€** |

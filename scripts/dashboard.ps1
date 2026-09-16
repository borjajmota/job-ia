# Lanza el dashboard local de job-ia apuntando a la BD real del runner
# (la misma que usa daily.yml), no a data/jobia.db del repo (que estaria
# vacia). Requiere haber instalado el proyecto en el venv: pip install -e .
#
# Uso:
#   .\scripts\dashboard.ps1
#
# Solo escucha en localhost (.streamlit/config.toml) -- no accesible desde
# fuera de este PC.

$env:JOBIA_DB_PATH = "C:\actions-runner\_work\job-ia\jobia-state\jobia.db"
$env:JOBIA_CHECKPOINT_PATH = "C:\actions-runner\_work\job-ia\jobia-state\checkpoints.db"

streamlit run src/jobia/dashboard/app.py

"""R0-R6 contra ejemplos reales de la corrida 20260916-080036 (ver
conversacion de diseño): valida que las 5 ofertas irrelevantes que llegaron
al LLM esa corrida se descartan ahora en L2, sin gastar nada."""
from __future__ import annotations

from jobia.models import Job
from jobia.pipeline.rules import apply, load_rules, score_title

RULES = load_rules()["title_rules"]
FULL_RULES = load_rules()


def _band(title: str, company: str) -> str:
    band, _score, _reasons = score_title(title, company, RULES)
    return band


def test_kill_specialist_sin_dominio_ni_seniority():
    # Atradius: "specialist" NO debe matchear "ia" como substring (bug viejo).
    assert _band("IT Application & Integration Specialist", "Atradius") == "KILL"


def test_kill_empresa_carnica():
    # Inetum esta en la lista carnica -> kill por empresa, independiente del titulo.
    assert _band("Arquitecto/a Salesforce", "Inetum") == "KILL"


def test_kill_empresa_carnica_grupo_digital():
    assert _band("Ingeniero/a de Datos e IA (Hibrido)", "Grupo Digital") == "KILL"


def test_kill_ai_engineer_sin_seniority():
    assert _band("AI Engineer", "Mática Partners") == "KILL"
    assert _band("AI Engineer", "Jobgether") == "KILL"


def test_review_o_pass_platform_architect():
    band = _band("Platform Architect", "CGI")
    assert band in ("PASS", "REVIEW")


def test_r0_excepcion_pasa_sin_evaluar_kills():
    band = _band("Engineering Manager - Data Platform", "Cualquier Empresa")
    assert band in ("PASS", "REVIEW")


def test_r2_kill_solo_si_abre_titulo():
    assert _band("VP Engineering", "Acme") == "KILL"
    # "VP" en medio del titulo no debe disparar R2.
    assert _band("Head of Data reporting to VP", "Acme") != "KILL"


def test_limite_de_palabra_evita_falso_positivo_cto_en_victory():
    # "victory" contiene "cto" como substring; no debe disparar R2.
    band = _band("Head of Engineering - Project Victory", "Acme")
    assert band != "KILL"


def test_ai_solutions_architect_no_se_mata_por_vendor():
    # "AI Solutions Architect" esta en titles_of_interest/semantic_anchors
    # del perfil; el kill de R1 a "solutions architect" es para el generico
    # sin dominio (pre-sales), no para este arquetipo.
    assert _band("AI Solutions Architect", "Acme") != "KILL"
    assert _band("Solutions Architect", "Acme") == "KILL"


# 2026-09-16: 10 ofertas que el usuario encontro a mano filtrando LinkedIn
# por titulo. R3 en su version original (dominio Y seniority) mataba 4 de
# las 10 porque el dominio vivia en la empresa/contexto, no en el titulo
# literal. Ver ajuste en score_title: seniority sola ya basta.
REAL_EXAMPLES_2026_09_16 = [
    ("AI Transformation Manager", "KPMG SA"),
    ("Data & AI Manager", "TeamSystem"),
    ("Technical Manager – Data Architecture & AI (Databricks)", "Empresa confidencial"),
    ("Migration Leader - CDAIO", "Santander"),
    ("Service Delivery Manager", "Cisco"),
    ("Technical Delivery Manager - Madrid", "Legora"),
    ("Head of Engineering", "Ailin.health"),
    ("Head of AI Transformation", "Make"),
    ("Head of Technology & AI, APAC, Europe and ESS", "AXA XL"),
    ("Lead AI Deployment Architect", "Celoris"),
]


def test_ofertas_curadas_a_mano_no_mueren():
    for title, company in REAL_EXAMPLES_2026_09_16:
        band = _band(title, company)
        assert band != "KILL", f"{title!r} ({company}) no deberia morir en L2"


def _j(job_id, title, company):
    return Job(job_id=job_id, title=title, company=company,
               url=f"https://www.linkedin.com/jobs/view/{job_id}")


def test_apply_desglosa_bajas_por_regla():
    jobs = [
        _j("1", "IT Application & Integration Specialist", "Atradius"),  # R3
        _j("2", "Arquitecto/a Salesforce", "Inetum"),                    # R4:carnica
        _j("3", "AI Engineer", "Jobgether"),                             # R3
        _j("4", "Head of Data", "Acme"),                                 # sobrevive
    ]
    survivors, breakdown = apply(jobs, FULL_RULES)
    assert len(survivors) == 1
    assert survivors[0].job_id == "4"
    assert breakdown["R3"] == 2
    assert breakdown["R4:carnica"] == 1
    assert sum(breakdown.values()) == 3

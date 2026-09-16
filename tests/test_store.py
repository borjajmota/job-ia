"""Store: el nivel que convierte el proceso en iterativo. CLAUDE.md pide
explicitamente el test de "dos corridas seguidas, la segunda da 0 nuevas" --
nunca existio hasta ahora."""
from __future__ import annotations

from datetime import UTC, datetime

from jobia.models import Job, QueryHealth, RunReport, ScoredJob
from jobia.store import Store


def _job(job_id: str, title: str = "Data Engineering Manager",
         company: str = "Acme", location: str = "Madrid") -> Job:
    return Job(job_id=job_id, title=title, company=company, location=location,
               url=f"https://www.linkedin.com/jobs/view/{job_id}")


def test_dos_corridas_seguidas_segunda_da_cero_nuevas(tmp_path):
    store = Store(tmp_path / "jobia.db")
    jobs = [
        _job("1", title="Data Engineering Manager", company="Acme"),
        _job("2", title="Head of AI", company="Otra Co"),
        _job("3", title="AI Solutions Architect", company="Tercera SL"),
    ]

    primera = store.filter_new(jobs)
    assert len(primera) == 3

    segunda = store.filter_new(jobs)
    assert segunda == []


def test_repost_detectado_aunque_cambie_el_job_id(tmp_path):
    """Misma oferta republicada con jobId nuevo: la caza el repost_key,
    no el job_id -- eso es lo que evita que la misma oferta te llegue
    cada semana con un ID distinto."""
    store = Store(tmp_path / "jobia.db")
    store.filter_new([_job("1", title="Head of Data", company="Acme", location="Madrid")])

    reposted = store.filter_new(
        [_job("999", title="Head of Data", company="Acme", location="Madrid")]
    )
    assert reposted == []


def test_job_realmente_nuevo_no_se_confunde_con_repost(tmp_path):
    store = Store(tmp_path / "jobia.db")
    store.filter_new([_job("1", title="Head of Data", company="Acme")])

    distinto = store.filter_new([_job("2", title="Head of Engineering", company="Otra Co")])
    assert len(distinto) == 1


def test_threshold_usa_floor_sin_historial_suficiente(tmp_path):
    store = Store(tmp_path / "jobia.db")
    assert store.threshold(percentile=80, floor=65, lookback_days=30) == 65


def test_threshold_percentil_con_historial_suficiente(tmp_path):
    store = Store(tmp_path / "jobia.db")
    scored = [ScoredJob(job_id=str(i), score=i, veredicto="dudoso", match=[], gaps=[])
              for i in range(1, 31)]  # scores 1..30, 30 >= 20
    store.save_scores(run_id="r1", pv=1, scored=scored)

    result = store.threshold(percentile=80, floor=10, lookback_days=30)
    assert result > 10  # el percentil manda, no el suelo
    assert result <= 30


def test_already_ran_today_false_sin_corridas(tmp_path):
    store = Store(tmp_path / "jobia.db")
    assert store.already_ran_today() is False


def test_already_ran_today_true_tras_corrida_sin_bloqueo(tmp_path):
    store = Store(tmp_path / "jobia.db")
    run_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    store.save_run(RunReport(run_id=run_id, started_at=datetime.now(UTC),
                              profile_version=1, blocked=False))
    assert store.already_ran_today() is True


def test_already_ran_today_false_si_la_unica_corrida_fue_bloqueo(tmp_path):
    """Una corrida bloqueada no cuenta como 'ya corrimos hoy': no aporto
    nada, deberia poder reintentarse."""
    store = Store(tmp_path / "jobia.db")
    run_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    store.save_run(RunReport(run_id=run_id, started_at=datetime.now(UTC),
                              profile_version=1, blocked=True))
    assert store.already_ran_today() is False


def test_consecutive_empty(tmp_path):
    store = Store(tmp_path / "jobia.db")
    for i in range(3):
        run_id = f"2026091{i}-000000"
        store.save_run(RunReport(
            run_id=run_id, started_at=datetime.now(UTC), profile_version=1,
            queries=[QueryHealth(query_id="ai", found=0, new=0)],
        ))
    assert store.consecutive_empty("ai", 3) is True
    assert store.consecutive_empty("ai", 4) is False


def test_consecutive_empty_false_si_alguna_tuvo_resultados(tmp_path):
    store = Store(tmp_path / "jobia.db")
    store.save_run(RunReport(
        run_id="20260910-000000", started_at=datetime.now(UTC), profile_version=1,
        queries=[QueryHealth(query_id="ai", found=5, new=2)],
    ))
    store.save_run(RunReport(
        run_id="20260911-000000", started_at=datetime.now(UTC), profile_version=1,
        queries=[QueryHealth(query_id="ai", found=0, new=0)],
    ))
    assert store.consecutive_empty("ai", 2) is False

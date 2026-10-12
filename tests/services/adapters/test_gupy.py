import json

from app.models.enums import EmploymentType, WorkplaceType
from app.services.adapters import base, gupy

URL = "https://vivo.gupy.io/jobs/12636503"
JOB = {
    "name": "Analista de Serviços ao Cliente Jr.",
    "description": "<p>Para que o cliente tenha uma melhor experiência.</p>",
    "responsibilities": "<p>Atender clientes.</p>",
    "prerequisites": "<li>Ensino Superior cursando</li>",
    "addressCity": "Curitiba",
    "addressState": "Paraná",
    "addressCountry": "Brasil",
    "workplaceType": "hybrid",
    "jobType": "vacancy_type_effective",
    "publishedAt": "2026-10-06T14:10:00.658Z",
    "status": "published",
    "careerPage": {"name": "Vivo (Telefônica Brasil)"},
}


def _page(job) -> str:
    data = {"props": {"pageProps": {"job": job}}}
    return f'<title>Página da Vaga</title><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'


def _scan(monkeypatch, html, url=URL):
    monkeypatch.setattr(base, "fetch_html", lambda u: base.FetchedPage(text=html, url=u))
    return gupy.scan_job_url(url)


def test_job_is_read_from_next_data(monkeypatch):
    result = _scan(monkeypatch, _page(JOB))

    assert result.success
    assert (result.title, result.company_name) == ("Analista de Serviços ao Cliente Jr.", "Vivo (Telefônica Brasil)")
    assert result.location == "Curitiba, Paraná, Brasil"
    assert (result.workplace_type, result.employment_type) == (WorkplaceType.HYBRID, EmploymentType.FULL_TIME)
    assert str(result.posted_at) == "2026-10-06"
    for text in ("melhor experiência", "Responsabilidades", "Atender clientes", "Requisitos", "Ensino Superior"):
        assert text in result.description


def test_unpublished_job_is_expired(monkeypatch):
    result = _scan(monkeypatch, _page({**JOB, "status": "closed"}))
    assert (result.success, result.expired) == (False, True)


def test_page_without_job_data_fails_without_expiring(monkeypatch):
    result = _scan(monkeypatch, "<title>Página da Vaga</title>")
    assert (result.success, result.expired) == (False, False)


def test_board_pages_and_other_hosts_are_not_claimed():
    assert gupy.scan_job_url("https://vivo.gupy.io/") is None
    assert gupy.scan_job_url("https://boards.greenhouse.io/acme/jobs/1") is None

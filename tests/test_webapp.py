"""Tester för webbappen: uppladdning, statuspolling, filnedladdning."""

import time

import pytest
from fastapi.testclient import TestClient

import webapp.app as webapp_module
from webapp.app import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp_module, "JOBS_DIR", tmp_path / "jobs")
    webapp_module.JOBS.clear()
    return TestClient(app)


def _wait_for_done(client, job_id, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.1)
    pytest.fail("Jobbet blev aldrig klart inom tidsgränsen")


def test_helt_jobb_via_api(client, drawing_pdf):
    with open(drawing_pdf, "rb") as f:
        res = client.post(
            "/api/jobs",
            files={"file": ("ritning.pdf", f, "application/pdf")},
            # ingen OCR behövs: testfilen har riktig PDF-text
            data={"scale": "1:50", "page": "1", "ocr": "off"})
    assert res.status_code == 200, res.text
    job_id = res.json()["job_id"]

    job = _wait_for_done(client, job_id)
    assert job["status"] == "done", job.get("error")
    assert job["summary"]["n_rader"] >= 3
    assert job["summary"]["skala_metod"] == "cli"
    assert set(job["files"]) >= {"markerad_pdf", "mangder_xlsx",
                                 "mangder_csv", "koder_csv", "rapport_txt"}

    # ladda ner den markerade PDF:en och mängd-CSV:n
    pdf = client.get(f"/api/jobs/{job_id}/files/{job['files']['markerad_pdf']}")
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")
    csv = client.get(f"/api/jobs/{job_id}/files/{job['files']['mangder_csv']}")
    assert csv.status_code == 200
    assert "Subject" in csv.text


def test_avvisar_icke_pdf(client):
    res = client.post("/api/jobs",
                      files={"file": ("x.pdf", b"inte en pdf", "application/pdf")})
    assert res.status_code == 400


def test_ogiltig_parameter_ger_400(client, drawing_pdf):
    with open(drawing_pdf, "rb") as f:
        res = client.post(
            "/api/jobs",
            files={"file": ("ritning.pdf", f, "application/pdf")},
            data={"pipe_width": "bred"})
    assert res.status_code == 400


def test_okant_jobb_ger_404(client):
    assert client.get("/api/jobs/finnsinte").status_code == 404


def test_fil_utanfor_jobbet_ger_404(client, drawing_pdf):
    with open(drawing_pdf, "rb") as f:
        res = client.post("/api/jobs",
                          files={"file": ("ritning.pdf", f, "application/pdf")},
                          data={"scale": "1:50", "ocr": "off"})
    job_id = res.json()["job_id"]
    _wait_for_done(client, job_id)
    # bara registrerade utdatafiler får hämtas – inte godtyckliga namn
    assert client.get(f"/api/jobs/{job_id}/files/ritning.pdf").status_code == 404
    assert client.get(f"/api/jobs/{job_id}/files/..%2Fritning.pdf").status_code == 404


def test_startsidan_serveras(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "Mängdning" in res.text


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}

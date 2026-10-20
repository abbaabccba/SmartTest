import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import sleep

from fastapi.testclient import TestClient
from core.logging import SMARTTEST_LOG_DIR_ENV

from smarttest_web.app import create_app


class RecordingOwner:
    def __init__(self):
        self.filters_arg = None
        self.performance_arg = None

    def get_filters(self, filters):
        self.filters_arg = filters
        return {
            "productLines": ["Consumer"], "projects": ["Apollo"],
            "projectOptions": [{"value": "Apollo", "label": "Apollo"}],
            "standards": ["802.11be"], "testReports": ["raw.csv"],
            "reportNames": ["Performance-1"], "brands": [], "mainChips": [],
            "ecosystems": [], "massProductionStatuses": [], "dutConnectTypes": [],
            "wifiModules": [], "interfaces": [],
        }

    def get_performance(self, filters):
        self.performance_arg = filters
        return {
            "data": [{"pathLossDb": 10.0, "throughputAvgMbps": 900.0, "reportName": "Performance-1"}],
            "summary": {"count": 1},
            "filters": {"productLines": filters.product_lines},
            "metadata": {"requestedLimit": filters.limit, "appliedLimit": filters.limit, "totalReturned": 1, "truncated": False},
        }


def test_health_does_not_resolve_database_owner():
    app = create_app(query_owner=lambda: (_ for _ in ()).throw(AssertionError("DB resolved")))
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_default_app_session_database_is_isolated_by_backend_fixture(isolate_server_credentials):
    app = create_app(query_owner=lambda: (_ for _ in ()).throw(AssertionError("DB resolved")))
    assert TestClient(app).get("/health").status_code == 200
    assert not isolate_server_credentials.database_path.exists()
    assert set(isolate_server_credentials.credential_stores) == {
        "smarttest_preparation_test"
    }


def test_importing_backend_app_does_not_create_default_app_data(tmp_path):
    repository_root = Path(__file__).resolve().parents[3]
    python_path = os.pathsep.join((str(repository_root / "web" / "backend"), str(repository_root)))
    environment = dict(os.environ, LOCALAPPDATA=str(tmp_path), PYTHONPATH=python_path)
    result = subprocess.run(
        [sys.executable, "-c", "import smarttest_web.app"], cwd=os.getcwd(), env=environment,
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "Amlogic" / "SmartTest" / "web" / "smarttest-web.db").exists()


def test_default_asgi_app_is_initialized_once_under_concurrency(monkeypatch):
    import smarttest_web.app as app_module
    created = []
    monkeypatch.setattr(app_module, "_default_app", None)

    def factory():
        sleep(.02)
        value = object(); created.append(value); return value

    monkeypatch.setattr(app_module, "create_configured_app", factory)
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(lambda _index: app_module._get_default_app(), range(16)))
    assert len(created) == 1
    assert all(value is created[0] for value in values)


def test_repeated_snake_case_filters_reach_owner_and_response_keeps_camel_case():
    owner = RecordingOwner()
    client = TestClient(create_app(query_owner=lambda: owner))
    response = client.get(
        "/api/filters",
        params=[("product_line", "Consumer"), ("product_line", "Enterprise"),
                ("project", "Apollo"), ("report_name", "Performance-1"),
                ("standard", "802.11be"), ("data_type", "performance")],
    )
    assert response.status_code == 200
    assert owner.filters_arg.product_lines == ["Consumer", "Enterprise"]
    assert owner.filters_arg.data_type == "PEAK_THROUGHPUT"
    assert response.json()["projectOptions"] == [{"value": "Apollo", "label": "Apollo"}]


def test_legacy_aliases_and_advanced_multivalue_filters_reach_owner():
    owner = RecordingOwner()
    client = TestClient(create_app(query_owner=lambda: owner))
    response = client.get("/api/performance", params=[
        ("projectId", "7"), ("wifi_module", "W2"), ("interface", "SDIO"),
        ("band", "5G"), ("bandwidth_mhz", "80"), ("test_report_csv_name", "rvr.csv"),
        ("device_type", "adb_device"), ("device_value", "SERIAL-1"),
        ("path_loss_min", "10.5"), ("rssi_max", "-30"), ("max_points", "50"),
    ])
    assert response.status_code == 200
    filters = owner.performance_arg
    assert filters.project_ids == [7]
    assert filters.wifi_modules == ["W2"]
    assert filters.interfaces == ["SDIO"]
    assert filters.bands == ["5G"]
    assert filters.bandwidths_mhz == [80.0]
    assert filters.test_report_csv_names == ["rvr.csv"]
    assert filters.device_column == "adb_device"
    assert filters.device_values == ["SERIAL-1"]
    assert filters.path_loss_min == 10.5
    assert filters.rssi_max == -30
    assert filters.limit == 50


def test_invalid_device_column_is_rejected_before_database_access():
    owner = RecordingOwner()
    response = TestClient(create_app(query_owner=lambda: owner)).get(
        "/api/performance?device_type=password&device_value=secret"
    )
    assert response.status_code == 422
    assert owner.performance_arg is None


def test_non_integer_project_id_is_ignored_instead_of_truncated():
    owner = RecordingOwner()
    response = TestClient(create_app(query_owner=lambda: owner)).get('/api/performance?projectId=7.9')
    assert response.status_code == 200
    assert owner.performance_arg.project_ids == []


def test_documented_camel_case_datatype_alias_reaches_owner():
    owner = RecordingOwner()
    response = TestClient(create_app(query_owner=lambda: owner)).get('/api/performance?dataType=RVO')
    assert response.status_code == 200
    assert owner.performance_arg.data_type == 'RVO'


def test_performance_contract_and_limit_are_forwarded():
    owner = RecordingOwner()
    response = TestClient(create_app(query_owner=lambda: owner)).get(
        "/api/performance?data_type=RVO&start_date=2026-08-01&end_date=2026-08-24&limit=25"
    )
    assert response.status_code == 200
    assert owner.performance_arg.data_type == "RVO"
    assert owner.performance_arg.limit == 25
    assert response.json()["data"][0]["throughputAvgMbps"] == 900.0


def test_database_failure_is_safe_503(tmp_path, monkeypatch):
    monkeypatch.setenv(SMARTTEST_LOG_DIR_ENV, str(tmp_path))

    class FailedOwner(RecordingOwner):
        def get_performance(self, filters):
            raise RuntimeError("password=secret SELECT * FROM private")

    response = TestClient(create_app(query_owner=lambda: FailedOwner())).get("/api/performance")
    assert response.status_code == 503
    assert response.json() == {"detail": "Wi-Fi Database is unavailable."}
    assert "secret" not in response.text
    assert "SELECT" not in response.text
    rows = [
        json.loads(line)
        for line in (tmp_path / "smarttest.log").read_text(encoding="utf-8").splitlines()
    ]
    request_rows = [row for row in rows if row["source"] == "request"]
    assert len(request_rows) == 1
    assert request_rows[0]["level"] == "error"
    assert request_rows[0]["extra"]["status"] == 503
    assert "secret" not in json.dumps(request_rows[0])
    assert "SELECT" not in json.dumps(request_rows[0])


def test_database_owner_configuration_failure_is_safe_503():
    def missing_configuration():
        raise RuntimeError("WIFI_DB_PASSWORD missing secret detail")

    client = TestClient(create_app(query_owner=missing_configuration), raise_server_exceptions=False)
    response = client.get("/api/filters")
    assert response.status_code == 503
    assert response.json() == {"detail": "Wi-Fi Database is unavailable."}
    assert "PASSWORD" not in response.text


def test_only_approved_routes_exist():
    client = TestClient(create_app(query_owner=lambda: RecordingOwner()))
    assert client.post("/api/performance").status_code == 405
    assert client.get("/api/users").status_code == 404


def test_request_is_logged_once_without_sensitive_headers(tmp_path, monkeypatch):
    monkeypatch.setenv(SMARTTEST_LOG_DIR_ENV, str(tmp_path))
    response = TestClient(create_app(query_owner=lambda: RecordingOwner())).get(
        "/health", headers={"authorization": "secret-token", "cookie": "secret-cookie"}
    )
    rows = [json.loads(line) for line in (tmp_path / "smarttest.log").read_text(encoding="utf-8").splitlines()]
    request_rows = [row for row in rows if row["source"] == "request"]
    assert len(request_rows) == 1
    row = request_rows[0]
    assert row["platform"] == "web"
    assert row["extra"]["method"] == "GET"
    assert row["extra"]["path"] == "/health"
    assert row["extra"]["status"] == 200
    assert row["extra"]["duration_ms"] >= 0
    assert "secret" not in json.dumps(row)
    assert response.headers["x-request-id"] == row["request_id"]

from __future__ import annotations

from types import ModuleType

from fastapi.testclient import TestClient


def test_home_page_contains_deployment_checks(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "The FastAPI demo is running." in response.text
    assert "Call the JSON endpoint" in response.text
    assert "(none — app is mounted at the site root)" in response.text
    assert 'const endpoint = "/api/hello"' in response.text


def test_health_endpoint(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_hello_endpoint_without_a_proxy_prefix(client: TestClient) -> None:
    response = client.get("/api/hello")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "message": "The browser reached the JSON endpoint through the application prefix.",
        "root_path": "",
        "detected_base_path": "",
    }


def test_connect_absolute_base_url_prefixes_links_and_requests(client: TestClient) -> None:
    response = client.get(
        "/",
        headers={"RStudio-Connect-App-Base-URL": "https://connect.example.gov/content/demo/"},
    )

    assert response.status_code == 200
    assert "Detected application base path: <code>/content/demo</code>" in response.text
    assert 'href="/content/demo/about"' in response.text
    assert 'href="/content/demo/health"' in response.text
    assert 'const endpoint = "/content/demo/api/hello"' in response.text


def test_connect_path_only_base_url_is_supported(client: TestClient) -> None:
    response = client.get(
        "/api/hello",
        headers={"RStudio-Connect-App-Base-URL": "/content/demo"},
    )

    assert response.status_code == 200
    assert response.json()["detected_base_path"] == "/content/demo"


def test_workbench_root_path_prefixes_navigation(demo_app_module: ModuleType) -> None:
    with TestClient(demo_app_module.app, root_path="/s/session/p/8000") as client:
        home = client.get("/")
        hello = client.get("/api/hello")

    assert home.status_code == 200
    assert 'href="/s/session/p/8000/about"' in home.text
    assert 'const endpoint = "/s/session/p/8000/api/hello"' in home.text
    assert hello.json()["root_path"] == "/s/session/p/8000"
    assert hello.json()["detected_base_path"] == "/s/session/p/8000"


def test_connect_base_path_takes_precedence_over_asgi_root_path(
    demo_app_module: ModuleType,
) -> None:
    with TestClient(demo_app_module.app, root_path="/internal-prefix") as client:
        response = client.get(
            "/api/hello",
            headers={"RStudio-Connect-App-Base-URL": "/content/demo"},
        )

    assert response.json()["root_path"] == "/internal-prefix"
    assert response.json()["detected_base_path"] == "/content/demo"


def test_unsafe_connect_base_path_falls_back_to_asgi_root(
    demo_app_module: ModuleType,
) -> None:
    with TestClient(demo_app_module.app, root_path="/safe-root") as client:
        response = client.get(
            "/api/hello",
            headers={"RStudio-Connect-App-Base-URL": "//outside.example/escape"},
        )

    assert response.status_code == 200
    assert response.json()["detected_base_path"] == "/safe-root"


def test_about_page_returns_to_prefixed_home(client: TestClient) -> None:
    response = client.get(
        "/about",
        headers={"RStudio-Connect-App-Base-URL": "/content/demo"},
    )

    assert response.status_code == 200
    assert 'href="/content/demo/"' in response.text

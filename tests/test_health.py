from httpx import AsyncClient


async def test_health_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_unknown_route_uses_the_error_format(client: AsyncClient) -> None:
    response = await client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_docs_are_exposed(client: AsyncClient) -> None:
    assert (await client.get("/docs")).status_code == 200
    assert (await client.get("/openapi.json")).status_code == 200

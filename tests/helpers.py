from typing import Any

from httpx import AsyncClient

from tests.conftest import REGISTER_PAYLOAD

TEXT = "Yesterday I go to the cinema with my friends."


async def register(client: AsyncClient, email: str = "ana@example.com") -> dict[str, str]:
    """Create an account and return its Authorization header."""
    response = await client.post("/api/v1/auth/register", json={**REGISTER_PAYLOAD, "email": email})
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def analyze(
    client: AsyncClient, headers: dict[str, str], text: str = TEXT, **extra: Any
) -> dict[str, Any]:
    """Analyse a text through the API and return the JSON body (asserting success)."""
    response = await client.post(
        "/api/v1/texts/analyze", headers=headers, json={"text": text, **extra}
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body

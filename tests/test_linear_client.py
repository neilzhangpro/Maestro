import json

import httpx

from maestro.config import LinearConfig
from maestro.linear.client import LinearClient


def test_fetch_issues_serializes_expected_graphql_filter() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        payload = json.loads(request.content.decode("utf-8"))
        captured["payload"] = payload
        return httpx.Response(
            200,
            json={
                "data": {
                    "issues": {
                        "nodes": [
                            {
                                "id": "issue-1",
                                "identifier": "ABC-123",
                                "title": "Test issue",
                                "description": "Hello",
                                "priority": 1,
                                "url": "https://linear.app/test/issue/ABC-123",
                                "team": {"key": "ABC"},
                                "state": {"id": "state-1", "name": "In Progress"},
                                "labels": {"nodes": [{"name": "backend"}]},
                                "relationDependencies": {"nodes": []},
                            }
                        ]
                    }
                }
            },
        )

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport, base_url="https://api.linear.app/graphql")
    config = LinearConfig(
        api_key="linear-secret",
        project_slug="maestro",
        team_id="team-1",
        active_states=["In Progress"],
    )

    client = LinearClient(config, http_client=http_client)
    issues = client.fetch_issues()

    assert len(issues) == 1
    assert issues[0].identifier == "ABC-123"
    assert str(captured["url"]).rstrip("/") == "https://api.linear.app/graphql"
    assert captured["authorization"] == "linear-secret"
    assert captured["payload"] == {
        "query": captured["payload"]["query"],
        "variables": {
            "filter": {
                "project": {"slugId": {"eq": "maestro"}},
                "team": {"id": {"eq": "team-1"}},
                "state": {"name": {"in": ["In Progress"]}},
            }
        },
    }


def test_fetch_issue_by_identifier_uses_team_key_and_number_filter() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        captured["payload"] = payload
        return httpx.Response(
            200,
            json={
                "data": {
                    "issues": {
                        "nodes": [
                            {
                                "id": "issue-1",
                                "identifier": "MAE-1",
                                "title": "Get familiar with Linear",
                                "description": "Hello",
                                "priority": 1,
                                "url": "https://linear.app/test/issue/MAE-1",
                                "team": {"key": "MAE"},
                                "state": {"id": "state-1", "name": "Todo"},
                                "labels": {"nodes": []},
                            }
                        ]
                    }
                }
            },
        )

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport, base_url="https://api.linear.app/graphql")
    client = LinearClient(LinearConfig(api_key="linear-secret"), http_client=http_client)

    issue = client.fetch_issue("MAE-1")

    assert issue.identifier == "MAE-1"
    assert captured["payload"] == {
        "query": captured["payload"]["query"],
        "variables": {"number": 1, "teamKey": "MAE"},
    }

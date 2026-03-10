"""Minimal Linear GraphQL client."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

import httpx

from maestro.config import LinearConfig
from maestro.linear.models import Issue


ISSUE_FIELDS = """
id
identifier
title
description
priority
url
branchName
createdAt
updatedAt
team {
  key
}
state {
  id
  name
}
labels {
  nodes {
    name
  }
}
inverseRelations {
  nodes {
    type
    relatedIssue {
      id
      identifier
      title
      state { name }
    }
  }
}
""".strip()

ISSUE_STATE_FIELDS = """
id
identifier
state {
  id
  name
}
""".strip()


class LinearError(RuntimeError):
    """Raised when a Linear GraphQL call fails."""


@dataclass(slots=True, frozen=True)
class IssueUpdateResult:
    success: bool
    issue_id: str | None = None


class LinearClient:
    """Thin wrapper around the Linear GraphQL API."""

    def __init__(
        self,
        config: LinearConfig,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            base_url=config.api_url,
            timeout=config.timeout_s,
        )
        self._client.headers.update(
            {
                "Authorization": config.api_key,
                "Content-Type": "application/json",
            }
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "LinearClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def fetch_issue(self, issue_ref: str) -> Issue:
        identifier_match = re.fullmatch(r"([A-Z][A-Z0-9_]*)-(\d+)", issue_ref.strip())
        if identifier_match:
            team_key, number = identifier_match.groups()
            query = f"""
            query GetIssueByIdentifier($number: Float!, $teamKey: String!) {{
              issues(
                first: 1,
                filter: {{
                  team: {{ key: {{ eq: $teamKey }} }}
                  number: {{ eq: $number }}
                }}
              ) {{
                nodes {{
                  {ISSUE_FIELDS}
                }}
              }}
            }}
            """
            data = self._graphql(
                query,
                {"number": int(number), "teamKey": team_key},
            )
            nodes = data["issues"]["nodes"]
            if nodes:
                return Issue.from_graphql(nodes[0])

            query = f"""
            query GetIssueByNumber($number: Float!) {{
              issues(first: 1, filter: {{ number: {{ eq: $number }} }}) {{
                nodes {{
                  {ISSUE_FIELDS}
                }}
              }}
            }}
            """
            data = self._graphql(query, {"number": int(number)})
            nodes = data["issues"]["nodes"]
            if nodes:
                return Issue.from_graphql(nodes[0])

        query = f"""
        query GetIssueById($id: String!) {{
          issue(id: $id) {{
            {ISSUE_FIELDS}
          }}
        }}
        """
        data = self._graphql(query, {"id": issue_ref})
        issue = data.get("issue")
        if not issue:
            raise LinearError(f"Could not find Linear issue '{issue_ref}'.")
        return Issue.from_graphql(issue)

    def fetch_issues(
        self,
        project_slug: str | None = None,
        state_names: list[str] | None = None,
    ) -> list[Issue]:
        filters = self._build_issue_filter(
            project_slug=project_slug or self.config.project_slug,
            team_id=self.config.team_id,
            state_names=state_names or self.config.active_states,
        )
        query = f"""
        query ListIssues($filter: IssueFilter) {{
          issues(first: 50, filter: $filter) {{
            nodes {{
              {ISSUE_FIELDS}
            }}
          }}
        }}
        """
        data = self._graphql(query, {"filter": filters})
        return [Issue.from_graphql(node) for node in data["issues"]["nodes"]]

    def update_issue_state(self, issue_id: str, state_id: str) -> IssueUpdateResult:
        mutation = """
        mutation UpdateIssueState($id: String!, $input: IssueUpdateInput!) {
          issueUpdate(id: $id, input: $input) {
            success
            issue {
              id
            }
          }
        }
        """
        data = self._graphql(mutation, {"id": issue_id, "input": {"stateId": state_id}})
        payload = data["issueUpdate"]
        issue = payload.get("issue") or {}
        return IssueUpdateResult(success=payload["success"], issue_id=issue.get("id"))

    def fetch_issues_by_states(self, state_names: list[str]) -> list[Issue]:
        """Fetch issues by state names (for startup terminal cleanup)."""
        if not state_names:
            return []
        filters = self._build_issue_filter(
            project_slug=self.config.project_slug,
            team_id=self.config.team_id,
            state_names=state_names,
        )
        query = f"""
        query ListIssuesByStates($filter: IssueFilter) {{
          issues(first: 50, filter: $filter) {{
            nodes {{
              {ISSUE_FIELDS}
            }}
          }}
        }}
        """
        data = self._graphql(query, {"filter": filters})
        return [Issue.from_graphql(node) for node in data["issues"]["nodes"]]

    def fetch_issue_states_by_ids(self, issue_ids: list[str]) -> list[Issue]:
        """Fetch minimal issue records by IDs (for reconciliation).

        Uses GraphQL ``[ID!]`` typing as required by the Linear API.
        """
        if not issue_ids:
            return []
        query = f"""
        query FetchIssueStates($ids: [ID!]!) {{
          issues(filter: {{ id: {{ in: $ids }} }}) {{
            nodes {{
              {ISSUE_STATE_FIELDS}
            }}
          }}
        }}
        """
        data = self._graphql(query, {"ids": issue_ids})
        nodes = data.get("issues", {}).get("nodes", [])
        results: list[Issue] = []
        for node in nodes:
            state = node.get("state") or {}
            results.append(Issue(
                id=node["id"],
                identifier=node.get("identifier", ""),
                title="",
                description=None,
                state=state.get("name", "Unknown"),
                state_id=state.get("id"),
            ))
        return results

    def find_state_id(self, team_key: str, state_name: str) -> str | None:
        """Look up a workflow state ID by team key and state name."""
        query = """
        query FindState($teamKey: String!) {
          teams(filter: { key: { eq: $teamKey } }) {
            nodes {
              states { nodes { id name } }
            }
          }
        }
        """
        data = self._graphql(query, {"teamKey": team_key})
        teams = data["teams"]["nodes"]
        if not teams:
            return None
        for state in teams[0]["states"]["nodes"]:
            if state["name"].lower() == state_name.lower():
                return state["id"]
        return None

    def create_comment(self, issue_id: str, body: str) -> str | None:
        mutation = """
        mutation CreateComment($input: CommentCreateInput!) {
          commentCreate(input: $input) {
            success
            comment {
              id
            }
          }
        }
        """
        data = self._graphql(mutation, {"input": {"issueId": issue_id, "body": body}})
        payload = data["commentCreate"]
        if not payload["success"]:
            raise LinearError("Linear commentCreate returned success=false.")
        comment = payload.get("comment") or {}
        return comment.get("id")

    @staticmethod
    def _build_issue_filter(
        *,
        project_slug: str | None,
        team_id: str | None,
        state_names: list[str] | None,
    ) -> dict[str, Any]:
        filter_data: dict[str, Any] = {}
        if project_slug:
            filter_data["project"] = {"slugId": {"eq": project_slug}}
        if team_id:
            filter_data["team"] = {"id": {"eq": team_id}}
        if state_names:
            filter_data["state"] = {"name": {"in": state_names}}
        return filter_data

    def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        response = self._client.post("", json={"query": query, "variables": variables})
        response.raise_for_status()
        payload = response.json()
        errors = payload.get("errors") or []
        if errors:
            messages = ", ".join(error.get("message", "Unknown error") for error in errors)
            raise LinearError(messages)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise LinearError("Linear response did not include a data payload.")
        return data

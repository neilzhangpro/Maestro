"""Lightweight HTTP client for the Maestro API."""

from __future__ import annotations

from typing import Any

import httpx


class MaestroAPIClient:
    """Synchronous wrapper around the Maestro REST endpoints."""

    def __init__(self, base_url: str = "http://127.0.0.1:8080") -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, timeout=10)

    def healthy(self) -> bool:
        try:
            return self._http.get("/api/health").status_code == 200
        except (httpx.HTTPError, httpx.ConnectError):
            return False

    def issues(self) -> list[dict[str, Any]]:
        return self._http.get("/api/issues/all").raise_for_status().json()

    def orchestrator(self) -> dict[str, Any]:
        return self._http.get("/api/v1/orchestrator").raise_for_status().json()

    def trigger(self, issue_id: str) -> dict[str, Any]:
        return self._http.post(
            "/api/runs", json={"issue_id": issue_id},
        ).raise_for_status().json()

    def set_state(self, issue_ref: str, state_name: str) -> dict[str, Any]:
        return self._http.patch(
            f"/api/issues/{issue_ref}/state",
            json={"state_name": state_name},
        ).raise_for_status().json()

    def refresh(self) -> dict[str, Any]:
        return self._http.post("/api/v1/refresh").raise_for_status().json()

    def add_comment(self, issue_ref: str, body: str) -> dict[str, Any]:
        return self._http.post(
            f"/api/issues/{issue_ref}/comment",
            json={"body": body},
        ).raise_for_status().json()

    def cancel_worker(self, issue_id: str) -> dict[str, Any]:
        return self._http.delete(
            f"/api/runs/{issue_id}",
        ).raise_for_status().json()

    def mark_pr_ready(self, issue_ref: str) -> dict[str, Any]:
        return self._http.post(
            f"/api/issues/{issue_ref}/mark-pr-ready",
        ).raise_for_status().json()

    def close(self) -> None:
        self._http.close()

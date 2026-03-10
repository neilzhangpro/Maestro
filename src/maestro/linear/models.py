"""Data models used by the Linear client."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True, frozen=True)
class BlockerRef:
    id: str | None
    identifier: str | None
    title: str | None = None
    state: str | None = None


@dataclass(slots=True, frozen=True)
class Issue:
    id: str
    identifier: str
    title: str
    description: str | None
    state: str
    team_key: str | None = None
    state_id: str | None = None
    priority: int | None = None
    labels: list[str] = field(default_factory=list)
    blocked_by: list[BlockerRef] = field(default_factory=list)
    url: str | None = None
    branch_name: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_graphql(cls, node: dict[str, Any]) -> "Issue":
        labels_raw = node.get("labels", {}).get("nodes", [])
        blockers_raw = (
            node.get("inverseRelations", {}).get("nodes")
            or node.get("blockedByIssues", {}).get("nodes")
            or []
        )
        state = node.get("state") or {}
        team = node.get("team") or {}

        blockers: list[BlockerRef] = []
        for rel in blockers_raw:
            if rel.get("type") and rel["type"] != "blocks":
                continue
            related = rel.get("relatedIssue") or rel
            blockers.append(BlockerRef(
                id=related.get("id"),
                identifier=related.get("identifier"),
                title=related.get("title"),
                state=(related.get("state") or {}).get("name"),
            ))

        priority = node.get("priority")
        if priority is not None and not isinstance(priority, int):
            priority = None

        return cls(
            id=node["id"],
            identifier=node["identifier"],
            title=node["title"],
            description=node.get("description"),
            state=state.get("name", "Unknown"),
            team_key=team.get("key"),
            state_id=state.get("id"),
            priority=priority,
            labels=[
                label["name"].lower()
                for label in labels_raw
                if "name" in label
            ],
            blocked_by=blockers,
            url=node.get("url"),
            branch_name=node.get("branchName"),
            created_at=node.get("createdAt"),
            updated_at=node.get("updatedAt"),
        )

    def to_template_dict(self) -> dict[str, Any]:
        """Return a dict suitable for Liquid template rendering."""
        return {
            "id": self.id,
            "identifier": self.identifier,
            "title": self.title,
            "description": self.description or "",
            "state": self.state,
            "priority": self.priority,
            "labels": self.labels,
            "url": self.url or "",
            "branch_name": self.branch_name or "",
            "blocked_by": [
                {
                    "id": b.id or "",
                    "identifier": b.identifier or "",
                    "title": b.title or "",
                    "state": b.state or "",
                }
                for b in self.blocked_by
            ],
        }


def issue_to_prompt(issue: Issue) -> str:
    """Render a Linear issue into a Cursor-ready prompt."""

    parts = [
        f"You are working on Linear issue {issue.identifier}: {issue.title}",
        "",
        f"Current state: {issue.state}",
    ]
    if issue.team_key:
        parts.append(f"Team: {issue.team_key}")
    if issue.labels:
        parts.append(f"Labels: {', '.join(issue.labels)}")
    if issue.url:
        parts.append(f"URL: {issue.url}")
    parts.extend(
        [
            "",
            "Description:",
            issue.description.strip() if issue.description else "(no description provided)",
        ]
    )
    if issue.blocked_by:
        parts.extend(
            [
                "",
                "Blocked by:",
                *[
                    f"- {blocker.identifier}: {blocker.title}"
                    for blocker in issue.blocked_by
                ],
            ]
        )
    return "\n".join(parts)

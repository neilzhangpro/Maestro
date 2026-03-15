"""SkillStore — SKILL.md file management for the evolution system.

Provides a clean abstraction over the ``evolved_skills/`` and
``pending_skills/`` directories inside ``.maestro/``.  All mutation
operations create a ``.bak`` backup before writing so manual rollback is
always possible.

SKILL.md format assumed by this module
---------------------------------------
Each file is a Markdown document with an optional YAML front-matter block
(enclosed in ``---`` fences) followed by the skill body.  The learned
section — auto-generated content appended by the evolution engine — is
delimited by an HTML comment sentinel::

    <!-- LEARNED -->
    ## Lessons Learned (auto-updated YYYY-MM-DD)

    - observation 1
    - observation 2

Everything *above* ``<!-- LEARNED -->`` is the original body and is never
touched by the mutator.  Everything *below* is managed by this module.

Opt-out
-------
A SKILL.md containing ``<!-- NO_AUTO_MUTATE -->`` anywhere in the original
body will be skipped by all mutation operations.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_SENTINEL = "<!-- LEARNED -->"
_NO_MUTATE = "<!-- NO_AUTO_MUTATE -->"
_SKILL_FILENAME = "SKILL.md"


@dataclass(frozen=True)
class SkillMeta:
    """Lightweight descriptor for an existing Skill."""

    name: str
    """Directory name used as the skill identifier."""

    path: Path
    """Absolute path to the SKILL.md file."""

    description: str = ""
    """Value of the ``description`` frontmatter key (empty if not found)."""

    has_learned_section: bool = False
    """Whether the file already contains a ``<!-- LEARNED -->`` block."""

    opt_out: bool = False
    """True when the file contains ``<!-- NO_AUTO_MUTATE -->``."""


@dataclass
class SkillContent:
    """Parsed content of a SKILL.md file."""

    name: str
    original_body: str
    """Everything above ``<!-- LEARNED -->`` (or the full file if no sentinel)."""

    learned_section: str = ""
    """Content between ``<!-- LEARNED -->`` and end-of-file."""

    opt_out: bool = False


class SkillStoreError(RuntimeError):
    """Raised for unrecoverable SkillStore operations."""


class SkillStore:
    """Read/write access to the evolved-Skill directory tree.

    Parameters
    ----------
    skills_dir:
        Root directory that contains one sub-directory per Skill.  When used
        for evolved skills this is ``{workspace_root}/.maestro/evolved_skills/``.
    pending_dir:
        Directory for skills that require human review before being applied.
        Defaults to a ``pending_skills/`` sibling of *skills_dir*.
    """

    def __init__(
        self,
        skills_dir: Path,
        pending_dir: Path | None = None,
    ) -> None:
        self._dir = skills_dir
        self._pending = pending_dir or skills_dir.parent / "pending_skills"

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def list_skills(self) -> list[SkillMeta]:
        """Return metadata for every SKILL.md found under *skills_dir*."""
        if not self._dir.exists():
            return []
        metas: list[SkillMeta] = []
        for skill_dir in sorted(self._dir.iterdir()):
            skill_file = skill_dir / _SKILL_FILENAME
            if not skill_dir.is_dir() or not skill_file.exists():
                continue
            metas.append(self._read_meta(skill_dir.name, skill_file))
        return metas

    def skill_exists(self, name: str) -> bool:
        """Return True if a SKILL.md exists for *name*."""
        return (self._dir / name / _SKILL_FILENAME).exists()

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def read_skill(self, name: str) -> SkillContent:
        """Return parsed content for *name*; raises :class:`SkillStoreError` if missing."""
        path = self._dir / name / _SKILL_FILENAME
        if not path.exists():
            raise SkillStoreError(f"Skill not found: {name!r} (expected {path})")
        return self._parse(name, path.read_text(encoding="utf-8"))

    # ------------------------------------------------------------------
    # Writing / mutation
    # ------------------------------------------------------------------

    def append_learned(self, name: str, addendum: str) -> None:
        """Append *addendum* to the learned section of *name*.

        If ``<!-- LEARNED -->`` is not present yet, it is inserted at the
        end of the file before the addendum.
        """
        content = self.read_skill(name)
        if content.opt_out:
            log.info("Skill %r has NO_AUTO_MUTATE — skipping.", name)
            return

        path = self._dir / name / _SKILL_FILENAME
        self._backup(path)

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        header = f"## Lessons Learned (auto-updated {now})\n\n"

        if content.learned_section:
            new_learned = content.learned_section.rstrip() + "\n\n" + addendum.strip()
        else:
            new_learned = header + addendum.strip()

        new_body = (
            content.original_body.rstrip()
            + f"\n\n{_SENTINEL}\n"
            + new_learned
            + "\n"
        )
        path.write_text(new_body, encoding="utf-8")
        log.info("Appended learned addendum to skill %r.", name)

    def replace_learned(self, name: str, new_learned: str) -> None:
        """Replace the entire learned section of *name* with *new_learned*."""
        content = self.read_skill(name)
        if content.opt_out:
            log.info("Skill %r has NO_AUTO_MUTATE — skipping.", name)
            return

        path = self._dir / name / _SKILL_FILENAME
        self._backup(path)

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        header = f"## Lessons Learned (auto-updated {now})\n\n"
        new_body = (
            content.original_body.rstrip()
            + f"\n\n{_SENTINEL}\n"
            + header
            + new_learned.strip()
            + "\n"
        )
        path.write_text(new_body, encoding="utf-8")
        log.info("Replaced learned section of skill %r.", name)

    def create_skill(self, name: str, content: str, *, pending: bool = False) -> Path:
        """Create a new SKILL.md for *name*.

        Parameters
        ----------
        name:
            Skill directory name (must be a valid directory segment).
        content:
            Full SKILL.md content.
        pending:
            When True the skill is written to *pending_dir* for human review
            instead of *skills_dir*.
        """
        base = self._pending if pending else self._dir
        skill_dir = base / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        path = skill_dir / _SKILL_FILENAME
        if path.exists():
            self._backup(path)
        path.write_text(content, encoding="utf-8")
        log.info("Created skill %r at %s.", name, path)
        return path

    def promote_pending(self, name: str) -> None:
        """Move *name* from *pending_dir* to *skills_dir*."""
        src = self._pending / name
        dst = self._dir / name
        if not src.exists():
            raise SkillStoreError(f"Pending skill not found: {name!r}")
        if dst.exists():
            shutil.rmtree(dst)
        shutil.move(str(src), str(dst))
        log.info("Promoted pending skill %r to evolved.", name)

    def list_pending(self) -> list[SkillMeta]:
        """Return metadata for every SKILL.md in *pending_dir*."""
        if not self._pending.exists():
            return []
        metas: list[SkillMeta] = []
        for skill_dir in sorted(self._pending.iterdir()):
            skill_file = skill_dir / _SKILL_FILENAME
            if not skill_dir.is_dir() or not skill_file.exists():
                continue
            metas.append(self._read_meta(skill_dir.name, skill_file))
        return metas

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse(name: str, text: str) -> SkillContent:
        opt_out = _NO_MUTATE in text
        if _SENTINEL in text:
            idx = text.index(_SENTINEL)
            original = text[:idx]
            learned = text[idx + len(_SENTINEL):].lstrip("\n")
        else:
            original = text
            learned = ""
        return SkillContent(
            name=name,
            original_body=original,
            learned_section=learned,
            opt_out=opt_out,
        )

    @staticmethod
    def _read_meta(name: str, path: Path) -> SkillMeta:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return SkillMeta(name=name, path=path)

        description = ""
        # Parse YAML frontmatter for description key
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end != -1:
                fm = text[3:end]
                for line in fm.splitlines():
                    if line.startswith("description:"):
                        description = line.split(":", 1)[1].strip().strip('"\'')
                        break

        return SkillMeta(
            name=name,
            path=path,
            description=description,
            has_learned_section=_SENTINEL in text,
            opt_out=_NO_MUTATE in text,
        )

    @staticmethod
    def _backup(path: Path) -> None:
        """Write a ``.bak`` copy before any mutation."""
        bak = path.with_suffix(".bak")
        try:
            shutil.copy2(path, bak)
        except OSError:
            log.warning("Could not create backup for %s", path, exc_info=True)

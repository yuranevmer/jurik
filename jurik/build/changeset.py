"""Differences between two renders of the repository as fast-import file changes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass
class ChangeSet:
    modified: dict[str, bytes] = field(default_factory=dict)  # added or changed paths
    deleted: set[str] = field(default_factory=set)

    def __bool__(self) -> bool:
        return bool(self.modified or self.deleted)

    def update(self, other: ChangeSet) -> None:
        """Apply ``other`` on top of this change set (later changes win)."""
        for path in other.deleted:
            self.modified.pop(path, None)
            self.deleted.add(path)
        for path, content in other.modified.items():
            self.deleted.discard(path)
            self.modified[path] = content

    def apply(self, files: Mapping[str, bytes]) -> dict[str, bytes]:
        """``files`` with this change set applied (a new dict)."""
        out = {p: c for p, c in files.items() if p not in self.deleted}
        out.update(self.modified)
        return out


def diff(prev_files: Mapping[str, bytes], new_files: Mapping[str, bytes]) -> ChangeSet:
    """Paths added or changed in ``new_files`` and paths of ``prev_files`` gone from it."""
    modified = {
        path: content for path, content in new_files.items() if prev_files.get(path) != content
    }
    deleted = {path for path in prev_files if path not in new_files}
    return ChangeSet(modified=dict(sorted(modified.items())), deleted=deleted)

"""Base class of the domain errors (``ZakonError``, ``RadaError``, ``BuildError``, …).

Every step raises its own subclass; the CLI turns them into ``click.ClickException`` and
lets anything else (a bug) surface with a traceback.
"""

from __future__ import annotations


class JurikError(Exception):
    """An expected failure of a jurik step: bad input, missing cache, unusable source."""

"""Resolve naive wall-clock times against a time zone, without guessing.

The only operation Python's stdlib gives you is ``naive.replace(tzinfo=tz)``.
That call silently invents an instant for a wall time that does not exist
(spring-forward gap) and silently picks one of two instants for a wall time
that happens twice (fall-back fold).  ``zone_safe`` makes both cases visible
and refuses to choose unless the caller asks for an explicit policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from zoneinfo import ZoneInfo

UTC = timezone.utc
Kind = Literal["unique", "ambiguous", "nonexistent"]
Choose = Literal["strict", "earlier", "later"]


class ZoneSafeError(ValueError):
    """Base class for all zone_safe errors."""


class AmbiguousTimeError(ZoneSafeError):
    """The wall time occurs twice and ``choose='strict'`` was requested."""

    def __init__(self, resolution: "Resolution") -> None:
        super().__init__(
            f"{resolution.local.isoformat()} occurs twice in {resolution.zone}: "
            + ", ".join(c.isoformat() for c in resolution.candidates)
        )
        self.resolution = resolution


class NonexistentTimeError(ZoneSafeError):
    """The wall time falls inside a forward clock jump."""

    def __init__(self, resolution: "Resolution") -> None:
        super().__init__(
            f"{resolution.local.isoformat()} does not exist in {resolution.zone}"
        )
        self.resolution = resolution


@dataclass(frozen=True)
class Resolution:
    """The complete answer for one naive wall-clock time.

    Attributes
    ----------
    local:
        The naive input, unchanged.
    zone:
        The zone key (or string form of a fixed-offset tzinfo).
    kind:
        ``unique``, ``ambiguous`` or ``nonexistent``.
    candidates:
        Every UTC instant whose local representation equals ``local``,
        sorted ascending.  Length 0 for a gap, 1 normally, 2 for a fold.
    projections:
        For gaps only: the wall times the two fold interpretations actually
        map to.  These are *not* valid resolutions of the input; they are
        evidence showing which way the clock jumped.
    """

    local: datetime
    zone: str
    kind: Kind
    candidates: tuple[datetime, ...]
    projections: tuple[datetime, ...] = ()
    # Kept so resolve_local() can render back into the same zone object.
    zone_info: object = field(default=None, repr=False, compare=False)

    def resolve(self, choose: Choose = "strict") -> datetime:
        """Return the single UTC instant, or raise if that is not honest.

        ``choose='earlier'`` / ``'later'`` is the explicit opt-in for folds.
        There is no policy that can make a gap resolvable, so gaps always
        raise.
        """
        if self.kind == "nonexistent":
            raise NonexistentTimeError(self)
        if choose == "strict":
            if self.kind == "ambiguous":
                raise AmbiguousTimeError(self)
            return self.candidates[0]
        if choose == "earlier":
            return self.candidates[0]
        if choose == "later":
            return self.candidates[-1]
        raise ZoneSafeError(f"choose must be strict|earlier|later, got {choose!r}")

    def resolve_local(self, choose: Choose = "strict") -> datetime:
        """Return the chosen instant rendered in the target zone.

        Same refusal rules as :meth:`resolve`: gaps always raise, folds need an
        explicit policy.  Fixed-offset zones are handled with the stored object.
        """
        instant = self.resolve(choose)
        if self.zone_info is None:
            return instant
        return instant.astimezone(self.zone_info)  # type: ignore[arg-type]

    @property
    def is_unique(self) -> bool:
        return self.kind == "unique"


def _as_zone(tz: str | ZoneInfo | timezone) -> ZoneInfo | timezone:
    if isinstance(tz, str):
        try:
            return ZoneInfo(tz)
        except Exception as exc:  # ZoneInfoNotFoundError and friends
            raise ZoneSafeError(f"unknown time zone {tz!r}") from exc
    if not hasattr(tz, "utcoffset"):
        raise ZoneSafeError(f"not a time zone: {tz!r}")
    return tz


def classify(local: datetime, tz: str | ZoneInfo | timezone) -> Resolution:
    """Classify a naive wall-clock time in ``tz``.

    The method is deliberately mechanical: ask the zone for the UTC instant
    under both ``fold`` values, convert each back, and keep only the ones that
    round-trip to the same wall time.  No transition tables or DST rules are
    hard-coded, so historical and half-hour transitions work the same way.
    """
    if not isinstance(local, datetime):
        raise ZoneSafeError("local must be a datetime")
    if local.tzinfo is not None:
        raise ZoneSafeError("local must be naive (tzinfo is None)")

    zone = _as_zone(tz)
    candidates: set[datetime] = set()
    projections: set[datetime] = set()

    for fold in (0, 1):
        aware = local.replace(tzinfo=zone, fold=fold)
        utc = aware.astimezone(UTC)
        back = utc.astimezone(zone)
        if back.replace(tzinfo=None) == local:
            candidates.add(utc)
        else:
            projections.add(back.replace(tzinfo=None))

    ordered = tuple(sorted(candidates))
    if len(ordered) == 0:
        kind: Kind = "nonexistent"
    elif len(ordered) == 1:
        kind = "unique"
    else:
        kind = "ambiguous"

    return Resolution(
        local=local,
        zone=str(zone),
        kind=kind,
        candidates=ordered,
        projections=tuple(sorted(projections)) if kind == "nonexistent" else (),
        zone_info=zone,
    )

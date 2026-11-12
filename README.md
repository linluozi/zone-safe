# zone-safe

`zone_safe` answers one question honestly: **given a naive wall-clock time and an IANA zone, which UTC instant(s) does it denote?** Python's `datetime.replace(tzinfo=tz)` silently invents an instant for spring-forward gaps and silently picks a side for fall-back folds. `zone_safe` classifies the input as `unique`, `ambiguous`, or `nonexistent`, returns every real candidate, and refuses to choose a fold unless you pass an explicit policy.

Upstream: none

## Why this exists

Scheduling code fails twice a year, usually in the gap:

```python
from datetime import datetime
from zoneinfo import ZoneInfo

dt = datetime(2026, 3, 8, 2, 30).replace(tzinfo=ZoneInfo("America/New_York"))
print(dt, dt.astimezone(ZoneInfo("UTC")))
# 2026-03-08 02:30:00-05:00 2026-03-08 07:30:00+00:00
```

That instant renders back as **03:30**, not 02:30 — the requested wall time never existed. `zone_safe` returns `kind="nonexistent"` and the two projections (`01:30`, `03:30`) as evidence of the jump. A fold is likewise reported as two candidates instead of one arbitrary answer.

## Install

```bash
python3 -m pip install .
```

Requires Python 3.9+ and the system/IANA tz database via `zoneinfo` (stdlib). No third-party dependencies.

## Use

```python
from datetime import datetime
from zone_safe import AmbiguousTimeError, classify

r = classify(datetime(2026, 11, 1, 1, 30), "America/New_York")
print(r.kind)                  # ambiguous
print(r.candidates)            # (05:30Z, 06:30Z) — both are real

try:
    r.resolve()                # strict by default
except AmbiguousTimeError as exc:
    print(len(exc.resolution.candidates))  # 2

print(r.resolve("earlier"))    # 05:30Z
print(r.resolve("later"))      # 06:30Z

print(r.resolve_local("later"))  # 2026-11-01 01:30:00-05:00 (fold=1)

gap = classify(datetime(2026, 3, 8, 2, 30), "America/New_York")
print(gap.kind, gap.candidates, gap.projections)
# nonexistent () (2026-03-08 01:30:00, 2026-03-08 03:30:00)
```

### CLI

```bash
python3 -m zone_safe 2026-11-01T01:30 America/New_York
python3 -m zone_safe 2026-11-01T01:30 America/New_York --choose later
python3 -m zone_safe 2026-03-08T02:30 America/New_York
```

Exit codes: `0` resolved/unique, `2` bad input or zone, `3` ambiguous under strict, `4` nonexistent (always, regardless of policy). JSON goes to stdout; the error line goes to stderr.

## How classification works

For both `fold=0` and `fold=1` it asks the zone for a UTC instant, converts that instant back, and keeps it only if the wall time round-trips exactly. Zero survivors = gap; one = unique; two distinct instants = fold. No DST transition table is hard-coded, so half-hour transitions (`Australia/Lord_Howe`), skipped calendar days (`Pacific/Apia`, 2011-12-30), and historical offsets behave the same.

## Evidence (actually run)

```bash
python3 -m unittest discover -s tests -v
```

Result on 2026-11-12, CPython 3.12.3, host IANA tzdata **2025b**: **21 tests, OK, 1.86 s**.

The suite does not assert against hand-picked expected UTC strings. Its oracle enumerates every UTC minute in a ±2-day window and keeps the instants that render back to the requested wall time; that is exhaustive because all UTC offsets lie within ±14 h. Comparisons are exact set equality.

- 12 named transition cases (New York, Berlin, Lord Howe, Apia's skipped day, Kolkata, London, UTC): **12/12** match the brute-force oracle and the expected class.
- 96 seeded random wall times across ten zones (2010–2027), including Dublin's negative DST, Casablanca's Ramadan shifts, Troll's 2-hour jump, Chatham's +12:45 offset and St John's -3:30: **96/96** match the oracle. Seed `20261112`.
- Gap projections for New York 2026-03-08 02:30 are exactly `01:30` and `03:30`; gap/fold policy tests cover strict/earlier/later, including the rule that **no policy can resolve a gap**.
- 5 CLI tests cover exit codes 0/2/3/4 and the `--choose later` instant.

## Limits, stated up front

- Release times only in the sense that `datetime` has no leap-second table; zone transitions come from the installed tz database, so results change if tzdata is updated.
- Input must be naive. Aware datetimes are rejected rather than reinterpreted.
- `resolve("earlier"|"later")` is a policy choice, not a correctness claim; the two candidates are both valid.
- `resolve_local()` returns an aware datetime in the target zone. Subtracting two such datetimes that share the same `tzinfo` compares wall time, ignoring `fold` (CPython rule); compare `x.astimezone(timezone.utc) - y.astimezone(timezone.utc)` when you need real elapsed time.
- No recurring-event expansion: this classifies a single wall time.

## License

MIT. See `LICENSE`.

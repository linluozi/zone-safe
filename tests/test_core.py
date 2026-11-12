from __future__ import annotations

import json
import random
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from zone_safe import (
    AmbiguousTimeError,
    NonexistentTimeError,
    ZoneSafeError,
    classify,
)

UTC = timezone.utc


def oracle(local: datetime, zone_name: str) -> list[datetime]:
    """Independent brute force: scan UTC minutes around the wall time.

    This shares no code with ``classify``: it only asks the zone database
    which UTC instants render back to ``local``.  Offsets are within +/-14h,
    so a +/-2 day scan is exhaustive.
    """
    tz = ZoneInfo(zone_name)
    base = local.replace(tzinfo=UTC)
    hit = set()
    for minutes in range(-2880, 2881):
        instant = base + timedelta(minutes=minutes)
        if instant.astimezone(tz).replace(tzinfo=None) == local:
            hit.add(instant)
    return sorted(hit)


CASES = [
    ("America/New_York", "2026-03-08T02:30", "nonexistent"),
    ("America/New_York", "2026-03-08T01:30", "unique"),
    ("America/New_York", "2026-11-01T01:30", "ambiguous"),
    ("America/New_York", "2026-11-01T03:30", "unique"),
    ("Europe/Berlin", "2026-03-29T02:30", "nonexistent"),
    ("Europe/Berlin", "2026-10-25T02:30", "ambiguous"),
    ("Australia/Lord_Howe", "2026-10-04T02:15", "nonexistent"),
    ("Australia/Lord_Howe", "2026-04-05T01:45", "ambiguous"),
    ("Pacific/Apia", "2011-12-30T12:00", "nonexistent"),
    ("Asia/Kolkata", "2026-06-01T12:00", "unique"),
    ("UTC", "2026-01-01T00:00", "unique"),
    ("Europe/London", "2026-06-15T12:00", "unique"),
]


class KnownCases(unittest.TestCase):
    def test_every_case_matches_oracle_and_label(self) -> None:
        self.assertEqual(len(CASES), 12)
        for zone, text, expected in CASES:
            local = datetime.fromisoformat(text)
            res = classify(local, zone)
            expected_instants = oracle(local, zone)
            self.assertEqual(
                list(res.candidates),
                expected_instants,
                msg=f"oracle mismatch for {zone} {text}",
            )
            self.assertEqual(res.kind, expected, msg=f"{zone} {text}")
            self.assertEqual(len(res.candidates), {"unique": 1, "ambiguous": 2, "nonexistent": 0}[expected])

    def test_gap_projections_show_the_jump(self) -> None:
        res = classify(datetime(2026, 3, 8, 2, 30), "America/New_York")
        self.assertEqual(res.kind, "nonexistent")
        self.assertEqual(res.candidates, ())
        # 02:30 standard projects to 03:30 daylight; 02:30 daylight projects
        # to 01:30 standard.  Both are evidence of the gap, not answers.
        self.assertEqual(
            [_naive(p) for p in res.projections],
            [datetime(2026, 3, 8, 1, 30), datetime(2026, 3, 8, 3, 30)],
        )

    def test_half_hour_transition_projections(self) -> None:
        res = classify(datetime(2026, 10, 4, 2, 15), "Australia/Lord_Howe")
        self.assertEqual(res.kind, "nonexistent")
        self.assertEqual(len(res.projections), 2)


class OracleSweep(unittest.TestCase):
    def test_random_wall_times_agree_with_bruteforce(self) -> None:
        rng = random.Random(20261112)
        zones = [
            "America/New_York", "Europe/Berlin", "Australia/Lord_Howe", "Asia/Kolkata",
            "Europe/Dublin", "Africa/Casablanca", "Antarctica/Troll",
            "Pacific/Chatham", "Asia/Kathmandu", "America/St_Johns",
        ]
        checked = 0
        for _ in range(96):
            zone = rng.choice(zones)
            start = datetime(2010, 1, 1)
            local = start + timedelta(minutes=rng.randrange(0, 17 * 365 * 24 * 60))
            res = classify(local, zone)
            self.assertEqual(list(res.candidates), oracle(local, zone), msg=f"{zone} {local}")
            self.assertIn(res.kind, ("unique", "ambiguous", "nonexistent"))
            checked += 1
        self.assertEqual(checked, 96)


class Policies(unittest.TestCase):
    def test_strict_refuses_fold(self) -> None:
        res = classify(datetime(2026, 11, 1, 1, 30), "America/New_York")
        with self.assertRaises(AmbiguousTimeError) as ctx:
            res.resolve("strict")
        self.assertEqual(len(ctx.exception.resolution.candidates), 2)

    def test_earlier_later_are_explicit_and_ordered(self) -> None:
        res = classify(datetime(2026, 11, 1, 1, 30), "America/New_York")
        early = res.resolve("earlier")
        late = res.resolve("later")
        self.assertLess(early, late)
        self.assertEqual((late - early), timedelta(hours=1))
        self.assertEqual(early.astimezone(ZoneInfo("America/New_York")).hour, 1)

    def test_gap_cannot_be_resolved_by_policy(self) -> None:
        res = classify(datetime(2026, 3, 8, 2, 30), "America/New_York")
        for choose in ("strict", "earlier", "later"):
            with self.assertRaises(NonexistentTimeError):
                res.resolve(choose)  # type: ignore[arg-type]

    def test_resolve_local_renders_in_target_zone(self) -> None:
        res = classify(datetime(2026, 11, 1, 1, 30), "America/New_York")
        early = res.resolve_local("earlier")
        late = res.resolve_local("later")
        self.assertEqual(early.replace(tzinfo=None), datetime(2026, 11, 1, 1, 30))
        self.assertGreater(early.utcoffset(), late.utcoffset())  # earlier is -04:00, later -05:00
        # Same-tzinfo subtraction is wall-clock (fold is ignored), so compare
        # the instants explicitly.  This is the trap the library exists for.
        self.assertEqual(late - early, timedelta(0))
        self.assertEqual(late.astimezone(UTC) - early.astimezone(UTC), timedelta(hours=1))

    def test_resolve_local_refuses_gap_and_strict_fold(self) -> None:
        gap = classify(datetime(2026, 3, 8, 2, 30), "America/New_York")
        with self.assertRaises(NonexistentTimeError):
            gap.resolve_local("earlier")
        fold = classify(datetime(2026, 11, 1, 1, 30), "America/New_York")
        with self.assertRaises(AmbiguousTimeError):
            fold.resolve_local()
        unique = classify(datetime(2026, 6, 1, 12, 0), "Asia/Kolkata")
        self.assertEqual(unique.resolve_local().utcoffset(), timedelta(hours=5, minutes=30))

    def test_resolve_local_keeps_fixed_offset(self) -> None:
        res = classify(datetime(2026, 1, 1, 0, 0), timezone(timedelta(hours=5, minutes=30)))
        local = res.resolve_local()
        self.assertEqual(local.utcoffset(), timedelta(hours=5, minutes=30))
        self.assertEqual(local.replace(tzinfo=None), datetime(2026, 1, 1, 0, 0))

    def test_unique_resolves_under_all_policies(self) -> None:
        res = classify(datetime(2026, 6, 1, 12, 0), "Asia/Kolkata")
        self.assertTrue(res.is_unique)
        for choose in ("strict", "earlier", "later"):
            self.assertEqual(res.resolve(choose), res.candidates[0])  # type: ignore[arg-type]

    def test_invalid_choose(self) -> None:
        res = classify(datetime(2026, 6, 1, 12, 0), "Asia/Kolkata")
        with self.assertRaises(ZoneSafeError):
            res.resolve("middle")  # type: ignore[arg-type]

    def test_fixed_offset_and_utc(self) -> None:
        res = classify(datetime(2026, 1, 1, 0, 0), timezone(timedelta(hours=5, minutes=30)))
        self.assertEqual(res.kind, "unique")
        self.assertEqual(res.candidates[0].isoformat(), "2025-12-31T18:30:00+00:00")

    def test_rejects_aware_input(self) -> None:
        with self.assertRaises(ZoneSafeError):
            classify(datetime(2026, 1, 1, tzinfo=UTC), "UTC")

    def test_unknown_zone(self) -> None:
        with self.assertRaises(ZoneSafeError):
            classify(datetime(2026, 1, 1), "Mars/Olympus_Mons")

    def test_rejects_non_datetime(self) -> None:
        with self.assertRaises(ZoneSafeError):
            classify("2026-01-01", "UTC")  # type: ignore[arg-type]


class CLI(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "zone_safe", *args],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_unique_exit_zero(self) -> None:
        proc = self.run_cli("2026-06-01T12:00", "Asia/Kolkata")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["kind"], "unique")
        self.assertIn("chosen_utc", payload)

    def test_ambiguous_strict_exit_three(self) -> None:
        proc = self.run_cli("2026-11-01T01:30", "America/New_York")
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(json.loads(proc.stdout)["kind"], "ambiguous")

    def test_ambiguous_later_exit_zero(self) -> None:
        proc = self.run_cli("2026-11-01T01:30", "America/New_York", "--choose", "later")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            json.loads(proc.stdout)["chosen_utc"], "2026-11-01T06:30:00+00:00"
        )

    def test_gap_exit_four(self) -> None:
        proc = self.run_cli("2026-03-08T02:30", "America/New_York")
        self.assertEqual(proc.returncode, 4)
        self.assertEqual(json.loads(proc.stdout)["kind"], "nonexistent")

    def test_bad_input_exit_two(self) -> None:
        self.assertEqual(self.run_cli("not-a-date", "UTC").returncode, 2)
        self.assertEqual(self.run_cli("2026-01-01T00:00", "No/Such_Zone").returncode, 2)


def _naive(dt: datetime) -> datetime:
    return dt.replace(tzinfo=None)


if __name__ == "__main__":
    unittest.main()

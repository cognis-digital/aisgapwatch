"""Turn a raw time/space gap into a suspiciousness score in ``[0, 1]``.

The score blends three independent signals, each saturating so no single
dimension can dominate:

* **duration**   — longer silences are more suspicious (a transponder off for
  six hours is worse than one off for ten minutes).
* **implied speed** — the speed needed to cover ``distance_nm`` during the gap.
  A reappearance that implies 60+ knots is physically impossible for a merchant
  vessel and is the strongest single tell of AIS spoofing / a dark rendezvous.
* **distance**   — a large positional jump, even at a plausible speed, still
  warrants attention.

The weights are deliberately simple and inspectable; tune them via
:class:`ScoreConfig` rather than editing the math.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ScoreConfig", "score_gap"]


@dataclass(frozen=True)
class ScoreConfig:
    """Saturation points + blend weights for :func:`score_gap`."""

    duration_full_h: float = 6.0        # a gap this long maxes the duration signal
    speed_impossible_kn: float = 40.0   # implied speed at/above this maxes the speed signal
    distance_full_nm: float = 50.0      # a jump this far maxes the distance signal
    w_duration: float = 0.4
    w_speed: float = 0.4
    w_distance: float = 0.2

    def __post_init__(self) -> None:
        total = self.w_duration + self.w_speed + self.w_distance
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"weights must sum to 1.0, got {total}")
        for name in ("duration_full_h", "speed_impossible_kn", "distance_full_nm"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")


def _sat(value: float, full: float) -> float:
    """Linear ramp from 0 at value=0 to 1 at value>=full."""
    if value <= 0:
        return 0.0
    return min(1.0, value / full)


def score_gap(duration_s: float, distance_nm: float, implied_speed_kn: float,
              config: ScoreConfig | None = None) -> tuple[float, tuple[str, ...]]:
    """Return ``(score, reasons)`` for one gap.

    ``score`` is the weighted blend of the three saturated signals; ``reasons``
    are short tags for the signals that contributed materially (>= 0.5 saturated).
    """
    cfg = config or ScoreConfig()
    d = _sat(duration_s / 3600.0, cfg.duration_full_h)
    s = _sat(implied_speed_kn, cfg.speed_impossible_kn)
    x = _sat(distance_nm, cfg.distance_full_nm)
    score = cfg.w_duration * d + cfg.w_speed * s + cfg.w_distance * x

    reasons: list[str] = []
    if d >= 0.5:
        reasons.append(f"long-duration-{duration_s / 3600.0:.1f}h")
    if s >= 0.5:
        reasons.append(f"implied-speed-{implied_speed_kn:.0f}kn")
    if x >= 0.5:
        reasons.append(f"position-jump-{distance_nm:.0f}nm")
    return round(min(1.0, score), 4), tuple(reasons)

# Explained simply

No jargon. If you've written a little Python and know what a CSV is, you can
follow this. (The precise version lives in [`01_ARCHITECTURE.md`](01_ARCHITECTURE.md).)

## What problem are we solving?

Ships broadcast their position over a radio system called **AIS** — think of it
as a car constantly tweeting "here's where I am." Most of the time that's normal.
But sometimes a ship **goes quiet** for hours and then pops back up far away. That
silence can be innocent (bad radio reception) — or it can mean someone turned the
transponder off on purpose to do something they'd rather you didn't see (meet
another ship in secret, dodge sanctions, fake a location).

The trouble: a real feed has *millions* of these silences, and almost all are
boring. We need a way to **rank** them so a human only looks at the few that are
genuinely weird. That's all `aisgapwatch` does: find the silences, and give each
one a suspicion score from 0 to 1.

## The pieces, in order

Think of it like an assembly line. Each station does one small job.

1. **Reading the data** (`parsers.py`, `data.py`). We start with lines of text
   like `2026-06-01T00:00:00Z,366123456,37.80,-122.40` — a time, a ship ID, and a
   latitude/longitude. We turn each line into a tidy `Ping` object and double-check
   the numbers make sense (no latitude of 200, that's impossible).

   > In plain terms: we clean up the raw data so the rest of the program can trust it.

2. **Sorting by ship** (`detect.py`). Many ships' pings are mixed together, maybe
   out of order. We sort them into one timeline per ship.

   > In plain terms: we put each ship's breadcrumbs back in the right order.

3. **Finding the silences** (`detect.py`). Walking each ship's timeline, whenever
   two breadcrumbs are more than (say) 30 minutes apart, that's a **gap** worth
   examining.

4. **Measuring the jump** (`geo.py`). How far apart were the two breadcrumbs around
   the silence? We use the *haversine* formula — a standard way to measure distance
   on a globe — and answer in nautical miles.

   > In plain terms: "how far did the ship move while we weren't looking?"

5. **Scoring the suspicion** (`scoring.py`). We combine three clues:
   - **How long** was it quiet? (6 hours is worse than 6 minutes.)
   - **How fast would it have had to go** to reappear there? If the math says
     60 knots, that's impossible for a cargo ship — a huge red flag.
   - **How far** did it jump?

   Each clue is capped so no single one runs away with the score, then they're
   mixed into one number between 0 and 1.

   > In plain terms: a long silence + an impossible speed + a big jump = "look here."

6. **Showing the answer** (`cli.py`). We list the gaps worst-first, as a table or
   as JSON, and the program "raises its hand" (a non-zero exit code) when it found
   something — handy for automatic alerts.

## A worked example

The sample file has a ship that's quiet for ~6 hours and reappears 164 nautical
miles away. To have done that legitimately it would've needed to average ~27
knots — fast and sustained, on top of a long blackout. It scores **0.87**. A
second ship just drifts slowly the whole time and scores **0.06**. Exactly the
triage we wanted: one thing to investigate, one to ignore.

## Want to change how strict it is?

You don't touch the math — you adjust the dials in `ScoreConfig`:

```python
from aisgapwatch import detect_gaps, ScoreConfig
strict = ScoreConfig(duration_full_h=3.0, speed_impossible_kn=30.0)
gaps = detect_gaps(pings, config=strict)
```

Lower numbers = more sensitive (more things flagged). That's the whole idea: the
tool is transparent and tunable, not a mysterious black box.

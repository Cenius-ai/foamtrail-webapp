# Using FoamTrail

A walkthrough of a real dial-in session. Commands are shown as
`python3 foamtrail.py …` so they work with no install; if `install.sh` put the
console script on your PATH, `foamtrail …` is the same thing.

## 1. Register the bag

Freshness is the first thing that decides whether a shot can taste good, so a
bag is always registered with its roast date (printed on the bag, often as
"roasted on"):

```console
$ python3 foamtrail.py beans add "House Blend" --roast 2026-11-08
Added House Blend — roasted 2026-11-08, 9 days off roast, rested.
Log a shot against it: foamtrail log --grind 12 --dose 18 --yield 36 --time 28 --bean "House Blend"
```

A roast date in the future, or one that does not parse, is refused and nothing
is written:

```console
$ python3 foamtrail.py beans add "Mystery" --roast 2024-13-40
error: roast date must look like YYYY-MM-DD (got '2024-13-40').
hint: for example: --roast 2025-06-28
```

## 2. See every bag and how stale it is

```console
$ python3 foamtrail.py beans
BEAN                 ROASTED     OFF ROAST  BAND    SHOTS
─────────────────────────────────────────────────────────
Huehuetenango Decaf  2026-11-15        2 d  fresh       0
House Blend          2026-11-08        9 d  rested      0
El Paraiso Geisha    2026-10-02       46 d  stale       0

fresh ≤ 7 days · rested 8–30 days · stale past 30 days off roast
```

The band is a text label, not just a colour, so it survives pipes and
`NO_COLOR`. With no beans registered you get an invitation instead of an empty
table:

```console
$ python3 foamtrail.py beans
No beans registered yet.
Add the bag you are pulling from:
  foamtrail beans add "House Blend" --roast 2026-11-03
```

## 3. Log a shot

Sour and thin — the classic under-extracted first shot of a new bag:

```console
$ python3 foamtrail.py log --grind 10 --dose 18 --yield 30 --time 26 --notes "sour, thin — grind finer"
Logged shot shot_7acc2841 against House Blend (9 days off roast, rested).
  grind   10 clicks
  dose    18 g
  yield   30 g
  time    26 s
  notes   sour, thin — grind finer
Next: `foamtrail chart` plots grind against yield for all 1 logged shot.
```

No `--bean` means "the bag I added most recently", which is nearly always the
one you are working through. Name it explicitly when you are comparing two bags:

```console
$ python3 foamtrail.py log --bean "El Paraiso Geisha" --grind 15 --dose 18 --yield 40 --time 31
Logged shot shot_53a0a9e5 against El Paraiso Geisha (46 days off roast, stale).
  grind   15 clicks
  dose    18 g
  yield   40 g
  time    31 s
note: El Paraiso Geisha is 46 days off roast — past the 30-day freshness window (stale). Expect flatter, woody shots; a fresher bag will dial in differently.
Next: `foamtrail chart` plots grind against yield for all 5 logged shots.
```

Anything out of range is rejected, with the offending field named, and the shot
is not logged:

```console
$ python3 foamtrail.py log --grind 0 --dose 18 --yield 36 --time 28
error: grind size must be between 1 and 60 clicks (got 0).
$ python3 foamtrail.py log --grind 12 --dose 18 --yield "a lot" --time 28
error: yield must be a number of grams between 5 and 150 (got 'a lot').
```

## 4. Compare the last few pulls

```console
$ python3 foamtrail.py shots
BEAN                 GRIND  DOSE  YIELD  TIME  ROAST  LOGGED    NOTES
──────────────────────────────────────────────────────────────────────
El Paraiso Geisha       15  18 g   40 g  31 s   46 d  just now  flat, woody — bag is old
Huehuetenango Decaf     11  17 g   32 g  27 s    2 d  just now  cocoa, low acid
House Blend             13  18 g   36 g  29 s    9 d  just now  balanced, caramel finish
House Blend             12  18 g   34 g  28 s    9 d  just now  sweeter, still bright
House Blend             10  18 g   30 g  26 s    9 d  just now  sour, thin — grind finer

grind in clicks · dose/yield in grams · roast = days off roast when the shot was pulled · newest first
```

Newest first, so the top row is the shot you are reacting to. `ROAST` records
the days off roast *at the time of the shot*, which does not drift as the bag
ages. With nothing logged yet:

```console
$ python3 foamtrail.py shots
No shots logged yet.
Log your first shot:
  foamtrail log --grind 12 --dose 18 --yield 36 --time 28
```

## 5. Read the chart

```console
$ python3 foamtrail.py chart
Grind size versus yield — 5 shots
15 │                                       ●
   │
   │
   │                       ●
   │
12 │
   │                ●
   │
   │        ●
   │
   │
10 │●
   └────────────────────────────────────────
    30                 35                 40
── vertical: grind clicks (10–15, higher = coarser)  ·  horizontal: yield grams (30–40)
● = one shot
```

- Vertical axis: grind clicks — higher on the page means a coarser setting.
- Horizontal axis: yield in grams — further right means more liquid in the cup.
- The axis captions always state the **observed** minimum and maximum of the
  shots you have actually logged, so the window is always meaningful.
- Two shots landing in the same cell are drawn as `✶` and named in the legend.

Reading it in the example above: coarser grind went with a higher yield, which
is the normal direction. A shot low and to the right (fine grind, big yield)
means the puck is channelling — you want to hear that from the data before you
taste it.

Nothing logged yet:

```console
$ python3 foamtrail.py chart
No shots to chart yet.
Log your first shot:
  foamtrail log --grind 12 --dose 18 --yield 36 --time 28
```

## 6. Scripting

Every list command speaks JSON:

```console
$ python3 foamtrail.py chart --json
{
  "shots": 2,
  "grind_clicks": {"min": 10, "max": 14},
  "yield_g": {"min": 30.0, "max": 38.0},
  "points": [
    {"bean": "House Blend", "grind_clicks": 14, "yield_g": 38.0},
    {"bean": "House Blend", "grind_clicks": 10, "yield_g": 30.0}
  ]
}
```

Results go to stdout, errors to stderr and the exit code is `0` only on
success, so `foamtrail shots --json | jq` behaves the way a shell expects.

## 7. Keeping several logs

```bash
FOAMTRAIL_DATA_DIR=~/logs/decaf  python3 foamtrail.py shots
FOAMTRAIL_DATA_DIR=~/logs/decaf  python3 foamtrail.py log --grind 11 --dose 17 --yield 32 --time 27
```

Each folder gets its own `state.json`. Copy the file to back the log up; it is
plain JSON.

# Installing FoamTrail

FoamTrail is a single-file Python command line tool with no third-party runtime
dependencies. There is no server to start and nothing to keep running.

## Prerequisites

| Requirement | Notes |
| --- | --- |
| Python 3.9 or newer | `python3 --version` must print 3.9, 3.10, 3.11, … |
| pip | Ships with Python; needed only for `install.sh` and the test suite |
| A terminal | Any POSIX shell; Unicode box-drawing characters are used |

Nothing else is installed at system level and no service is started.

## Step 1 — install the project dependencies

```bash
bash install.sh
```

It upgrades `pip`/`setuptools`/`wheel`, installs the `foamtrail` console script
in editable mode (`pip install -e .`), installs the test runner pinned in
`requirements.txt`, runs an import self-check and a `--version` smoke test, then
exits. It is safe to run repeatedly.

If you prefer a completely dependency-free approach, skip this step entirely:
`python3 foamtrail.py --help` works from a plain copy of the repository.

## Step 2 — verify the install

```bash
python3 foamtrail.py --version      # FoamTrail 1.0.0
foamtrail --version                 # the same via the console script (if on PATH)
python3 foamtrail.py --help         # usage, commands and examples
```

## Step 3 — see it work

```bash
bash demo.sh
```

The demo registers three bags, logs five shots, prints the bean freshness list,
the shot history and the chart, shows two rejected inputs, and dumps JSON. It
uses a throwaway folder (`mktemp -d`) so your own log is untouched.

## Step 4 — use it for real

```bash
python3 foamtrail.py beans add "House Blend" --roast 2026-11-08
python3 foamtrail.py log --grind 12 --dose 18 --yield 36 --time 28 --notes "sweet"
python3 foamtrail.py shots
python3 foamtrail.py chart
```

State is written to `~/.foamtrail/state.json` (created on first use). Set
`FOAMTRAIL_DATA_DIR` to keep the log somewhere else.

## Step 5 — run the tests

```bash
python3 -m pytest
```

The suite runs offline against temporary state folders and takes a few seconds.

## Uninstalling

Delete `~/.foamtrail/` (and the project folder). If `install.sh` installed the
console script, also run `python3 -m pip uninstall foamtrail`.

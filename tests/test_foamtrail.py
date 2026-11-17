"""FoamTrail test suite — one meaningful test per core feature.

Covers: bean registration + roast-date validation + freshness bands (F3),
shot logging with confirmation, ranges and the stale warning (F1), the shot
history table and its order (F4), the grind-versus-yield text chart (F2), the
state file contract (D2) and the plain-language error paths.
"""

import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

import foamtrail

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "foamtrail.py"
MARKER = foamtrail.MARKER
AXIS_BAR = "\u2502"
LOG_EXAMPLE = "foamtrail log --grind 12 --dose 18 --yield 36 --time 28"


def days_ago(days: int) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


class Cli:
    """Runs the real console entry point as a subprocess against a temp state dir."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir

    def __call__(self, *args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["FOAMTRAIL_DATA_DIR"] = str(self.data_dir)
        env["NO_COLOR"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(ROOT),
            timeout=60,
            check=False,
        )

    def state(self) -> dict:
        """The stored log; a rejected command legitimately leaves no file behind."""
        path = self.data_dir / "state.json"
        if not path.exists():
            return {"version": foamtrail.STATE_VERSION, "beans": [], "shots": []}
        return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture()
def cli(tmp_path: Path) -> Cli:
    return Cli(tmp_path / "state")


def ok(result: subprocess.CompletedProcess) -> subprocess.CompletedProcess:
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "Traceback" not in result.stderr
    return result


def failed(result: subprocess.CompletedProcess) -> subprocess.CompletedProcess:
    assert result.returncode == 1, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "Traceback" not in result.stderr
    return result


# --------------------------------------------------------------------------
# T1 — CLI skeleton, state file, dispatch
# --------------------------------------------------------------------------
def test_help_lists_every_command(cli: Cli) -> None:
    result = ok(cli("--help"))
    for command in ("beans", "log", "shots", "chart"):
        assert command in result.stdout


def test_bare_invocation_prints_help_and_succeeds(cli: Cli) -> None:
    result = ok(cli())
    assert "usage: foamtrail" in result.stdout
    assert "chart" in result.stdout


def test_version_flag(cli: Cli) -> None:
    result = ok(cli("--version"))
    assert "FoamTrail" in result.stdout
    assert foamtrail.__version__ in result.stdout


@pytest.mark.parametrize("args", [("beans",), ("shots",), ("chart",)])
def test_every_command_creates_the_state_file(cli: Cli, args: tuple) -> None:
    result = ok(cli(*args))
    assert result.stdout.strip()
    assert (cli.data_dir / "state.json").exists()
    assert cli.state()["beans"] == []
    assert cli.state()["shots"] == []


# --------------------------------------------------------------------------
# T3 — beans add / list, roast-date validation, freshness (F3)
# --------------------------------------------------------------------------
def test_beans_add_then_list_shows_roast_date_days_and_band(cli: Cli) -> None:
    roast = days_ago(5)
    added = ok(cli("beans", "add", "House Blend", "--roast", roast))
    assert "House Blend" in added.stdout
    assert "5 days off roast" in added.stdout
    assert "fresh" in added.stdout

    listing = ok(cli("beans")).stdout
    assert "House Blend" in listing
    assert roast in listing
    assert "5 d" in listing
    assert "fresh" in listing


def test_beans_list_bands_the_bag_by_days_off_roast(cli: Cli) -> None:
    ok(cli("beans", "add", "House Blend", "--roast", days_ago(5)))
    ok(cli("beans", "add", "El Paraiso Geisha", "--roast", days_ago(45)))
    listing = ok(cli("beans")).stdout
    lines = {line.split()[0]: line for line in listing.splitlines() if line.startswith(("House", "El"))}
    assert " 5 d" in lines["House"]
    assert "fresh" in lines["House"]
    assert " 45 d" in lines["El"]
    assert "stale" in lines["El"]


@pytest.mark.parametrize(
    "roast",
    ["2024-13-40", "not-a-date", "", (date.today() + timedelta(days=3)).isoformat()],
)
def test_beans_add_rejects_bad_roast_dates_and_saves_nothing(cli: Cli, roast: str) -> None:
    result = failed(cli("beans", "add", "Mystery", "--roast", roast))
    assert "roast date" in result.stderr
    assert cli.state()["beans"] == []
    assert "No beans registered yet." in ok(cli("beans")).stdout


def test_beans_empty_state_names_the_add_command(cli: Cli) -> None:
    result = ok(cli("beans"))
    assert "No beans registered yet." in result.stdout
    assert "foamtrail beans add" in result.stdout


def test_beans_json_output(cli: Cli) -> None:
    ok(cli("beans", "add", "House Blend", "--roast", days_ago(5)))
    payload = json.loads(ok(cli("beans", "--json")).stdout)
    assert payload[0]["name"] == "House Blend"
    assert payload[0]["days_off_roast"] == 5
    assert payload[0]["freshness_band"] == "fresh"


# --------------------------------------------------------------------------
# T2 — log a shot: confirmation, ranges, stale warning, default bean (F1)
# --------------------------------------------------------------------------
def test_log_confirms_the_recorded_values_and_lists_the_shot(cli: Cli) -> None:
    ok(cli("beans", "add", "House Blend", "--roast", days_ago(9)))
    logged = ok(cli("log", "--grind", "12", "--dose", "18", "--yield", "36", "--time", "28")).stdout
    assert "House Blend" in logged
    assert "grind   12 clicks" in logged
    assert "dose    18 g" in logged
    assert "yield   36 g" in logged
    assert "time    28 s" in logged
    assert "9 days off roast" in logged

    table = ok(cli("shots")).stdout
    assert "House Blend" in table
    assert "12" in table and "36 g" in table and "28 s" in table

    stored = cli.state()["shots"][0]
    assert stored["grind_clicks"] == 12
    assert stored["dose_g"] == 18.0
    assert stored["yield_g"] == 36.0
    assert stored["brew_seconds"] == 28.0
    assert stored["days_off_roast_at_log"] == 9


def test_log_records_optional_notes(cli: Cli) -> None:
    ok(cli("beans", "add", "House Blend", "--roast", days_ago(9)))
    ok(cli("log", "--grind", "12", "--dose", "18", "--yield", "36", "--time", "28", "--notes", "sweet, balanced"))
    assert "sweet, balanced" in ok(cli("shots")).stdout
    assert cli.state()["shots"][0]["notes"] == "sweet, balanced"


@pytest.mark.parametrize(
    "flags, field",
    [
        (("--grind", "0"), "grind size"),
        (("--grind", "99"), "grind size"),
        (("--grind", "abc"), "grind size"),
        (("--grind", "12.5"), "grind size"),
        (("--dose", "0"), "dose"),
        (("--dose", "heavy"), "dose"),
        (("--yield", "999"), "yield"),
        (("--time", "-3"), "brew time"),
    ],
)
def test_log_rejects_out_of_range_and_non_numeric_values(cli: Cli, flags: tuple, field: str) -> None:
    ok(cli("beans", "add", "House Blend", "--roast", days_ago(9)))
    base = {"--grind": "12", "--dose": "18", "--yield": "36", "--time": "28"}
    base[flags[0]] = flags[1]
    args = ["log"] + [part for key, value in base.items() for part in (key, value)]
    result = failed(cli(*args))
    assert field in result.stderr
    assert cli.state()["shots"] == []


def test_log_warns_when_the_bean_is_stale(cli: Cli) -> None:
    ok(cli("beans", "add", "Old Bag", "--roast", days_ago(45)))
    logged = ok(cli("log", "--grind", "12", "--dose", "18", "--yield", "36", "--time", "28")).stdout
    assert "45 days off roast" in logged
    assert "stale" in logged
    assert "freshness window" in logged


def test_log_defaults_to_the_most_recently_added_bean(cli: Cli) -> None:
    ok(cli("beans", "add", "A", "--roast", days_ago(20)))
    ok(cli("beans", "add", "B", "--roast", days_ago(20)))
    logged = ok(cli("log", "--grind", "12", "--dose", "18", "--yield", "36", "--time", "28")).stdout
    assert "against B" in logged
    bean_id = cli.state()["shots"][0]["bean_id"]
    bean_b = next(bean for bean in cli.state()["beans"] if bean["name"] == "B")
    assert bean_id == bean_b["id"]


def test_log_accepts_an_explicit_bean(cli: Cli) -> None:
    ok(cli("beans", "add", "A", "--roast", days_ago(20)))
    ok(cli("beans", "add", "B", "--roast", days_ago(20)))
    logged = ok(cli("log", "--bean", "A", "--grind", "12", "--dose", "18", "--yield", "36", "--time", "28")).stdout
    assert "against A" in logged


def test_log_rejects_an_unknown_bean_name(cli: Cli) -> None:
    ok(cli("beans", "add", "House Blend", "--roast", days_ago(9)))
    result = failed(cli("log", "--bean", "Nope", "--grind", "12", "--dose", "18", "--yield", "36", "--time", "28"))
    assert "no bean named 'Nope'" in result.stderr
    assert "House Blend" in result.stderr
    assert cli.state()["shots"] == []


def test_log_without_any_bean_explains_the_fix(cli: Cli) -> None:
    result = failed(cli("log", "--grind", "12", "--dose", "18", "--yield", "36", "--time", "28"))
    assert "no beans registered yet" in result.stderr
    assert "foamtrail beans add" in result.stderr


def test_logging_twice_appends_two_shots(cli: Cli) -> None:
    ok(cli("beans", "add", "House Blend", "--roast", days_ago(9)))
    ok(cli("log", "--grind", "12", "--dose", "18", "--yield", "36", "--time", "28"))
    ok(cli("log", "--grind", "13", "--dose", "18", "--yield", "38", "--time", "29"))
    assert len(cli.state()["shots"]) == 2


# --------------------------------------------------------------------------
# F4 — local shot history
# --------------------------------------------------------------------------
def test_shots_empty_state_names_the_exact_log_command(cli: Cli) -> None:
    result = ok(cli("shots"))
    assert "No shots logged yet." in result.stdout
    assert LOG_EXAMPLE in result.stdout


def test_shots_lists_newest_first(cli: Cli) -> None:
    ok(cli("beans", "add", "House Blend", "--roast", days_ago(9)))
    for grind, shot_yield in ((10, 30), (12, 34), (14, 38)):
        ok(cli("log", "--grind", str(grind), "--dose", "18", "--yield", str(shot_yield), "--time", "28"))
    payload = json.loads(ok(cli("shots", "--json")).stdout)
    assert [shot["grind_clicks"] for shot in payload] == [14, 12, 10]
    assert payload[0]["bean"] == "House Blend"

    table = ok(cli("shots")).stdout
    first_data_row = next(line for line in table.splitlines() if line.startswith("House"))
    assert "14" in first_data_row


# --------------------------------------------------------------------------
# F2 — grind versus yield chart
# --------------------------------------------------------------------------
def test_chart_plots_one_marker_per_shot_with_observed_axes(cli: Cli) -> None:
    ok(cli("beans", "add", "House Blend", "--roast", days_ago(9)))
    for grind, shot_yield in ((10, 30), (12, 34), (14, 38)):
        ok(cli("log", "--grind", str(grind), "--dose", "18", "--yield", str(shot_yield), "--time", "28"))

    chart = ok(cli("chart")).stdout
    plot_rows = [line for line in chart.splitlines() if AXIS_BAR in line]
    marker_rows = [index for index, line in enumerate(plot_rows) if MARKER in line]
    assert len(marker_rows) == 3, chart
    # grind 14 is drawn above grind 10
    assert marker_rows[0] < marker_rows[1] < marker_rows[2]
    # both observed extremes are labelled on the axes
    assert "10\u201314" in chart
    assert "30\u201338" in chart
    assert "grind clicks" in chart
    assert "yield grams" in chart


def test_chart_horizontal_axis_grows_left_to_right(cli: Cli) -> None:
    ok(cli("beans", "add", "House Blend", "--roast", days_ago(9)))
    ok(cli("log", "--grind", "12", "--dose", "18", "--yield", "20", "--time", "28"))
    ok(cli("log", "--grind", "12", "--dose", "18", "--yield", "60", "--time", "28"))
    chart = ok(cli("chart")).stdout
    plot_rows = [line for line in chart.splitlines() if AXIS_BAR in line]
    marker_columns: list[int] = []
    for line in plot_rows:
        start = line.find(MARKER)
        while start != -1:
            marker_columns.append(start)
            start = line.find(MARKER, start + 1)
    assert len(marker_columns) == 2, chart
    assert marker_columns[0] < marker_columns[1]


def test_chart_empty_state_invites_the_first_log(cli: Cli) -> None:
    result = ok(cli("chart"))
    assert "No shots to chart yet." in result.stdout
    assert LOG_EXAMPLE in result.stdout


def test_chart_json_exposes_the_observed_range(cli: Cli) -> None:
    ok(cli("beans", "add", "House Blend", "--roast", days_ago(9)))
    for grind, shot_yield in ((10, 30), (14, 38)):
        ok(cli("log", "--grind", str(grind), "--dose", "18", "--yield", str(shot_yield), "--time", "28"))
    payload = json.loads(ok(cli("chart", "--json")).stdout)
    assert payload["shots"] == 2
    assert payload["grind_clicks"] == {"min": 10, "max": 14}
    assert payload["yield_g"] == {"min": 30.0, "max": 38.0}


# --------------------------------------------------------------------------
# D4 — freshness is computed on read
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "days, band",
    [(0, "fresh"), (7, "fresh"), (8, "rested"), (30, "rested"), (31, "stale"), (120, "stale")],
)
def test_freshness_bands(days: int, band: str) -> None:
    assert foamtrail.freshness_band(days) == band


def test_days_off_roast_counts_calendar_days() -> None:
    roast = date(2026, 5, 1)
    assert foamtrail.days_off_roast(roast, date(2026, 5, 1)) == 0
    assert foamtrail.days_off_roast(roast, date(2026, 5, 31)) == 30
    assert foamtrail.days_off_roast(roast, date(2026, 6, 1)) == 31


# --------------------------------------------------------------------------
# D2 — the state file contract
# --------------------------------------------------------------------------
def test_state_file_is_human_readable_json(cli: Cli) -> None:
    ok(cli("beans", "add", "House Blend", "--roast", days_ago(9)))
    ok(cli("log", "--grind", "12", "--dose", "18", "--yield", "36", "--time", "28"))
    raw = (cli.data_dir / "state.json").read_text(encoding="utf-8")
    assert raw.startswith("{")
    assert '"beans"' in raw and '"shots"' in raw
    assert cli.state()["version"] == foamtrail.STATE_VERSION


def test_state_roundtrip_through_the_module(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "state.json"
    state = foamtrail.State(
        beans=[foamtrail.Bean("bean_1", "House Blend", date(2026, 5, 1), datetime(2026, 5, 3, 8, 30))],
        shots=[
            foamtrail.Shot(
                "shot_1", "bean_1", 12, 18.0, 36.0, 28.0, "sweet", datetime(2026, 5, 3, 8, 31), 2
            )
        ],
    )
    foamtrail.save_state(state, path)
    reloaded = foamtrail.load_state(path)
    assert reloaded == state


def test_corrupt_state_file_gives_a_plain_language_error(cli: Cli) -> None:
    cli.data_dir.mkdir(parents=True, exist_ok=True)
    (cli.data_dir / "state.json").write_text("{not json", encoding="utf-8")
    result = failed(cli("shots"))
    assert "not valid JSON" in result.stderr
    assert "state file" in result.stderr
    assert "Traceback" not in result.stderr


def test_state_file_with_a_broken_record_is_reported(cli: Cli) -> None:
    cli.data_dir.mkdir(parents=True, exist_ok=True)
    (cli.data_dir / "state.json").write_text(
        json.dumps({"beans": [{"id": "bean_1", "name": "House Blend"}], "shots": []}),
        encoding="utf-8",
    )
    result = failed(cli("beans"))
    assert "roast_date" in result.stderr
    assert "Traceback" not in result.stderr


# --------------------------------------------------------------------------
# Input-validation helpers
# --------------------------------------------------------------------------
def test_parse_roast_date_rejects_future_dates() -> None:
    with pytest.raises(foamtrail.FoamTrailError) as excinfo:
        foamtrail.parse_roast_date("2026-06-01", today=date(2026, 5, 1))
    assert "roast date" in excinfo.value.message
    assert "future" in excinfo.value.message


def test_parse_int_field_names_the_field() -> None:
    with pytest.raises(foamtrail.FoamTrailError) as excinfo:
        foamtrail.parse_int_field("nope", "grind size", 1, 60, "clicks")
    assert "grind size" in excinfo.value.message
    assert "clicks" in excinfo.value.message


def test_render_table_aligns_numeric_columns() -> None:
    table = foamtrail.render_table(
        ["bean", "grind"], [["House Blend", "12"], ["A", "9"]], ["left", "right"]
    )
    lines = table.splitlines()
    assert lines[0].startswith("BEAN")
    assert lines[1].count("\u2500") == len(lines[0])
    assert lines[2].endswith("12")
    assert lines[3].endswith(" 9")


def test_fmt_num_trims_trailing_zeros() -> None:
    assert foamtrail.fmt_num(18.0) == "18"
    assert foamtrail.fmt_num(18.5) == "18.5"
    assert foamtrail.fmt_num(0.25) == "0.2"


def test_display_width_ignores_ansi_colour() -> None:
    painted = foamtrail.Color(True).paint("rested", foamtrail.THEME["rested"])
    assert "\x1b[" in painted
    assert foamtrail.display_width(painted) == foamtrail.display_width("rested") == 6


def test_colour_keeps_table_columns_aligned() -> None:
    colored = foamtrail.Color(True)
    headers = ["bean", "band"]
    aligns = ["left", "left"]
    painted = foamtrail.render_table(
        headers, [["House Blend", colored.paint("rested", foamtrail.THEME["rested"])]], aligns
    )
    plain = foamtrail.render_table(headers, [["House Blend", "rested"]], aligns)
    assert foamtrail.ANSI_PATTERN.sub("", painted) == plain

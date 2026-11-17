#!/usr/bin/env bash
# FoamTrail setup — installs the project-level dependencies and the
# `foamtrail` console script. It never starts anything and never exits
# non-zero on success.
#
# Runs unprivileged: only pip is used, no system packages, no services.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"

echo "==> FoamTrail setup (Python: $("$PYTHON" -V 2>&1))"

echo "==> Upgrading pip, setuptools and wheel"
"$PYTHON" -m pip install --upgrade pip setuptools wheel

echo "==> Installing the foamtrail console script (editable)"
"$PYTHON" -m pip install -e .

echo "==> Installing the test runner (pytest)"
"$PYTHON" -m pip install -r requirements.txt

echo "==> Self-check: importing the tool"
"$PYTHON" -c "import foamtrail; print('   ', foamtrail.APP_NAME, foamtrail.__version__, 'ready')"

echo "==> Self-check: running the CLI"
"$PYTHON" foamtrail.py --version

cat <<'EOF'

Setup complete — FoamTrail is a command line tool, so there is nothing to
leave running. Next steps:

  python3 foamtrail.py --help      # usage and examples
  bash demo.sh                     # non-interactive end-to-end demo
  python3 -m pytest                # test suite

If `foamtrail` is not on your PATH, either add the Python scripts folder
(`python3 -m site --user-base`/bin) or just call `python3 foamtrail.py`.
EOF

#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "${script_dir}/.." && pwd)"

if [[ -n "${PYTHON_BIN:-}" ]]; then
  python_bin="${PYTHON_BIN}"
elif [[ -x "${project_root}/.venv/bin/python" ]]; then
  python_bin="${project_root}/.venv/bin/python"
else
  python_bin="python3"
fi

cd "${project_root}"
"${python_bin}" -m compileall -q app scripts tests
"${python_bin}" -m pytest -q
node --check app/static/site.js

for shell_script in scripts/*.sh; do
  bash -n "${shell_script}"
done

git diff --check
git diff --cached --check

printf 'All project checks passed.\n'

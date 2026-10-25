"""Run the course pytest metrics command in the Hermes test environment.

The pytest session is intentionally single-process: pytest-html and JUnit XML
must be produced by one pytest controller to retain the exact report formats
expected by the assignment.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


CODIGO_FONTE = "pm"
TESTES = "tests/pm"
SUFIXO = {"before": "antes", "after": "depois"}
ROOT = Path(__file__).resolve().parent


def _test_python() -> str:
    return os.environ.get("__HERMES_TEST_PYTHON") or sys.executable


def _clean_environment(scratch: Path) -> dict[str, str]:
    """Match run_tests.sh's hermetic environment without changing the shell."""
    allowed = {
        "PATH", "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "LOCALAPPDATA",
        "APPDATA", "SYSTEMROOT", "PROGRAMFILES", "ProgramFiles", "PROGRAMDATA",
        "ProgramData", "SSL_CERT_FILE", "SSL_CERT_DIR", "CI", "GITHUB_ACTIONS",
        "HERMES_RUN_SLOW_PET_TESTS", "HERMES_E2E_BROWSER", "HERMES_RUN_E2E",
    }
    env = {key: value for key, value in os.environ.items() if key in allowed}
    env.update({
        # Deliberately omit HERMES_HOME: tests/conftest.py creates its own
        # registered sandbox, as it does under scripts/run_tests.sh.
        "TMPDIR": str(scratch),
        "TEMP": str(scratch),
        "TMP": str(scratch),
        "COVERAGE_FILE": str(scratch / ".coverage"),
        # Python 3.14 makes coverage choose sysmon by default. Its path
        # inspection can trip tests/home_io_guard.py while formatting expected
        # exceptions from cron tests; ctrace preserves coverage without that
        # filesystem probe.
        "COVERAGE_CORE": "ctrace",
        "TZ": "UTC",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONHASHSEED": "0",
        "PYTHONUTF8": "1",
    })
    return env


def main() -> int:
    parser = argparse.ArgumentParser(description="Extrai métricas de testes com o pytest.")
    parser.add_argument(
        "--etapa",
        choices=("before", "after"),
        required=True,
        help="before = antes da refatoração; after = depois da refatoração",
    )
    parser.add_argument(
        "--testes",
        default=TESTES,
        nargs="+",
        help=f"arquivo ou pasta de testes (padrão: {TESTES})",
    )
    parser.add_argument(
        "--saida",
        help="pasta de saída; por padrão, metrics-before/after-pytest",
    )
    args = parser.parse_args()

    source = ROOT / CODIGO_FONTE
    test_paths = [ROOT / path for path in (args.testes if isinstance(args.testes, list) else [args.testes])]
    if not source.is_dir():
        parser.error(f"a pasta de código-fonte '{CODIGO_FONTE}' não existe")
    missing = [str(path) for path in test_paths if not path.exists()]
    if missing:
        parser.error(f"caminho(s) de teste não encontrado(s): {', '.join(missing)}")

    output = ROOT / (args.saida or f"metrics-{args.etapa}-pytest")
    suffix = SUFIXO[args.etapa]
    if output.exists():
        parser.error(f"a pasta {output.name}/ já existe; os resultados não serão sobrescritos")
    output.mkdir()

    scratch_root = Path(tempfile.mkdtemp(prefix="hermes-metrics-", dir="/var/tmp" if Path("/var/tmp").is_dir() else None))
    scratch = scratch_root / "scratch"
    scratch.mkdir()

    def report(name: str) -> str:
        return str(output / name.format(s=suffix))

    print(f"Etapa         : {args.etapa}")
    print(f"Código-fonte  : {CODIGO_FONTE}")
    print(f"Testes        : {' '.join(args.testes if isinstance(args.testes, list) else [args.testes])}")
    print(f"Relatórios em : {output.name}/\n")

    command = [
        _test_python(),
        "-m",
        "pytest",
        *(args.testes if isinstance(args.testes, list) else [args.testes]),
        "-v",
        "-p",
        "no:cacheprovider",
        f"--junit-xml={report('pytest_{s}.xml')}",
        f"--html={report('pytest_{s}.html')}",
        "--self-contained-html",
        f"--cov={CODIGO_FONTE}",
        "--cov-branch",
        f"--cov-report=xml:{report('coverage_{s}.xml')}",
        f"--cov-report=json:{report('coverage_{s}.json')}",
        f"--cov-report=html:{report('coverage_{s}_html')}",
        "--cov-report=term",
    ]

    try:
        result = subprocess.run(command, cwd=ROOT, env=_clean_environment(scratch))
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)

    if result.returncode == 0:
        print(f"\nArquivos gerados em {output.name}/")
        print("Concluído.")
    elif result.returncode == 1:
        print(
            f"\n[aviso] o pytest encontrou falhas de teste; os relatórios foram gerados em {output.name}/."
        )
    else:
        print(f"\n[erro] o pytest terminou com código {result.returncode}; os relatórios podem estar incompletos.")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())

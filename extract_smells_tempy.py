"""Detect test smells with TEMPY, optionally restricted to a test directory."""

from __future__ import annotations

import argparse
import contextlib
import csv
from collections import Counter
import functools
import html
import io
import json
import os
import subprocess
import sys
from datetime import datetime


TEMPY_DIR = "../TEMPY"
SCRIPT_NAME = os.path.basename(__file__)
IGNORED_DIRS = {"venv", "env", "node_modules", "build", "dist", "site-packages", "__pycache__"}


def load_tempy(tempy_dir):
    assets = os.path.join(os.path.abspath(tempy_dir), "assets")
    if not os.path.isfile(os.path.join(assets, "python_parser.py")):
        raise SystemExit(f"[erro] TEMPY não encontrado em: {os.path.abspath(tempy_dir)}")
    sys.path.insert(0, assets)
    import python_parser

    python_parser.open = functools.partial(open, encoding="utf-8", errors="ignore")
    return python_parser.PythonParser


def is_test_file(path):
    with open(path, encoding="utf-8", errors="ignore") as file:
        return any(
            line.find("assert") == 0
            or any(token in line for token in ("import unittest", "import pytest", "from unittest", "from pytest"))
            for line in file
        )


def find_test_files(root):
    files = []
    for directory, names, filenames in os.walk(root):
        names[:] = sorted(
            name for name in names
            if not name.startswith(".") and not name.startswith("metrics-") and name not in IGNORED_DIRS
        )
        for name in sorted(filenames):
            path = os.path.join(directory, name)
            if name.endswith(".py") and name != SCRIPT_NAME and is_test_file(path):
                files.append(path)
    return files


def analyze(parser_class, path):
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            parser = parser_class(path)
            occurrences = parser.start() if parser.ast_parser else parser.start2()
        return occurrences, None
    except Exception as error:  # TEMPY não consegue analisar alguns arquivos modernos.
        return [], f"{type(error).__name__}: {error}"


def run(parser_class, root, files):
    rows, errors, seen = [], [], set()
    for path in files:
        relative = os.path.relpath(path, root).replace(os.sep, "/")
        occurrences, error = analyze(parser_class, path)
        if error:
            errors.append({"arquivo": relative, "erro": error})
        for occurrence in occurrences:
            lines = ";".join(str(line) for line in occurrence.lines)
            row = {
                "arquivo": relative,
                "smell": occurrence.test_smell_type,
                "metodo": occurrence.method_name or "(nível do módulo)",
                "linhas": lines,
            }
            key = tuple(row.values())
            if key not in seen:
                seen.add(key)
                rows.append(row)
    return rows, errors


def git_commit(path):
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True)
        return result.stdout.strip() or None
    except OSError:
        return None


def print_summary(rows, file_count, errors):
    print(f"\n{len(rows)} test smells encontrados em {file_count} arquivos de teste analisados.\n")
    for smell, count in Counter(row["smell"] for row in rows).most_common():
        print(f"  {count:5d}  {smell}")
    if errors:
        print(f"\n[aviso] {len(errors)} arquivo(s) não puderam ser analisados pelo TEMPY.")


def write_html(path, summary, rows):
    by_type = "".join(f"<tr><td>{html.escape(smell)}</td><td>{count}</td></tr>" for smell, count in summary["por_tipo"].items())
    body = "".join(
        f"<tr><td>{index}</td><td>{html.escape(row['arquivo'])}</td><td>{html.escape(row['smell'])}</td>"
        f"<td>{html.escape(row['metodo'])}</td><td>{html.escape(row['linhas'].replace(';', ', '))}</td></tr>"
        for index, row in enumerate(rows, 1)
    )
    page = f"""<!DOCTYPE html><html lang="pt-br"><head><meta charset="utf-8">
<title>Test smells ({summary['etapa']})</title><style>
body {{ font-family: system-ui, sans-serif; margin: 2rem; }}
table {{ border-collapse: collapse; margin-bottom: 2rem; }}
th, td {{ border: 1px solid #999; padding: 4px 8px; text-align: left; }}
th {{ background: #eee; }}</style></head><body>
<h1>Test smells detectados pelo TEMPY ({summary['etapa']})</h1>
<p>Data: {summary['data']}<br>Commit do projeto: {summary['commit_projeto']}<br>
Arquivos de teste analisados: {summary['arquivos_analisados']}<br>
Total de test smells: <strong>{summary['total_smells']}</strong></p>
<h2>Por tipo</h2><table><tr><th>Test smell</th><th>Ocorrências</th></tr>{by_type}</table>
<h2>Ocorrências</h2><table><tr><th>#</th><th>Arquivo</th><th>Test smell</th><th>Método</th><th>Linhas</th></tr>{body}</table>
</body></html>"""
    with open(path, "w", encoding="utf-8") as file:
        file.write(page)


def main():
    parser = argparse.ArgumentParser(description="Detecta test smells com o TEMPY.")
    parser.add_argument("--etapa", choices=("before", "after"), help="salva os resultados oficiais")
    parser.add_argument("--arquivo", help="analisa um único arquivo sem salvar")
    parser.add_argument("--diretorio", help="analisa apenas este diretório sem salvar, por exemplo tests/pm")
    parser.add_argument("--tempy", default=TEMPY_DIR, help="pasta onde o TEMPY foi clonado")
    args = parser.parse_args()
    if args.etapa and (args.arquivo or args.diretorio):
        parser.error("--arquivo/--diretorio são opções de conferência e não podem ser usados com --etapa")

    root = os.getcwd()
    parser_class = load_tempy(args.tempy)
    if args.arquivo:
        files = [args.arquivo]
    else:
        target_dir = args.diretorio if args.diretorio else "tests/pm"
        scan_root = os.path.abspath(target_dir) if os.path.isdir(target_dir) else root
        if not os.path.isdir(scan_root):
            parser.error(f"diretório não encontrado: {scan_root}")
        files = find_test_files(scan_root)
    if not files:
        raise SystemExit("[erro] nenhum arquivo de teste encontrado")

    rows, errors = run(parser_class, root, files)
    print_summary(rows, len(files), errors)
    if not args.etapa:
        print("\n(modo conferência: nenhum arquivo foi salvo)")
        return

    output = f"metrics-{args.etapa}-tempy"
    if os.path.exists(output):
        raise SystemExit(f"[erro] a pasta {output}/ já existe; os resultados não serão sobrescritos")
    os.makedirs(output)
    summary = {
        "etapa": args.etapa,
        "data": datetime.now().isoformat(timespec="seconds"),
        "commit_projeto": git_commit(root),
        "commit_tempy": git_commit(os.path.abspath(args.tempy)),
        "arquivos_analisados": len(files),
        "arquivos_com_erro": len(errors),
        "total_smells": len(rows),
        "por_tipo": dict(Counter(row["smell"] for row in rows).most_common()),
        "por_arquivo": dict(Counter(row["arquivo"] for row in rows).most_common()),
        "erros": errors,
    }
    with open(os.path.join(output, "smells.csv"), "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=["arquivo", "smell", "metodo", "linhas"])
        writer.writeheader()
        writer.writerows(rows)
    with open(os.path.join(output, "resumo.json"), "w", encoding="utf-8") as file:
        json.dump(summary, file, ensure_ascii=False, indent=2)
    write_html(os.path.join(output, "relatorio.html"), summary, rows)
    print(f"\nResultados salvos em {output}/ (smells.csv, resumo.json, relatorio.html)")


if __name__ == "__main__":
    main()

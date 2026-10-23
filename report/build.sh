#!/usr/bin/env bash
# Build the PBL3 report (XeLaTeX, two passes for TOC/references).
set -euo pipefail
cd "$(dirname "$0")"
latexmk -xelatex -interaction=nonstopmode -halt-on-error report.tex

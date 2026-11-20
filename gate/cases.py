"""Frozen P4 data and artifact helpers used by trusted CI."""
import csv
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import runpy
import subprocess
from types import SimpleNamespace
from xml.etree import ElementTree
import zipfile

HERE = Path(__file__).resolve().parent
FACTS = HERE / "facts"
BENCH = FACTS / "tests/p4_content_benchmark"
RULES = {
    "evidence": ("fact_support", "reasoning"),
    "reader": ("answers_task", "depth", "alternatives", "actionability",
               "naturalness", "conciseness", "reader_value"),
    "visual": ("chart_argument", "layout"),
}
AB_CRITERIA = ("answers_task", "depth", "actionability", "naturalness", "conciseness", "reader_value")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def check(code, status, location, finding, action):
    return dict(id=code, status=status, location=location, finding=finding, next_step=action)


def extract(path):
    if path.suffix == ".pdf":
        raw = subprocess.check_output(["pdftotext", "-layout", str(path), "-"], timeout=40, encoding="utf-8")
        return raw.rstrip("\f").split("\f")
    if path.suffix == ".docx":
        with zipfile.ZipFile(path) as file:
            if sum(entry.file_size for entry in file.infolist()) > 45_000_000 or file.testzip():
                raise ValueError("invalid/oversized DOCX ZIP")
            tree = ElementTree.fromstring(file.read("word/document.xml"))
        return ["\n".join("".join(p.itertext()) for p in tree.findall(".//{*}p"))]
    return [path.read_text(encoding="utf-8")]


class Benchmark:
    def __init__(self):
        files = read_json(HERE / "facts-pins.json")
        if {name: digest(FACTS / name) for name in files} != files:
            raise ValueError("trusted fact archive changed")
        self.rules = runpy.run_path(str(BENCH / "test_content_benchmark.py"))
        reader = self.rules["read_rows"]
        self.market = reader(BENCH / "author/market.csv", "evidence_id")
        self.full = self.market | reader(BENCH / "author/market-full-only.csv", "evidence_id")
        self.production = reader(FACTS / "data/samples/nbs_nev_production_2026Q3.csv", "metric_id")


def source_rows(bench, task):
    if task in {"D", "T1"}:
        return bench.production
    return bench.market if task == "C" else bench.full


def decomposition(bench, window, values, location, method):
    suffix, end = (":ytd", "-01/09") if window == "ytd" else ("", "-09")
    try:
        bench.rules["test_a_legal_wholesale_decomposition"](bench.full, suffix, end, tuple(values))
    except (AssertionError, ValueError):
        return check("decomposition", "FAIL", location, "报告读数与冻结批发分解不符。", "复算同窗厂商出口和余项。")
    return check("decomposition", "PASS", location, f"已复算 {values} 万辆。", "保留证据定位。")


old = SimpleNamespace(read_json=read_json, digest=digest, extract=extract, check=check,
                      decomposition=decomposition, compact=lambda text: "".join(text.split()))


def read_packet(folder, case, profile):
    lines = extract(folder / "candidate.md")[0].splitlines()
    pages = extract(folder / "report.pdf")
    if not 1 <= len(pages) <= 10 or len(lines) > 1600 or sum(map(len, pages)) > 65000:
        raise ValueError("PDF or report exceeds project review limits")
    for index, _ in enumerate(pages, 1):
        subprocess.run(["pdftoppm", "-f", str(index), "-l", str(index), "-r", "96", "-png", "-singlefile",
                        str(folder / "report.pdf"), str(folder / f"page-{index}")],
                       check=True, timeout=25, capture_output=True)
    return {
        "case": case, "question": profile["question"], "reader": profile["reader"], "cutoff": profile["cutoff"],
        "report_lines": [{"line": i, "text": row} for i, row in enumerate(lines, 1)],
        "pdf_pages": [{"page": i, "text": text, "image": f"page-{i}.png", "image_sha256": digest(folder / f"page-{i}.png")}
                      for i, text in enumerate(pages, 1)],
        "evidence": profile["evidence"], "tool_receipts": profile.get("tool_receipts", []),
    }

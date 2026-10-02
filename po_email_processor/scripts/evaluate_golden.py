"""Evaluate extraction accuracy against the golden dataset.

    python scripts/evaluate_golden.py                 # parser from DOCUMENT_PARSER
    python scripts/evaluate_golden.py --parser docling
    python scripts/evaluate_golden.py --min-accuracy 0.98 --show-mismatches
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.evaluation.golden import evaluate, load_expected  # noqa: E402
from app.extraction.factory import get_extractor, get_parser  # noqa: E402
from app.models.schemas import DocumentType  # noqa: E402


def main() -> int:
    settings = get_settings()
    ap = argparse.ArgumentParser()
    ap.add_argument("--parser", default=settings.document_parser, choices=["docling", "pdfplumber"])
    ap.add_argument("--expected-dir", type=Path, default=ROOT / "sample_data" / "expected")
    ap.add_argument("--min-accuracy", type=float, default=0.0)
    ap.add_argument("--show-mismatches", action="store_true")
    args = ap.parse_args()

    parser = get_parser(args.parser, do_ocr=settings.docling_do_ocr)
    extractor = get_extractor(DocumentType.PURCHASE_ORDER, date_order=settings.date_order)

    total = correct = 0
    print(f"{'document':<22} {'fields':>6} {'correct':>8} {'accuracy':>9}")
    for path in sorted(args.expected_dir.glob("*.json")):
        expected = load_expected(path)
        parsed, _ = parser.parse(ROOT / expected["source_pdf"])
        result = extractor.extract(parsed)
        report = evaluate(path.stem, expected["purchase_order"], result.purchase_order)
        total += report.total
        correct += report.correct
        print(f"{path.stem:<22} {report.total:>6} {report.correct:>8} {report.accuracy:>8.1%}")
        if args.show_mismatches:
            for m in report.mismatches:
                print(f"    x {m.field}: expected={m.expected!r} actual={m.actual!r}")
    overall = correct / total if total else 1.0
    print(f"{'OVERALL':<22} {total:>6} {correct:>8} {overall:>8.1%}   (parser={args.parser})")
    return 0 if overall >= args.min_accuracy else 1


if __name__ == "__main__":
    sys.exit(main())

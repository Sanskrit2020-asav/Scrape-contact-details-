"""Load uploaded knowledge files into plain-text documents.

Supports .txt/.md natively and .pdf/.docx/.xlsx when the optional parser
libraries are installed (pypdf, python-docx, openpyxl). Unsupported or
unparseable files are skipped with a warning rather than crashing ingestion.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".rst", ".csv"}
SUPPORTED_SUFFIXES = TEXT_SUFFIXES | {".pdf", ".docx", ".xlsx"}


@dataclass
class RawDocument:
    title: str
    path: str
    text: str
    suffix: str


@dataclass
class IngestReport:
    loaded: list[RawDocument] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (path, reason)

    def summary(self) -> str:
        return f"{len(self.loaded)} document(s) loaded, {len(self.skipped)} skipped."


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _read_pdf(path: Path) -> str:
    from pypdf import PdfReader  # optional dependency

    reader = PdfReader(str(path))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _read_docx(path: Path) -> str:
    import docx  # python-docx, optional

    doc = docx.Document(str(path))
    return "\n".join(p.text for p in doc.paragraphs)


def _read_xlsx(path: Path) -> str:
    from openpyxl import load_workbook  # optional

    wb = load_workbook(str(path), data_only=True, read_only=True)
    lines: list[str] = []
    for ws in wb.worksheets:
        lines.append(f"# Sheet: {ws.title}")
        for row in ws.iter_rows(values_only=True):
            cells = [str(c) for c in row if c is not None]
            if cells:
                lines.append(" | ".join(cells))
    wb.close()
    return "\n".join(lines)


def load_file(path: str | Path) -> RawDocument:
    """Load a single file into a RawDocument (raises on unsupported/parse error)."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        text = _read_text(path)
    elif suffix == ".pdf":
        text = _read_pdf(path)
    elif suffix == ".docx":
        text = _read_docx(path)
    elif suffix == ".xlsx":
        text = _read_xlsx(path)
    else:
        raise ValueError(f"Unsupported file type: {suffix}")
    return RawDocument(title=path.stem, path=str(path), text=text, suffix=suffix)


def load_directory(directory: str | Path) -> IngestReport:
    """Recursively load every supported file under ``directory``."""
    directory = Path(directory)
    report = IngestReport()
    if not directory.exists():
        return report
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        try:
            doc = load_file(path)
            if doc.text.strip():
                report.loaded.append(doc)
            else:
                report.skipped.append((str(path), "empty after parsing"))
        except ImportError as exc:
            report.skipped.append((str(path), f"missing parser dependency: {exc.name}"))
        except Exception as exc:  # noqa: BLE001 - never let one bad file stop ingestion
            report.skipped.append((str(path), f"{type(exc).__name__}: {exc}"))
    return report

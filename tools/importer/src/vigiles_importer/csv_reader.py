"""Reading contractor CSV files exported from Excel.

Handles UTF-8, UTF-8 with BOM and cp1250 (Excel on Polish Windows), and
either ``;`` (Polish Excel) or ``,`` as the separator. Problems that make
the whole file unreadable raise ``FileFormatError``; problems with single
rows are reported per row by ``asset_rows``.
"""

import codecs
import csv
import io
from dataclasses import dataclass


class FileFormatError(Exception):
    """The file as a whole cannot be processed."""


@dataclass(frozen=True)
class CsvTable:
    header: list[str]
    rows: list[tuple[int, list[str]]]  # (line number in the file, cells)
    encoding: str
    delimiter: str


def decode(data: bytes) -> tuple[str, str]:
    """Return (text, encoding). BOM wins, then strict UTF-8, then cp1250."""
    if data.startswith(codecs.BOM_UTF8):
        return data[len(codecs.BOM_UTF8) :].decode("utf-8"), "utf-8-sig"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("cp1250"), "cp1250"
    except UnicodeDecodeError as exc:
        raise FileFormatError(
            "file encoding not recognised; save it as UTF-8 or Windows-1250 (cp1250)"
        ) from exc


def detect_delimiter(header_line: str) -> str:
    semicolons, commas = header_line.count(";"), header_line.count(",")
    if semicolons == 0 and commas == 0:
        raise FileFormatError("cannot detect the column separator; use ';' or ','")
    return ";" if semicolons >= commas else ","


def read_csv(data: bytes) -> CsvTable:
    text, encoding = decode(data)
    first_line = next((line for line in text.splitlines() if line.strip()), None)
    if first_line is None:
        raise FileFormatError("file is empty")
    delimiter = detect_delimiter(first_line)

    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    header: list[str] | None = None
    rows: list[tuple[int, list[str]]] = []
    try:
        for cells in reader:
            if not any(c.strip() for c in cells):
                continue  # Excel leaves rows of empty separators at the end
            if header is None:
                header = [c.strip() for c in cells]
                continue
            rows.append((reader.line_num, cells))
    except csv.Error as exc:
        raise FileFormatError(f"malformed CSV near line {reader.line_num}: {exc}") from exc
    assert header is not None
    return CsvTable(header=header, rows=rows, encoding=encoding, delimiter=delimiter)


def write_csv(path, header: list[str], rows: list[list[str]], encoding: str, delimiter: str) -> None:
    """Write a report in the encoding and separator of the source file,
    so it opens in the same Excel the contractor used."""
    with open(path, "w", encoding=encoding, errors="replace", newline="") as handle:
        writer = csv.writer(handle, delimiter=delimiter, lineterminator="\r\n")
        writer.writerow(header)
        writer.writerows(rows)

import openpyxl
import pytest

from jevtweet.xlsx_import import read_sheet


def _workbook(path, sheets):
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    for name, rows in sheets.items():
        sheet = workbook.create_sheet(name)
        for row in rows:
            sheet.append(row)
    workbook.save(path)


def test_reads_header_keyed_rows_and_skips_blank_lines(tmp_path):
    path = tmp_path / "w.xlsx"
    _workbook(path, {"All": [["id", "text"], ["a", "hello"], [None, None], ["b", "world"]]})
    assert read_sheet(path, "All") == [{"id": "a", "text": "hello"}, {"id": "b", "text": "world"}]


def test_unknown_sheet_and_duplicate_headers_rejected(tmp_path):
    path = tmp_path / "w.xlsx"
    _workbook(path, {"All": [["id", "id"], ["a", "b"]]})
    with pytest.raises(ValueError, match="Unknown sheet"):
        read_sheet(path, "Missing")
    with pytest.raises(ValueError, match="uplicate header"):
        read_sheet(path, "All")


def test_row_cap_enforced(tmp_path):
    path = tmp_path / "w.xlsx"
    _workbook(path, {"All": [["id"]] + [[str(i)] for i in range(5)]})
    with pytest.raises(ValueError, match="row cap"):
        read_sheet(path, "All", max_rows=3)

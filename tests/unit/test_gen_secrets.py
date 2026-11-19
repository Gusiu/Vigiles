"""gen_secrets.py writes LF line endings, also where text mode defaults to CRLF."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_gen_secrets():
    spec = importlib.util.spec_from_file_location("gen_secrets", ROOT / "scripts" / "gen_secrets.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_files_use_lf_line_endings(tmp_path, monkeypatch) -> None:
    gen_secrets = _load_gen_secrets()
    monkeypatch.setattr(gen_secrets, "ROOT", tmp_path)
    monkeypatch.setattr(gen_secrets, "ENV", tmp_path / ".env")
    monkeypatch.setattr(gen_secrets, "REALM", tmp_path / "realm.json")
    monkeypatch.setattr("sys.argv", ["gen_secrets.py"])
    write_text = Path.write_text

    def windows_write_text(self, data, encoding=None, errors=None, newline=None):
        # Text mode on Windows turns "\n" into "\r\n" unless newline is given.
        if newline is None:
            data, newline = data.replace("\n", "\r\n"), ""
        return write_text(self, data, encoding=encoding, errors=errors, newline=newline)

    monkeypatch.setattr(Path, "write_text", windows_write_text)
    gen_secrets.main()
    assert b"\r" not in (tmp_path / ".env").read_bytes()
    assert b"\r" not in (tmp_path / "realm.json").read_bytes()

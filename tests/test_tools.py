import pytest
from pathlib import Path
from backend.tools import read_file, write_file, run_bash, list_dir, grep, glob_files, dispatch, edit_file


@pytest.fixture
def ws(tmp_path):
    return str(tmp_path)


# --- read_file ---

def test_read_file_returns_content(ws):
    Path(ws, "hello.txt").write_text("line1\nline2\nline3")
    result = read_file(ws, "hello.txt")
    assert "line1" in result
    assert "line2" in result
    assert "   1│" in result  # line numbers present


def test_read_file_offset_and_limit(ws):
    Path(ws, "f.txt").write_text("a\nb\nc\nd")
    result = read_file(ws, "f.txt", offset=2, limit=2)
    assert "   2│ b" in result
    assert "   3│ c" in result


def test_read_file_offset_only(ws):
    Path(ws, "f.txt").write_text("a\nb\nc")
    result = read_file(ws, "f.txt", offset=2)
    assert "   2│ b" in result
    assert "   3│ c" in result


def test_read_file_limit_only(ws):
    Path(ws, "f.txt").write_text("a\nb\nc")
    result = read_file(ws, "f.txt", limit=2)
    assert "   1│ a" in result
    assert "   2│ b" in result


def test_read_file_missing_returns_error(ws):
    result = read_file(ws, "nonexistent.txt")
    assert result.startswith("error:")


# --- write_file ---

def test_write_file_creates_file(ws):
    write_file(ws, "out.txt", "hello")
    assert Path(ws, "out.txt").read_text() == "hello"


def test_write_file_creates_nested_dirs(ws):
    write_file(ws, "sub/deep/file.txt", "content")
    assert Path(ws, "sub/deep/file.txt").read_text() == "content"


def test_write_file_returns_confirmation(ws):
    result = write_file(ws, "x.txt", "y")
    assert "x.txt" in result


def test_write_file_returns_error_on_permission_denied(ws, monkeypatch):
    # monkeypatch mkdir to raise PermissionError
    original_mkdir = Path.mkdir
    def fake_mkdir(self, *args, **kwargs):
        raise PermissionError("permission denied")
    monkeypatch.setattr(Path, "mkdir", fake_mkdir)
    result = write_file(ws, "sub/file.txt", "content")
    assert result.startswith("error:")


# --- run_bash ---

def test_run_bash_captures_stdout(ws):
    result = run_bash(ws, "echo hello")
    assert "hello" in result
    assert "exit: 0" in result


def test_run_bash_cwd_is_workspace(ws):
    Path(ws, "marker.txt").write_text("x")
    result = run_bash(ws, "ls")
    assert "marker.txt" in result
    assert "exit: 0" in result


def test_run_bash_captures_stderr_on_failure(ws):
    result = run_bash(ws, "ls /nonexistent_xyz_dir_abc")
    assert len(result.strip()) > 0  # some error output


def test_run_bash_timeout_returns_error(ws):
    result = run_bash(ws, "sleep 10", timeout=1)
    assert "timed out" in result


# --- list_dir ---

def test_list_dir_shows_files_and_dirs(ws):
    Path(ws, "a.py").write_text("")
    Path(ws, "subdir").mkdir()
    result = list_dir(ws, ".")
    assert "file  a.py" in result
    assert "dir  subdir" in result


def test_list_dir_is_sorted(ws):
    Path(ws, "z.txt").write_text("")
    Path(ws, "a.txt").write_text("")
    lines = list_dir(ws, ".").splitlines()
    names = [l.split()[-1] for l in lines]
    assert names == sorted(names)


# --- grep ---

def test_grep_finds_match(ws):
    Path(ws, "code.py").write_text("def foo():\n    return 42\n")
    result = grep(ws, r"def foo", ".")
    assert "code.py:1" in result


def test_grep_no_match_returns_message(ws):
    Path(ws, "empty.py").write_text("x = 1\n")
    result = grep(ws, r"def foo", ".")
    assert result == "no matches"


def test_grep_glob_filter(ws):
    Path(ws, "a.py").write_text("def foo(): pass\n")
    Path(ws, "b.txt").write_text("def foo(): pass\n")
    result = grep(ws, r"def foo", ".", glob_filter="*.py")
    assert "a.py" in result
    assert "b.txt" not in result


# --- glob_files ---

def test_glob_files_matches_pattern(ws):
    Path(ws, "main.py").write_text("")
    Path(ws, "test.py").write_text("")
    Path(ws, "readme.md").write_text("")
    result = glob_files(ws, "*.py")
    assert "main.py" in result
    assert "test.py" in result
    assert "readme.md" not in result


def test_glob_files_no_match_returns_message(ws):
    result = glob_files(ws, "*.xyz")
    assert result == "no matches"


# --- dispatch ---

def test_dispatch_routes_to_correct_tool(ws):
    Path(ws, "f.txt").write_text("hi")
    result = dispatch(ws, "read_file", {"path": "f.txt"})
    assert "hi" in result  # content present (with line number prefix)


def test_dispatch_unknown_tool(ws):
    result = dispatch(ws, "unknown_tool", {})
    assert "unknown tool" in result


# --- edit_file ---

def test_edit_file_replaces_exact_match(ws):
    Path(ws, "f.py").write_text("def foo():\n    return 1\n")
    result = edit_file(ws, "f.py", "return 1", "return 2")
    assert result == "edited f.py"
    assert Path(ws, "f.py").read_text() == "def foo():\n    return 2\n"

def test_edit_file_errors_on_not_found(ws):
    Path(ws, "f.py").write_text("x = 1\n")
    result = edit_file(ws, "f.py", "x = 99", "x = 2")
    assert result.startswith("error:")

def test_edit_file_errors_on_multiple_matches(ws):
    Path(ws, "f.py").write_text("x = 1\nx = 1\n")
    result = edit_file(ws, "f.py", "x = 1", "x = 2")
    assert result.startswith("error:")

def test_edit_file_replace_all(ws):
    Path(ws, "f.py").write_text("x = 1\nx = 1\n")
    result = edit_file(ws, "f.py", "x = 1", "x = 2", replace_all=True)
    assert "2 replacements" in result
    assert Path(ws, "f.py").read_text() == "x = 2\nx = 2\n"

def test_dispatch_edit_file(ws):
    Path(ws, "g.py").write_text("a = 1\n")
    result = dispatch(ws, "edit_file", {"path": "g.py", "old_string": "a = 1", "new_string": "a = 2"})
    assert result == "edited g.py"

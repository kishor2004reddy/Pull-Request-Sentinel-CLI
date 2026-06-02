from pr_sentinel.diff import chunker


def _file(path: str, size: int) -> dict:
    return {
        "filePath": path,
        "changeType": "modified",
        "diff": "x" * size,
        "addedLines": 0,
        "removedLines": 0,
    }


def test_empty_input_returns_empty_list():
    assert chunker.chunk_files([]) == []


def test_all_files_fit_in_one_chunk():
    files = [_file("a.py", 100), _file("b.py", 200), _file("c.py", 300)]
    chunks = chunker.chunk_files(files, budget=10_000)
    assert len(chunks) == 1
    assert [f["filePath"] for f in chunks[0]] == ["a.py", "b.py", "c.py"]


def test_files_split_into_multiple_chunks_when_over_budget():
    files = [_file("a.py", 600), _file("b.py", 600), _file("c.py", 600)]
    chunks = chunker.chunk_files(files, budget=1000)
    # First chunk can hold a.py (600). Adding b.py would exceed 1000 → new chunk.
    assert len(chunks) >= 2
    assert chunks[0][0]["filePath"] == "a.py"
    # Order is preserved across chunks
    flat = [f["filePath"] for c in chunks for f in c]
    assert flat == ["a.py", "b.py", "c.py"]


def test_single_file_larger_than_budget_gets_own_chunk():
    files = [_file("huge.py", 5000), _file("small.py", 100)]
    chunks = chunker.chunk_files(files, budget=1000)
    assert chunks[0] == [files[0]]  # huge alone
    assert chunks[1][0]["filePath"] == "small.py"


def test_format_diff_block_emits_file_headers():
    files = [
        {"filePath": "a.py", "changeType": "modified", "diff": "line1"},
        {"filePath": "b.py", "changeType": "added", "diff": "line2"},
    ]
    block = chunker.format_diff_block(files)
    assert "===== FILE: a.py (modified) =====" in block
    assert "===== FILE: b.py (added) =====" in block
    assert "line1" in block
    assert "line2" in block

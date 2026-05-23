DEFAULT_CHUNK_BUDGET = 100_000


def chunk_files(files: list[dict], budget: int = DEFAULT_CHUNK_BUDGET) -> list[list[dict]]:
    """Greedy-pack file records into chunks whose total diff size <= budget.

    A single file larger than budget gets its own chunk (we never split a file).
    Order is preserved.
    """
    if not files:
        return []

    chunks: list[list[dict]] = [[]]
    current_size = 0
    for f in files:
        size = len(f["diff"])
        if current_size + size > budget and chunks[-1]:
            chunks.append([])
            current_size = 0
        chunks[-1].append(f)
        current_size += size

    return [c for c in chunks if c]


def format_diff_block(files: list[dict]) -> str:
    """Join a chunk's files into a single string with explicit file separators."""
    parts: list[str] = []
    for f in files:
        parts.append(f"===== FILE: {f['filePath']} ({f['changeType']}) =====")
        parts.append(f["diff"].rstrip())
        parts.append("")
    return "\n".join(parts)

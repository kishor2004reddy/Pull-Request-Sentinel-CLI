from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from pr_sentinel.agents import AGENT_REGISTRY

DEFAULT_MAX_PARALLEL = 8


def run_agents(
    agent_keys: list[str],
    chunks: list[list[dict]],
    on_start: Callable[[str], None] | None = None,
    on_finish: Callable[[str, dict | Exception], None] | None = None,
    on_chunk_done: Callable[[str, int, int], None] | None = None,
    max_parallel: int = DEFAULT_MAX_PARALLEL,
    model: str | None = None,
) -> list[dict]:
    """Run all (agent, chunk) tasks in parallel under a single bounded pool.

    Returns one result dict per agent_key, in the input order. A result is either
    {"agent": name, "findings": [...]} on success or
    {"agent": name, "findings": [], "failed": True, "error": "..."} on any chunk
    failure for that agent (partial findings are discarded to match prior behavior).
    """
    instances = {k: AGENT_REGISTRY[k]() for k in agent_keys}
    display_names = {k: instances[k].display_name for k in agent_keys}

    if on_start:
        for k in agent_keys:
            on_start(display_names[k])

    total_chunks = len(chunks)
    findings_by_agent: dict[str, list[dict]] = {k: [] for k in agent_keys}
    error_by_agent: dict[str, str | None] = {k: None for k in agent_keys}
    chunks_done_by_agent: dict[str, int] = {k: 0 for k in agent_keys}
    finished_signaled: set[str] = set()

    def _signal_finished(k: str, payload) -> None:
        if k in finished_signaled:
            return
        finished_signaled.add(k)
        if on_finish:
            on_finish(display_names[k], payload)

    if total_chunks == 0:
        return [{"agent": display_names[k], "findings": []} for k in agent_keys]

    tasks: list[tuple[str, int, list[dict]]] = []
    for k in agent_keys:
        for idx, chunk in enumerate(chunks, start=1):
            tasks.append((k, idx, chunk))

    workers = max(1, min(max_parallel, len(tasks)))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_meta = {}
        for k, idx, chunk in tasks:
            future = pool.submit(instances[k].process_chunk, chunk, model)
            future_to_meta[future] = (k, idx)

        for future in as_completed(future_to_meta):
            k, _ = future_to_meta[future]

            if error_by_agent[k] is not None:
                continue

            try:
                chunk_findings = future.result()
            except Exception as e:
                error_by_agent[k] = str(e)
                findings_by_agent[k] = []
                _signal_finished(k, e)
                continue

            findings_by_agent[k].extend(chunk_findings)
            chunks_done_by_agent[k] += 1
            if on_chunk_done:
                on_chunk_done(display_names[k], chunks_done_by_agent[k], total_chunks)

            if chunks_done_by_agent[k] == total_chunks:
                result = {"agent": display_names[k], "findings": findings_by_agent[k]}
                _signal_finished(k, result)

    results: list[dict] = []
    for k in agent_keys:
        if error_by_agent[k] is not None:
            results.append(
                {
                    "agent": display_names[k],
                    "findings": [],
                    "failed": True,
                    "error": error_by_agent[k],
                }
            )
        else:
            results.append(
                {"agent": display_names[k], "findings": findings_by_agent[k]}
            )
    return results

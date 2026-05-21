from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from pr_sentinel.agents import AGENT_REGISTRY


def run_agents(
    agent_keys: list[str],
    files: list[dict],
    chunk_budget: int,
    on_start: Callable[[str], None] | None = None,
    on_finish: Callable[[str, dict | Exception], None] | None = None,
    on_chunk_done: Callable[[str, int, int], None] | None = None,
    max_workers: int | None = None,
    model: str | None = None,
) -> list[dict]:
    """Run selected agents in parallel and return results in deterministic order.

    Order of returned results matches the order of agent_keys, regardless of
    completion order, so reports are stable across runs.
    """
    instances = [(k, AGENT_REGISTRY[k]()) for k in agent_keys]
    results: dict[str, dict] = {}

    workers = max_workers if max_workers is not None else max(1, len(instances))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for key, agent in instances:
            if on_start:
                on_start(agent.display_name)
            future = pool.submit(agent.run, files, chunk_budget, on_chunk_done, model)
            futures[future] = (key, agent)

        for future in as_completed(futures):
            key, agent = futures[future]
            try:
                result = future.result()
                results[key] = result
                if on_finish:
                    on_finish(agent.display_name, result)
            except Exception as e:
                results[key] = {
                    "agent": agent.display_name,
                    "findings": [],
                    "failed": True,
                    "error": str(e),
                }
                if on_finish:
                    on_finish(agent.display_name, e)

    return [results[k] for k in agent_keys]

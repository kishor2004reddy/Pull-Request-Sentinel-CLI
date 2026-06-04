"""Diff ingestion pipeline: fetch → parse → chunk.

- git_diff:    obtain a raw unified diff from git (branch range or staged).
- diff_parser: split the raw diff into per-file entries, filter noise, truncate.
- chunker:     pack parsed files into budget-sized chunks for a provider call.
"""

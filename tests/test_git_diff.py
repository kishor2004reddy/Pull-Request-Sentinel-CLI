import subprocess
from pathlib import Path

import pytest

from pr_sentinel.diff import git_diff
from pr_sentinel.diff.git_diff import GitError


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway git repo with one commit on the default branch."""
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "file.txt").write_text("hello\n", encoding="utf-8")
    _git(tmp_path, "add", "file.txt")
    _git(tmp_path, "commit", "-m", "initial")
    return tmp_path


def test_is_working_tree_dirty_clean(repo: Path):
    assert git_diff.is_working_tree_dirty(cwd=repo) is False


def test_is_working_tree_dirty_with_unstaged_change(repo: Path):
    (repo / "file.txt").write_text("hello world\n", encoding="utf-8")
    assert git_diff.is_working_tree_dirty(cwd=repo) is True


def test_is_working_tree_dirty_with_staged_change(repo: Path):
    (repo / "file.txt").write_text("changed\n", encoding="utf-8")
    _git(repo, "add", "file.txt")
    assert git_diff.is_working_tree_dirty(cwd=repo) is True


def test_is_working_tree_dirty_ignores_untracked(repo: Path):
    # An untracked file (e.g. the reports/ output) must not count as dirty —
    # it carries across a branch switch harmlessly.
    (repo / "untracked.txt").write_text("scratch\n", encoding="utf-8")
    assert git_diff.is_working_tree_dirty(cwd=repo) is False


def test_create_branch_from_start_point_and_checkout(repo: Path):
    git_diff.create_branch("feature/x", "main", cwd=repo)
    assert git_diff.get_current_branch(cwd=repo) == "feature/x"


def test_create_branch_fails_when_name_exists(repo: Path):
    git_diff.create_branch("feature/x", "main", cwd=repo)
    git_diff.checkout_branch("main", cwd=repo)
    with pytest.raises(GitError):
        git_diff.create_branch("feature/x", "main", cwd=repo)


def test_checkout_branch_switches(repo: Path):
    _git(repo, "branch", "other")
    git_diff.checkout_branch("other", cwd=repo)
    assert git_diff.get_current_branch(cwd=repo) == "other"


def test_checkout_branch_unknown_raises(repo: Path):
    with pytest.raises(GitError):
        git_diff.checkout_branch("does-not-exist", cwd=repo)

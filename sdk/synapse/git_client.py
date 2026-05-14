"""Git client namespace for Synapse Cell — Sprint A Batch 6.

Wraps the host git binary via Cell.command("git ..."). Each method builds
a git command, executes it in the cell's data directory, and returns stdout.

Usage:
    cell = Cell(api_url="http://localhost:8002", persistent=True)
    cell.git.clone("https://github.com/user/repo.git")
    cell.git.add(".")
    cell.git.commit("Initial commit")
    cell.git.push()
"""
from __future__ import annotations

from typing import List, Optional, Union, TYPE_CHECKING

if TYPE_CHECKING:
    from synapse.cell import Cell


class GitNamespace:
    """Git client namespace. Access via ``cell.git``.

    All methods shell out to the host ``git`` binary in the cell's data
    directory via ``cell.command("git ...")``. Raises ``CellError`` if
    the git command fails (non-zero exit code).
    """

    def __init__(self, cell: "Cell"):
        self._cell = cell

    def _run(self, *args: str) -> str:
        """Run a git command and return stdout. Raises on non-zero exit."""
        cmd = "git " + " ".join(args)
        result = self._cell.command(cmd)
        if hasattr(result, 'exit_code') and result.exit_code != 0:
            from synapse.cell import CellError
            stderr = getattr(result, 'stderr', '')
            raise CellError(f"git command failed (exit {result.exit_code}): {stderr}")
        if hasattr(result, 'stdout'):
            return result.stdout.strip()
        # If result is a dict (from _request)
        if isinstance(result, dict):
            if result.get("exit_code", 0) != 0:
                from synapse.cell import CellError
                raise CellError(f"git command failed: {result.get('stderr', '')}")
            return result.get("stdout", "").strip()
        return str(result).strip()

    # ─── Repository operations ───────────────────────────────────

    def clone(self, repo_url: str, dest: str = ".", depth: Optional[int] = None) -> str:
        """Clone a repository into the cell's data directory.

        Args:
            repo_url: Git repository URL (HTTPS or SSH).
            dest: Destination directory (relative to /data/).
            depth: If set, create a shallow clone with this many commits.
        """
        args = ["clone"]
        if depth:
            args.extend(["--depth", str(depth)])
        args.extend([repo_url, dest])
        return self._run(*args)

    def init(self, bare: bool = False) -> str:
        """Initialize a new git repository in the cell's data directory."""
        args = ["init"]
        if bare:
            args.append("--bare")
        return self._run(*args)

    # ─── Status + staging ────────────────────────────────────────

    def status(self, short: bool = False) -> str:
        """Show working tree status."""
        args = ["status"]
        if short:
            args.append("--short")
        return self._run(*args)

    def add(self, paths: Union[str, List[str]] = ".") -> str:
        """Stage files for commit.

        Args:
            paths: File path(s) to stage. Defaults to "." (all).
        """
        if isinstance(paths, str):
            paths = [paths]
        return self._run("add", *paths)

    def commit(self, message: str, author: Optional[str] = None) -> str:
        """Create a commit with the given message.

        Args:
            message: Commit message.
            author: Optional author string ("Name <email>").
        """
        args = ["commit", "-m", f'"{message}"']
        if author:
            args.extend(["--author", f'"{author}"'])
        return self._run(*args)

    def reset(self, ref: str = "HEAD", hard: bool = False) -> str:
        """Reset current HEAD to the specified state.

        Args:
            ref: Commit ref to reset to (default: HEAD).
            hard: If True, discard working tree changes (--hard).
        """
        args = ["reset"]
        if hard:
            args.append("--hard")
        args.append(ref)
        return self._run(*args)

    def restore(self, paths: Union[str, List[str]]) -> str:
        """Restore working tree files.

        Args:
            paths: File path(s) to restore.
        """
        if isinstance(paths, str):
            paths = [paths]
        return self._run("restore", *paths)

    # ─── Branches ────────────────────────────────────────────────

    def branch_list(self) -> List[str]:
        """List all local branches. Returns a list of branch names."""
        output = self._run("branch", "--list")
        branches = []
        for line in output.splitlines():
            name = line.strip().lstrip("* ").strip()
            if name:
                branches.append(name)
        return branches

    def branch_create(self, name: str) -> str:
        """Create a new branch."""
        return self._run("branch", name)

    def checkout(self, ref: str) -> str:
        """Switch branches or restore working tree files.

        Args:
            ref: Branch name, tag, or commit SHA.
        """
        return self._run("checkout", ref)

    def branch_delete(self, name: str, force: bool = False) -> str:
        """Delete a branch.

        Args:
            name: Branch name.
            force: If True, force-delete even if not merged (-D).
        """
        flag = "-D" if force else "-d"
        return self._run("branch", flag, name)

    # ─── Remote ──────────────────────────────────────────────────

    def remote_add(self, name: str, url: str) -> str:
        """Add a remote.

        Args:
            name: Remote name (e.g., "origin").
            url: Remote URL.
        """
        return self._run("remote", "add", name, url)

    def remote_get(self, name: str = "origin") -> str:
        """Get the URL of a remote."""
        return self._run("remote", "get-url", name)

    def push(self, remote: str = "origin", branch: Optional[str] = None,
             force: bool = False) -> str:
        """Push commits to a remote.

        Args:
            remote: Remote name (default: "origin").
            branch: Branch to push (default: current branch).
            force: If True, force-push.
        """
        args = ["push"]
        if force:
            args.append("--force")
        args.append(remote)
        if branch:
            args.append(branch)
        return self._run(*args)

    def pull(self, remote: str = "origin", branch: Optional[str] = None) -> str:
        """Pull commits from a remote.

        Args:
            remote: Remote name (default: "origin").
            branch: Branch to pull (default: current branch).
        """
        args = ["pull", remote]
        if branch:
            args.append(branch)
        return self._run(*args)

    # ─── Configuration ───────────────────────────────────────────

    def config_set(self, key: str, value: str, scope: str = "local") -> str:
        """Set a git config value.

        Args:
            key: Config key (e.g., "user.name").
            value: Config value.
            scope: "local" (default), "global", or "system".
        """
        return self._run("config", f"--{scope}", key, value)

    def config_get(self, key: str) -> str:
        """Get a git config value."""
        return self._run("config", "--get", key)

    def configure_user(self, name: str, email: str) -> str:
        """Set user.name and user.email (local scope).

        Args:
            name: User display name.
            email: User email address.
        """
        self.config_set("user.name", name)
        return self.config_set("user.email", email)

    def authenticate(self, token: str) -> str:
        """Configure credential helper to use a personal access token.

        Sets the git credential.helper to store the token so subsequent
        push/pull operations authenticate automatically.

        Args:
            token: Personal access token (GitHub, GitLab, etc.).
        """
        # Use the store helper and write the token to .git-credentials
        self._run("config", "--local", "credential.helper", "store")
        # Write the token file
        self._cell.command(
            f'echo "https://token:{token}@github.com" > /data/.git-credentials'
        )
        return "Credential helper configured"

    # ─── Convenience ─────────────────────────────────────────────

    def log(self, max_count: int = 10, oneline: bool = True) -> str:
        """Show commit log.

        Args:
            max_count: Maximum number of commits to show.
            oneline: If True, show each commit on one line.
        """
        args = ["log", f"--max-count={max_count}"]
        if oneline:
            args.append("--oneline")
        return self._run(*args)

    def diff(self, cached: bool = False) -> str:
        """Show changes between commits, working tree, etc."""
        args = ["diff"]
        if cached:
            args.append("--cached")
        return self._run(*args)

    def __repr__(self) -> str:
        return f"GitNamespace(cell={self._cell.cell_id[:8]}...)"

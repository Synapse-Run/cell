---
title: Git Integration
description: Clone, commit, push, and manage Git repositories inside sandboxes.
---

## Git Namespace

The `cell.git` namespace provides 19 Git operations:

```python
# Clone a repository
cell.git.clone("https://github.com/user/repo.git")

# Basic operations
status = cell.git.status()
cell.git.add(".")
cell.git.commit("feat: add new feature")

# Branch management
cell.git.branch("feature-branch")
cell.git.checkout("feature-branch")
branches = cell.git.list_branches()

# Remote operations
cell.git.push()
cell.git.pull()

# Diff and log
diff = cell.git.diff()
log = cell.git.log(n=10)
```

## Full Method List

| Method | Description |
|--------|-------------|
| `clone(url)` | Clone a repository |
| `init()` | Initialize a new repository |
| `status()` | Working tree status |
| `add(path)` | Stage files |
| `commit(message)` | Commit staged changes |
| `push()` | Push to remote |
| `pull()` | Pull from remote |
| `fetch()` | Fetch from remote |
| `branch(name)` | Create a branch |
| `checkout(ref)` | Switch branches |
| `merge(branch)` | Merge branches |
| `list_branches()` | List all branches |
| `diff()` | Show unstaged changes |
| `log(n)` | Show commit history |
| `stash()` | Stash working changes |
| `stash_pop()` | Apply stashed changes |
| `tag(name)` | Create a tag |
| `remote_add(name, url)` | Add a remote |
| `reset(ref)` | Reset HEAD |

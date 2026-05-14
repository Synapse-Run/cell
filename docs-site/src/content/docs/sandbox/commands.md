---
title: Shell Commands
description: Run shell commands inside Synapse sandboxes.
---

## Running Commands

```python
result = cell.command("ls -la /data/")
print(result.stdout)
print(result.exit_code)
```

## Background Execution

```python
handle = cell.command("python long_script.py", background=True)

# Do other work...

handle.wait(timeout_ms=60000)
print(handle.stdout)
```

## Piping and Chaining

```python
result = cell.command("echo 'hello' | tr 'h' 'H'")
print(result.stdout)  # "Hello"
```

## Process Management

```python
# List running processes
processes = cell.processes()
for p in processes:
    print(f"PID {p.pid}: {p.command}")

# Kill a process
handle.kill()
```

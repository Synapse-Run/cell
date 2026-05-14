---
title: Environment Variables
description: Set and manage environment variables in sandboxes.
---

## At Creation

```python
cell = Cell(
    envs={
        "DATABASE_URL": "postgresql://...",
        "API_KEY": "sk_live_...",
        "DEBUG": "true",
    }
)
```

## Runtime Updates

```python
# Get current environment variables
envs = cell.get_envs()

# Merge new variables (existing keys preserved)
cell.set_envs({"NEW_VAR": "value"})
```

## From the SDK

Environment variables are available to executed code:

```python
result = cell.run("import os; print(os.environ.get('API_KEY'))")
print(result.stdout)  # "sk_live_..."
```

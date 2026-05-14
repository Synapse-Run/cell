---
title: Custom Templates
description: Create and register custom sandbox templates.
---

## Template Class

```python
from synapse import Template

template = Template(
    name="data-science",
    runtime="python3",
    packages=["pandas", "numpy", "scikit-learn", "matplotlib"],
    description="Data science environment with ML libraries",
)

# Register with the gateway
template.register()
```

## Using Custom Templates

```python
cell = Cell(template="data-science")
result = cell.run("import pandas as pd; print(pd.__version__)")
```

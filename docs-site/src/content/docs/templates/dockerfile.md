---
title: Dockerfile Transpiler
description: Convert Dockerfiles to Synapse templates automatically.
---

## Overview

Synapse can transpile existing Dockerfiles into native Wasm templates:

```python
from synapse.dockerfile_transpiler import DockerfileTranspiler

transpiler = DockerfileTranspiler()
template = transpiler.transpile("./Dockerfile")
template.register()
```

## Supported Instructions

| Dockerfile Instruction | Support |
|-----------------------|---------|
| `FROM` | ✅ |
| `RUN` | ✅ |
| `COPY` | ✅ |
| `ENV` | ✅ |
| `WORKDIR` | ✅ |
| `EXPOSE` | ✅ |
| `CMD` | ✅ |
| `ENTRYPOINT` | ✅ |
| `ARG` | ✅ |
| `USER` | ✅ |

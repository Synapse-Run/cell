---
title: LangChain Integration
description: Use Synapse as a code execution tool in LangChain agents.
---

## Installation

```bash
pip install synapserun langchain
```

## Usage

```python
from synapse.langchain_tool import SynapseCellExecuteTool

tool = SynapseCellExecuteTool(api_key="cell_sk_live_...")

# Use in a LangChain agent
from langchain.agents import initialize_agent
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(model="gpt-4")
agent = initialize_agent(
    tools=[tool],
    llm=llm,
    agent="zero-shot-react-description",
)

result = agent.run("Calculate the factorial of 20 using Python")
```

## Migrating from E2B

```diff
- from langchain_e2b import E2BCodeInterpreterTool
+ from synapse.langchain_tool import SynapseCellExecuteTool

- tool = E2BCodeInterpreterTool(api_key="...")
+ tool = SynapseCellExecuteTool(api_key="...")
```

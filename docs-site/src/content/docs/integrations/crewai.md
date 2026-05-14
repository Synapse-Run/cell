---
title: CrewAI Integration
description: Use Synapse sandboxes as CrewAI tools.
---

```python
from synapse.crewai_tool import SynapseCellCrewTool

tool = SynapseCellCrewTool(api_key="cell_sk_live_...")

# Use in a CrewAI agent
from crewai import Agent, Task, Crew

coder = Agent(
    role="Python Developer",
    goal="Write and execute Python code",
    tools=[tool],
)

task = Task(
    description="Calculate pi to 100 decimal places",
    agent=coder,
)

crew = Crew(agents=[coder], tasks=[task])
result = crew.kickoff()
```

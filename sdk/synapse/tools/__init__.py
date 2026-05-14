"""Synapse Agent Tools — LangChain + CrewAI integrations.

Research-arm tools (local .syn execution):
    from synapse.tools import SynapseGenerateTool    # LangChain
    from synapse.tools import SynapseGenerateCrewTool  # CrewAI

Cell commercial tools (Python sandbox, 200x faster than E2B):
    from synapse.tools import SynapseCellExecuteTool       # LangChain
    from synapse.tools import SynapseCellCrewTool           # CrewAI
"""

from synapse.tools.langchain_tool import SynapseExecuteTool, SynapseGenerateTool
from synapse.tools.crewai_tool import SynapseExecuteCrewTool, SynapseGenerateCrewTool

# Re-export Cell commercial tools for convenience
from synapse.langchain_tool import SynapseCellExecuteTool
from synapse.crewai_tool import SynapseCellCrewTool

__all__ = [
    # Research-arm (.syn local execution)
    'SynapseExecuteTool',
    'SynapseGenerateTool',
    'SynapseExecuteCrewTool',
    'SynapseGenerateCrewTool',
    # Cell commercial (Python sandbox)
    'SynapseCellExecuteTool',
    'SynapseCellCrewTool',
]

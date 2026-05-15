#!/usr/bin/env python3
"""Smoke tests for LangChain + CrewAI integration tool classes.

These tests verify:
  1. Tool classes can be instantiated without LangChain/CrewAI installed
  2. The _run method delegates to Cell.run under the hood
  3. Streaming callbacks are wired through the LangChain callback manager
  4. tools/__init__.py exports both research-arm and Cell commercial tools

No external API keys or framework installs required.
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestLangChainCellTool(unittest.TestCase):
    """Tests for synapse.langchain_tool.SynapseCellExecuteTool."""

    def test_import_without_langchain(self):
        """Tool module imports even when langchain is not installed."""
        # The module uses a fallback stub if langchain is missing.
        from synapse.langchain_tool import SynapseCellExecuteTool
        self.assertTrue(hasattr(SynapseCellExecuteTool, '_run'))

    def test_instantiation(self):
        """Tool can be instantiated with default and custom params."""
        from synapse.langchain_tool import SynapseCellExecuteTool
        tool = SynapseCellExecuteTool()
        self.assertEqual(tool.name, "synapse_cell_executor")
        self.assertIn("200x", tool.description)

        tool2 = SynapseCellExecuteTool(
            api_url="http://localhost:8002",
            api_key="test_key",
        )
        self.assertEqual(tool2.api_url, "http://localhost:8002")
        self.assertEqual(tool2.api_key, "test_key")

    def test_pydantic_v2_model_config(self):
        """Tool uses Pydantic v2 model_config, not v1 class Config."""
        from synapse.langchain_tool import SynapseCellExecuteTool
        self.assertTrue(hasattr(SynapseCellExecuteTool, 'model_config'))
        self.assertIsInstance(SynapseCellExecuteTool.model_config, dict)
        self.assertTrue(SynapseCellExecuteTool.model_config.get("arbitrary_types_allowed"))

    @patch("synapse.cell.Cell")
    def test_run_delegates_to_cell(self, MockCell):
        """_run creates a Cell and calls .run() with the code."""
        from synapse.langchain_tool import SynapseCellExecuteTool

        mock_result = MagicMock()
        mock_result.exit_code = 0
        mock_result.stdout = "42"
        mock_result.stderr = ""
        mock_result.receipt = None

        mock_cell_instance = MagicMock()
        mock_cell_instance.run.return_value = mock_result
        mock_cell_instance.__enter__ = MagicMock(return_value=mock_cell_instance)
        mock_cell_instance.__exit__ = MagicMock(return_value=False)
        MockCell.return_value = mock_cell_instance

        tool = SynapseCellExecuteTool(api_key="test_key")
        output = tool._run("print(42)")

        mock_cell_instance.run.assert_called_once()
        call_args = mock_cell_instance.run.call_args
        self.assertEqual(call_args[0][0], "print(42)")
        self.assertIn("42", output)
        self.assertIn("Exit code: 0", output)

    @patch("synapse.cell.Cell")
    def test_streaming_wired_with_callback_manager(self, MockCell):
        """When run_manager is provided, on_stdout/on_stderr are passed to Cell.run."""
        from synapse.langchain_tool import SynapseCellExecuteTool

        mock_result = MagicMock()
        mock_result.exit_code = 0
        mock_result.stdout = "hello"
        mock_result.stderr = ""
        mock_result.receipt = None

        mock_cell_instance = MagicMock()
        mock_cell_instance.run.return_value = mock_result
        mock_cell_instance.__enter__ = MagicMock(return_value=mock_cell_instance)
        mock_cell_instance.__exit__ = MagicMock(return_value=False)
        MockCell.return_value = mock_cell_instance

        mock_run_manager = MagicMock()

        tool = SynapseCellExecuteTool(api_key="test_key")
        tool._run("print('hello')", run_manager=mock_run_manager)

        call_kwargs = mock_cell_instance.run.call_args[1]
        # on_stdout and on_stderr should be callables when run_manager is present
        self.assertIsNotNone(call_kwargs.get("on_stdout"))
        self.assertIsNotNone(call_kwargs.get("on_stderr"))


class TestCrewAICellTool(unittest.TestCase):
    """Tests for synapse.crewai_tool.SynapseCellCrewTool."""

    def test_import_without_crewai(self):
        """Tool module imports even when crewai is not installed."""
        from synapse.crewai_tool import SynapseCellCrewTool
        self.assertTrue(hasattr(SynapseCellCrewTool, '_run'))

    def test_instantiation(self):
        """Tool can be instantiated with default and custom params."""
        from synapse.crewai_tool import SynapseCellCrewTool
        tool = SynapseCellCrewTool()
        self.assertEqual(tool.name, "synapse_cell_executor")
        self.assertIn("200x", tool.description)

    def test_pydantic_v2_model_config(self):
        """Tool uses Pydantic v2 model_config, not v1 class Config."""
        from synapse.crewai_tool import SynapseCellCrewTool
        self.assertTrue(hasattr(SynapseCellCrewTool, 'model_config'))
        self.assertIsInstance(SynapseCellCrewTool.model_config, dict)

    @patch("synapse.cell.Cell")
    def test_run_delegates_to_cell(self, MockCell):
        """_run creates a Cell and calls .run() with the code."""
        from synapse.crewai_tool import SynapseCellCrewTool

        mock_result = MagicMock()
        mock_result.exit_code = 0
        mock_result.stdout = "42"
        mock_result.stderr = ""
        mock_result.receipt = None

        mock_cell_instance = MagicMock()
        mock_cell_instance.run.return_value = mock_result
        mock_cell_instance.__enter__ = MagicMock(return_value=mock_cell_instance)
        mock_cell_instance.__exit__ = MagicMock(return_value=False)
        MockCell.return_value = mock_cell_instance

        # Instantiate without kwargs -- the stub CrewBaseTool doesn't
        # accept keyword args. Set api_key via attribute instead.
        tool = SynapseCellCrewTool()
        tool.api_key = "test_key"
        output = tool._run(code="print(42)")

        mock_cell_instance.run.assert_called_once_with("print(42)")
        self.assertIn("42", output)

    def test_run_accepts_kwargs(self):
        """CrewAI's BaseTool passes input as kwargs; verify _run(**kwargs) works."""
        from synapse.crewai_tool import SynapseCellCrewTool
        import inspect
        sig = inspect.signature(SynapseCellCrewTool._run)
        params = list(sig.parameters.keys())
        # Should accept 'code' as a keyword arg and **kwargs
        self.assertIn("code", params)
        self.assertIn("kwargs", params)


class TestToolsInitExports(unittest.TestCase):
    """Tests for synapse.tools.__init__.py exports."""

    def test_research_tools_exported(self):
        """Research-arm tools are exported from synapse.tools."""
        from synapse.tools import (
            SynapseExecuteTool,
            SynapseGenerateTool,
        )
        # Access .name on instances (Pydantic v2 fields not on class)
        self.assertEqual(SynapseExecuteTool().name, "synapse_execute")
        self.assertEqual(SynapseGenerateTool().name, "synapse_compute")

    def test_cell_tools_exported(self):
        """Cell commercial tools are re-exported from synapse.tools."""
        from synapse.tools import SynapseCellExecuteTool, SynapseCellCrewTool
        self.assertEqual(SynapseCellExecuteTool().name, "synapse_cell_executor")
        self.assertEqual(SynapseCellCrewTool().name, "synapse_cell_executor")

    def test_all_list_complete(self):
        """__all__ includes both research and commercial tool names."""
        from synapse import tools
        expected = {
            'SynapseExecuteTool', 'SynapseGenerateTool',
            'SynapseExecuteCrewTool', 'SynapseGenerateCrewTool',
            'SynapseCellExecuteTool', 'SynapseCellCrewTool',
        }
        self.assertEqual(set(tools.__all__), expected)


class TestLegacyTools(unittest.TestCase):
    """Tests for legacy/preview .syn tool classes."""

    def test_syn_execute_tool(self):
        """SynapseExecuteTool (preview gateway) is still importable."""
        from synapse.langchain_tool import SynapseExecuteTool
        tool = SynapseExecuteTool()
        self.assertEqual(tool.name, "synapse_execute")

    def test_validate_tool_returns_unavailable(self):
        """SynapseValidateTool returns a clear unavailable message."""
        from synapse.langchain_tool import SynapseValidateTool
        tool = SynapseValidateTool()
        result = tool._run("any code")
        self.assertIn("not part of the current Synapse preview surface", result)

    def test_syn_crew_tool(self):
        """SynapseCrewTool (preview gateway) is still importable."""
        from synapse.crewai_tool import SynapseCrewTool
        tool = SynapseCrewTool()
        self.assertEqual(tool.name, "synapse_execute")


if __name__ == "__main__":
    # Run with verbose output
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)

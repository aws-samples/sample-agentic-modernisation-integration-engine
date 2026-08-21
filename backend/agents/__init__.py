"""AI agents for the Code Transformation Engine.

Agents are plain Python classes that:
- Accept a StorageManager instance for data access
- Use boto3 bedrock-runtime for LLM inference
- Have async generator methods that yield SSE event dicts
- Handle Bedrock unavailability gracefully
"""

from agents.doc_analysis_agent import DocAnalysisAgent
from agents.kiro_specs_agent import KiroSpecsAgent
from agents.llm_judge import LLMJudge

__all__ = ["DocAnalysisAgent", "KiroSpecsAgent", "LLMJudge"]

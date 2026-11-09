"""Versioned durable LangGraph execution, independent of local queue delivery."""
import os

# This product does not send business traces to a hosted service. Explicit call
# contexts additionally disable callbacks/tracing for parent and child graphs.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGGRAPH_STRICT_MSGPACK"] = "true"

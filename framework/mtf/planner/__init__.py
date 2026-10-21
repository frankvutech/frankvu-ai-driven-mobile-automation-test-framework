"""AI Planner: natural language -> candidate DSL plan (structured output) -> validation -> human approval.

The planner sits outside the execution engine. It can only *propose* data; it never operates a
device, calls the backend, runs shell commands or executes code. Saved plans run without any AI.
"""

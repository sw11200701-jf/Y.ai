"""Read-only admission of the already resolved LLM prompt before durable issue.

Reuse 03's actual renderer; do not infer preflight status from a later error code.
Attachment extraction and model transport remain in the business operation.
"""
from copy import deepcopy


def validate_llm_prompt_request(*, config, inputs):
    # A typed message may supply the legacy User prompt only after its governed
    # extraction. Do not fake that text or perform attachment I/O here. Canonical
    # role prompts always use the explicit variables dictionary unchanged by that
    # extraction, so their pure rendering can be checked before any effect.
    if 'prompt_messages' not in config and inputs.get('message') is not None:
        return
    from app.agents.prompts import render_messages
    render_messages(config, deepcopy(inputs))

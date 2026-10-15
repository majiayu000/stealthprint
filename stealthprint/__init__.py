"""stealthprint — fingerprint openai-compatible stealth models, model-agnostic."""

__version__ = "0.1.0"

from .client import ChatClient
from .layers import (
    tokenizer_differential,
    wrapper_constant,
    context_search,
    needle_test,
    error_family,
    vision_truth,
    vision_repeat,
    video_probe,
    catalog_ab,
)

__all__ = [
    "ChatClient",
    "tokenizer_differential",
    "wrapper_constant",
    "context_search",
    "needle_test",
    "error_family",
    "vision_truth",
    "vision_repeat",
    "video_probe",
    "catalog_ab",
    "__version__",
]

from .probes_extra import tokenizer_repeats, echo_verify, tools_probe, wrapper_turns
from .survey import run_survey

__all__ += ["tokenizer_repeats", "echo_verify", "tools_probe", "wrapper_turns", "run_survey"]

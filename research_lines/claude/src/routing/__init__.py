"""Per-token mixture-of-experts routing trace utilities."""

from .alignment import TokenAnnotations, annotate_chat_prompt
from .capture import (
    DEFAULT_ROUTER_ADAPTER,
    GptOssRouterAdapter,
    OlmoeRouterAdapter,
    Qwen3MoeRouterAdapter,
    RouterAdapter,
    RouterLayerCapture,
    RouterTraceRecorder,
    available_router_adapters,
    describe_model_routers,
    register_router_adapter,
    resolve_router_adapter,
)
from .schema import RoutingStep, StepMetadata
from .validate import validate_trace
from .writer import ShardedTraceWriter

__all__ = [
    "DEFAULT_ROUTER_ADAPTER",
    "GptOssRouterAdapter",
    "OlmoeRouterAdapter",
    "Qwen3MoeRouterAdapter",
    "RouterAdapter",
    "RouterLayerCapture",
    "RouterTraceRecorder",
    "RoutingStep",
    "ShardedTraceWriter",
    "StepMetadata",
    "TokenAnnotations",
    "annotate_chat_prompt",
    "available_router_adapters",
    "describe_model_routers",
    "register_router_adapter",
    "resolve_router_adapter",
    "validate_trace",
]

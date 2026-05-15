"""Inference backend abstraction.

Provides a unified interface for LLM inference across different backends
(llama-cpp-python, MLX). Phase 1 implements the llama.cpp backend.
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

logger = logging.getLogger("next-edit-server.inference")


class InferenceBackend(ABC):
    """Abstract base class for LLM inference backends."""

    @abstractmethod
    def generate(self, prompt: str, max_tokens: int = 256, temperature: float = 0.1) -> str:
        """Generate text completion for the given prompt."""
        ...

    @abstractmethod
    def is_loaded(self) -> bool:
        """Check if the model is loaded and ready for inference."""
        ...

    @abstractmethod
    def load(self) -> None:
        """Load the model into memory."""
        ...

    @abstractmethod
    def unload(self) -> None:
        """Unload the model from memory."""
        ...


class LlamaCppBackend(InferenceBackend):
    """Inference backend using llama-cpp-python.

    Supports GGUF model files for both CPU and GPU inference.
    """

    def __init__(
        self,
        model_path: str,
        n_ctx: int = 4096,
        n_gpu_layers: int = -1,
        verbose: bool = False,
    ) -> None:
        self._model_path = model_path
        self._n_ctx = n_ctx
        self._n_gpu_layers = n_gpu_layers
        self._verbose = verbose
        self._llm: Any = None

    def load(self) -> None:
        if self._llm is not None:
            return

        from llama_cpp import Llama

        logger.info("Loading model from %s", self._model_path)
        self._llm = Llama(
            model_path=self._model_path,
            n_ctx=self._n_ctx,
            n_gpu_layers=self._n_gpu_layers,
            verbose=self._verbose,
        )
        logger.info("Model loaded successfully")

    def unload(self) -> None:
        self._llm = None
        logger.info("Model unloaded")

    def is_loaded(self) -> bool:
        return self._llm is not None

    def generate(self, prompt: str, max_tokens: int = 256, temperature: float = 0.1) -> str:
        if self._llm is None:
            raise RuntimeError("Model not loaded. Call load() first.")

        result = self._llm(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=["</next_edit>", "\n\n\n"],
            echo=False,
        )

        text = result["choices"][0]["text"]
        return text.strip()


class DummyBackend(InferenceBackend):
    """A dummy backend for testing that generates predictable output.

    For rename predictions, it produces the expected NES diff by simple
    string replacement. This allows end-to-end testing without a real model.
    """

    def __init__(self) -> None:
        self._loaded = False
        self._pending_context: dict[str, Any] = {}

    def set_context(self, context: dict[str, Any]) -> None:
        """Set context for the next generation (used by the generator)."""
        self._pending_context = context

    def load(self) -> None:
        self._loaded = True

    def unload(self) -> None:
        self._loaded = False

    def is_loaded(self) -> bool:
        return self._loaded

    def generate(self, prompt: str, max_tokens: int = 256, temperature: float = 0.1) -> str:
        ctx = self._pending_context

        if "old_name" in ctx and "new_name" in ctx and "target_line" in ctx:
            # Rename: produce a simple substitution diff
            old_name = ctx["old_name"]
            new_name = ctx["new_name"]
            target_line = ctx["target_line"]
            line_text = ctx.get("line_text", "")

            new_text = line_text.replace(old_name, new_name)
            return (
                f"{target_line + 1}-| {line_text}\n"
                f"{target_line + 1}+| {new_text}"
            )

        return ""


def create_backend(
    model_path: str | None = None,
    backend_type: str = "auto",
) -> InferenceBackend:
    """Factory function to create an inference backend.

    Parameters:
        model_path: Path to the model file. If None or empty, creates a DummyBackend.
        backend_type: "llama_cpp", "dummy", or "auto" (auto-detect from path).
    """
    if not model_path or backend_type == "dummy":
        logger.info("Using DummyBackend (no model path provided)")
        return DummyBackend()

    if backend_type == "llama_cpp" or backend_type == "auto":
        return LlamaCppBackend(model_path=model_path)

    raise ValueError(f"Unknown backend type: {backend_type}")

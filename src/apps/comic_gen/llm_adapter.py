"""
LLM Adapter - Unified interface for DashScope and OpenAI-compatible APIs.

Supports two providers:
  - dashscope (default): Alibaba Cloud DashScope via OpenAI-compatible endpoint
  - openai: Any OpenAI-compatible API (OpenAI, DeepSeek, Ollama, etc.)

Configuration via environment variables:
  LLM_PROVIDER=dashscope|openai
  DASHSCOPE_API_KEY=...
  OPENAI_API_KEY=...
  OPENAI_BASE_URL=https://api.openai.com/v1
  OPENAI_MODEL=gpt-4o
"""
import os
import logging
from typing import Dict, List, Optional, Any

from ...utils.endpoints import get_provider_base_url

logger = logging.getLogger(__name__)


class LLMAdapter:
    """Unified LLM call interface supporting DashScope and OpenAI-compatible APIs.

    Entry-layer auth (``src.auth``): when LUOXIA_AUTH_MODE=session and provider
    is xai_pool (or another session adapter), chat can use the session token
    against an OpenAI-compatible base URL — no pasted pay-per-call API key.
    """

    def __init__(self):
        self.provider = os.getenv("LLM_PROVIDER", "dashscope").lower()
        self._client = None
        self._client_key = None
        logger.info(f"LLM Adapter initialized with provider: {self.provider}")

    @property
    def is_configured(self) -> bool:
        try:
            from src.auth.config import load_auth_config
            from src.auth.resolver import resolve_credential

            cfg = load_auth_config()
            if cfg.mode == "offline":
                return False
            if cfg.mode == "session":
                try:
                    resolve_credential(config=cfg, purpose="llm")
                    return True
                except Exception:
                    return False
        except Exception:
            pass
        if self.provider == "openai":
            return bool(os.getenv("OPENAI_API_KEY"))
        return bool(os.getenv("DASHSCOPE_API_KEY"))

    def _get_client(self):
        """Get or create the OpenAI-compatible client (lazy, cached)."""
        api_key, base_url, cache_key = self._resolve_llm_transport()
        if self._client is None or self._client_key != cache_key:
            try:
                from openai import OpenAI
            except ImportError:
                raise RuntimeError(
                    "openai package not installed. Run: pip install openai>=1.0.0"
                )
            self._client = OpenAI(api_key=api_key, base_url=base_url)
            self._client_key = cache_key
        return self._client

    def _resolve_llm_transport(self):
        """Return (api_key, base_url, cache_key) via auth entry layer when applicable."""
        try:
            from src.auth.config import load_auth_config
            from src.auth.errors import AuthError, LoginRequiredError
            from src.auth.resolver import resolve_credential

            cfg = load_auth_config()
            if cfg.mode == "offline":
                raise RuntimeError(
                    "Auth mode is offline — LLM cloud calls disabled."
                )
            if cfg.mode == "session":
                try:
                    resolved = resolve_credential(config=cfg, purpose="llm")
                    token = resolved.credential.token
                    base = (
                        resolved.credential.base_url
                        or os.getenv("OPENAI_BASE_URL")
                        or "https://api.x.ai/v1"
                    )
                    # Session pool chat: force openai-compatible transport.
                    self.provider = "openai"
                    return token, base.rstrip("/"), f"session:{cfg.provider}:{token[:12]}"
                except LoginRequiredError as e:
                    raise RuntimeError(str(e)) from e
                except AuthError as e:
                    raise RuntimeError(str(e)) from e
        except RuntimeError:
            raise
        except Exception as e:
            logger.debug("auth resolver skipped for LLM: %s", e)

        if self.provider == "openai":
            key = os.getenv("OPENAI_API_KEY")
            if not key:
                raise RuntimeError(
                    "Need login for subscription pool, or set OPENAI_API_KEY / switch auth mode."
                )
            base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
            return key, base, f"openai:{key[:8]}"
        key = os.getenv("DASHSCOPE_API_KEY")
        if not key:
            raise RuntimeError(
                "Need login for subscription pool, or set DASHSCOPE_API_KEY / "
                "LUOXIA_AUTH_MODE=session + login."
            )
        base = f"{get_provider_base_url('DASHSCOPE')}/compatible-mode/v1"
        return key, base, f"dashscope:{key[:8]}"

    # DashScope qwen 系列：首选 qwen3.7-plus（最新），不可用时回退到 qwen3.6-plus，
    # 最终回退到 qwen-plus alias（始终指向最新稳定通用版）。
    # 维护 fallback chain 而不是硬写一个名字，避免新版本上下线时整条 LLM 链断掉。
    _DASHSCOPE_MODEL_FALLBACK_CHAIN = ["qwen3.7-plus", "qwen3.6-plus", "qwen-plus"]

    def _get_default_model(self) -> str:
        if self.provider == "openai":
            # Session pool (xAI) often uses grok-* chat models when OPENAI_MODEL unset.
            return os.getenv("OPENAI_MODEL") or os.getenv("LUOXIA_POOL_CHAT_MODEL") or "grok-4"
        return self._DASHSCOPE_MODEL_FALLBACK_CHAIN[0]

    def chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        response_format: Optional[Dict[str, str]] = None,
    ) -> str:
        """
        Send a chat completion request and return the response content.

        Args:
            messages: List of {"role": ..., "content": ...} dicts
            model: Model name override (uses provider default if None)
            response_format: Optional {"type": "json_object"} constraint

        Returns:
            The assistant's response content as a string.

        Raises:
            RuntimeError: If the API call fails.
        """
        client = self._get_client()

        # 显式 model override 路径：单次尝试，失败就抛。
        if model:
            return self._chat_once(client, model, messages, response_format)

        # Provider 默认路径：DashScope 走 fallback chain，OpenAI 单次尝试。
        if self.provider == "openai":
            return self._chat_once(client, self._get_default_model(), messages, response_format)

        last_err: Optional[Exception] = None
        for idx, candidate in enumerate(self._DASHSCOPE_MODEL_FALLBACK_CHAIN):
            try:
                return self._chat_once(client, candidate, messages, response_format)
            except RuntimeError as e:
                # 仅在 "模型不存在 / 不可用" 类错误时回退；其他错误（鉴权、限流、网络）
                # 直接抛，不浪费第二次重试。判定关键字宽松匹配 DashScope 文案。
                msg = str(e).lower()
                is_model_unavailable = any(k in msg for k in (
                    "model not found", "invalidmodel", "model_not_found",
                    "no such model", "not supported", "modelnotfound", "404",
                ))
                last_err = e
                if is_model_unavailable and idx < len(self._DASHSCOPE_MODEL_FALLBACK_CHAIN) - 1:
                    next_candidate = self._DASHSCOPE_MODEL_FALLBACK_CHAIN[idx + 1]
                    logger.warning(
                        "DashScope model %s unavailable (%s); falling back to %s",
                        candidate, e, next_candidate,
                    )
                    continue
                raise
        # 理论上不可达（最后一次失败已 raise），保留兜底
        raise last_err if last_err else RuntimeError("DashScope: no models available")

    def _chat_once(
        self,
        client,
        model: str,
        messages: List[Dict[str, str]],
        response_format: Optional[Dict[str, str]],
    ) -> str:
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        if response_format:
            kwargs["response_format"] = response_format

        try:
            response = client.chat.completions.create(**kwargs)
            return response.choices[0].message.content
        except Exception as e:
            provider_label = "DashScope" if self.provider != "openai" else "OpenAI"
            raise RuntimeError(f"{provider_label} API error: {e}") from e

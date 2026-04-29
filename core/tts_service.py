import asyncio
import base64
import mimetypes
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

import httpx


STYLE_TAG_RE = re.compile(r"^\s*<style>(.*?)</style>\s*", re.IGNORECASE | re.DOTALL)
AUDIO_STYLE_PREFIX_RE = re.compile(r"^\s*(?:\([^)]{1,80}\)|（[^）]{1,80}）|\[[^\]]{1,80}\])")
TTS_MODE_PRESET = "preset"
TTS_MODE_DESIGN = "design"
TTS_MODE_CLONE = "clone"
TTS_MODE_ALIASES = {
    "preset": TTS_MODE_PRESET,
    "built_in": TTS_MODE_PRESET,
    "builtin": TTS_MODE_PRESET,
    "内置": TTS_MODE_PRESET,
    "预置": TTS_MODE_PRESET,
    "design": TTS_MODE_DESIGN,
    "voice_design": TTS_MODE_DESIGN,
    "voicedesign": TTS_MODE_DESIGN,
    "描述": TTS_MODE_DESIGN,
    "定制": TTS_MODE_DESIGN,
    "设计": TTS_MODE_DESIGN,
    "clone": TTS_MODE_CLONE,
    "voice_clone": TTS_MODE_CLONE,
    "voiceclone": TTS_MODE_CLONE,
    "克隆": TTS_MODE_CLONE,
    "复刻": TTS_MODE_CLONE,
}
TTS_MODEL_BY_MODE = {
    TTS_MODE_PRESET: "mimo-v2.5-tts",
    TTS_MODE_DESIGN: "mimo-v2.5-tts-voicedesign",
    TTS_MODE_CLONE: "mimo-v2.5-tts-voiceclone",
}
MAX_CLONE_VOICE_BYTES = 10 * 1024 * 1024


@dataclass
class TtsStyleDecision:
    text: str
    voice: str
    style: str | None = None
    mode: str = TTS_MODE_PRESET
    voice_prompt: str | None = None
    voice_clone: str | None = None
    model: str | None = None


def extract_persona_tts_config(base_prompt: Any) -> dict[str, str]:
    if not isinstance(base_prompt, dict):
        return {}
    value = base_prompt.get("tts")
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for key in ("mode", "voice", "style", "user_hint", "voice_prompt", "voice_clone", "voice_clone_path", "model"):
        raw = value.get(key)
        if raw is None:
            continue
        text = str(raw).strip()
        if text:
            result[key] = text
    return result


class TtsService:
    def __init__(
        self,
        *,
        plugin_config: Any,
        logger: Any,
        get_http_client: Callable[[], httpx.AsyncClient],
        data_dir: Path,
        style_planner: Callable[[list[dict[str, Any]]], Any] | None = None,
    ) -> None:
        self.plugin_config = plugin_config
        self.logger = logger
        self.get_http_client = get_http_client
        self.cache_dir = data_dir / "tts_cache"
        self.style_planner = style_planner

    def is_enabled(self) -> bool:
        return bool(getattr(self.plugin_config, "personification_tts_enabled", False))

    def is_configured(self) -> bool:
        return bool(str(getattr(self.plugin_config, "personification_tts_api_key", "") or "").strip())

    def is_available(self) -> bool:
        return self.is_enabled() and self.is_configured()

    def is_style_planner_enabled(self) -> bool:
        return False

    def is_group_auto_enabled(self, group_config: dict[str, Any] | None = None) -> bool:
        config = group_config or {}
        return bool(
            config.get(
                "tts_enabled",
                getattr(self.plugin_config, "personification_tts_group_default_enabled", True),
            )
        )

    def should_auto_tts(
        self,
        *,
        is_private: bool,
        group_config: dict[str, Any] | None,
        text: str,
        has_rich_content: bool = False,
    ) -> bool:
        if not self.is_available():
            return False
        if not getattr(self.plugin_config, "personification_tts_auto_enabled", True):
            return False
        if has_rich_content:
            return False
        if not str(text or "").strip():
            return False
        if is_private and getattr(self.plugin_config, "personification_tts_private_force_auto", False):
            return True
        if not is_private and not self.is_group_auto_enabled(group_config):
            return False
        probability = float(getattr(self.plugin_config, "personification_tts_auto_probability", 0.2) or 0.0)
        if probability <= 0:
            return False
        return random.random() < probability

    def split_text(self, text: str) -> list[str]:
        raw = str(text or "").strip()
        if not raw:
            return []

        max_chars = int(getattr(self.plugin_config, "personification_tts_max_chars_per_segment", 120) or 120)
        if max_chars <= 0 or len(raw) <= max_chars:
            return [raw]

        chunks = re.split(r"([。！？!?；;，,\n])", raw)
        segments: list[str] = []
        buffer = ""
        for chunk in chunks:
            if not chunk:
                continue
            if len(buffer) + len(chunk) <= max_chars:
                buffer += chunk
                continue
            if buffer.strip():
                segments.append(buffer.strip())
            buffer = chunk
            while len(buffer) > max_chars:
                cut = self._safe_split_index(buffer, max_chars)
                segments.append(buffer[:cut].strip())
                buffer = buffer[cut:]
        if buffer.strip():
            segments.append(buffer.strip())
        return self._merge_punctuation_only_segments(segments)

    def infer_style_decision(
        self,
        text: str,
        *,
        mode_hint: str | None = None,
        voice_hint: str | None = None,
        style_hint: str | None = None,
        voice_prompt_hint: str | None = None,
        voice_clone_hint: str | None = None,
        voice_clone_path_hint: str | None = None,
        model_hint: str | None = None,
        user_hint: str | None = None,
        is_private: bool = False,
        group_style: str | None = None,
        persona_tts: dict[str, str] | None = None,
    ) -> TtsStyleDecision:
        _ = user_hint, is_private, group_style
        persona_config = persona_tts or {}
        explicit_style, clean_text = self._extract_explicit_style(text)
        persona_voice = str(persona_config.get("voice", "") or "").strip() or None
        persona_style = str(persona_config.get("style", "") or "").strip() or None
        final_mode = self._normalize_mode(
            mode_hint
            or str(persona_config.get("mode", "") or "").strip()
            or getattr(self.plugin_config, "personification_tts_mode", TTS_MODE_PRESET)
        )
        final_style = (style_hint or explicit_style or persona_style or "").strip() or None
        final_voice = (
            voice_hint
            or persona_voice
            or str(getattr(self.plugin_config, "personification_tts_default_voice", "mimo_default") or "").strip()
            or "mimo_default"
        ).strip()
        voice_prompt = (
            voice_prompt_hint
            or str(persona_config.get("voice_prompt", "") or "").strip()
            or str(getattr(self.plugin_config, "personification_tts_voice_design_prompt", "") or "").strip()
            or None
        )
        voice_clone = (
            voice_clone_hint
            or str(persona_config.get("voice_clone", "") or "").strip()
            or str(getattr(self.plugin_config, "personification_tts_voice_clone", "") or "").strip()
            or None
        )
        voice_clone_path = (
            voice_clone_path_hint
            or str(persona_config.get("voice_clone_path", "") or "").strip()
            or str(getattr(self.plugin_config, "personification_tts_voice_clone_path", "") or "").strip()
            or None
        )
        if final_mode == TTS_MODE_CLONE and not voice_clone and str(final_voice or "").startswith("data:"):
            voice_clone = final_voice
        if final_mode == TTS_MODE_CLONE and not voice_clone:
            voice_clone = self._load_clone_voice_data_url(voice_clone_path)
        model = (
            model_hint
            or str(persona_config.get("model", "") or "").strip()
            or str(getattr(self.plugin_config, "personification_tts_model", "") or "").strip()
            or TTS_MODEL_BY_MODE[final_mode]
        )
        if model in {"mimo-v2-tts", "mimo-v2.5-tts", "mimo-v2.5-tts-voicedesign", "mimo-v2.5-tts-voiceclone"}:
            if model == "mimo-v2-tts":
                model = "mimo-v2.5-tts"
            if final_mode != self._mode_from_model(model):
                model = TTS_MODEL_BY_MODE[final_mode]
        return TtsStyleDecision(
            text=clean_text.strip(),
            voice=final_voice,
            style=final_style,
            mode=final_mode,
            voice_prompt=voice_prompt,
            voice_clone=voice_clone,
            model=model,
        )

    async def synthesize(
        self,
        text: str,
        *,
        mode_hint: str | None = None,
        voice_hint: str | None = None,
        style_hint: str | None = None,
        voice_prompt_hint: str | None = None,
        voice_clone_hint: str | None = None,
        voice_clone_path_hint: str | None = None,
        model_hint: str | None = None,
        user_hint: str | None = None,
        is_private: bool = False,
        group_style: str | None = None,
        persona_tts: dict[str, str] | None = None,
    ) -> list[Path]:
        if not self.is_available():
            raise RuntimeError("TTS 未启用或未配置 personification_tts_api_key")

        persona_config = persona_tts or {}
        final_user_hint = user_hint or str(persona_config.get("user_hint", "") or "").strip() or None
        decision = self.infer_style_decision(
            text,
            mode_hint=mode_hint,
            voice_hint=voice_hint,
            style_hint=style_hint,
            voice_prompt_hint=voice_prompt_hint,
            voice_clone_hint=voice_clone_hint,
            voice_clone_path_hint=voice_clone_path_hint,
            model_hint=model_hint,
            user_hint=final_user_hint,
            is_private=is_private,
            group_style=group_style,
            persona_tts=persona_config,
        )
        if not decision.text:
            return []

        self._cleanup_old_files()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        output_files: list[Path] = []
        for segment in self.split_text(decision.text):
            audio_path = await self._synthesize_segment(
                segment,
                voice=decision.voice,
                style=decision.style,
                mode=decision.mode,
                voice_prompt=decision.voice_prompt,
                voice_clone=decision.voice_clone,
                model=decision.model,
                user_hint=final_user_hint,
            )
            output_files.append(audio_path)
        return output_files

    async def send_tts(
        self,
        *,
        bot: Any,
        event: Any,
        message_segment_cls: Any,
        text: str,
        mode_hint: str | None = None,
        voice_hint: str | None = None,
        style_hint: str | None = None,
        voice_prompt_hint: str | None = None,
        voice_clone_hint: str | None = None,
        voice_clone_path_hint: str | None = None,
        model_hint: str | None = None,
        user_hint: str | None = None,
        is_private: bool = False,
        group_style: str | None = None,
        persona_tts: dict[str, str] | None = None,
        pause_range: tuple[float, float] = (0.8, 1.4),
    ) -> bool:
        audio_files = await self.synthesize(
            text,
            mode_hint=mode_hint,
            voice_hint=voice_hint,
            style_hint=style_hint,
            voice_prompt_hint=voice_prompt_hint,
            voice_clone_hint=voice_clone_hint,
            voice_clone_path_hint=voice_clone_path_hint,
            model_hint=model_hint,
            user_hint=user_hint,
            is_private=is_private,
            group_style=group_style,
            persona_tts=persona_tts,
        )
        if not audio_files:
            return False

        for index, audio_file in enumerate(audio_files):
            await bot.send(event, message_segment_cls.record(audio_file.absolute().as_uri()))
            if index < len(audio_files) - 1:
                await asyncio.sleep(random.uniform(*pause_range))
        return True

    async def _synthesize_segment(
        self,
        text: str,
        *,
        voice: str,
        style: str | None,
        mode: str,
        voice_prompt: str | None,
        voice_clone: str | None,
        model: str | None,
        user_hint: str | None = None,
    ) -> Path:
        payload = self._build_payload(
            text,
            mode=mode,
            model=model,
            voice=voice,
            style=style,
            user_hint=user_hint,
            voice_prompt=voice_prompt,
            voice_clone=voice_clone,
        )
        api_url = str(getattr(self.plugin_config, "personification_tts_api_url", "https://api.xiaomimimo.com/v1") or "https://api.xiaomimimo.com/v1").rstrip("/")
        api_key = str(getattr(self.plugin_config, "personification_tts_api_key", "") or "").strip()
        client = self.get_http_client()

        try:
            response = await client.post(
                f"{api_url}/chat/completions",
                headers={
                    "api-key": api_key,
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=float(getattr(self.plugin_config, "personification_tts_timeout", 60) or 60),
            )
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            self.logger.warning(f"[tts] MiMo 请求失败: {e}")
            raise

        try:
            audio_data = data["choices"][0]["message"]["audio"]["data"]
            audio_bytes = base64.b64decode(audio_data)
        except Exception as e:
            raise RuntimeError(f"MiMo 返回音频数据解析失败: {e}") from e

        file_ext = str(getattr(self.plugin_config, "personification_tts_default_format", "wav") or "wav").lower()
        target = self.cache_dir / f"tts_{int(time.time() * 1000)}_{random.randint(1000, 9999)}.{file_ext}"
        target.write_bytes(audio_bytes)
        return target

    def _build_payload(
        self,
        text: str,
        *,
        mode: str,
        model: str | None,
        voice: str,
        style: str | None,
        user_hint: str | None,
        voice_prompt: str | None,
        voice_clone: str | None,
    ) -> dict[str, Any]:
        resolved_mode = self._normalize_mode(mode)
        resolved_model = str(model or "").strip() or TTS_MODEL_BY_MODE[resolved_mode]
        audio: dict[str, str] = {
            "format": str(getattr(self.plugin_config, "personification_tts_default_format", "wav") or "wav"),
        }
        if resolved_mode == TTS_MODE_PRESET:
            audio["voice"] = str(voice or "").strip() or "mimo_default"
        elif resolved_mode == TTS_MODE_CLONE:
            if not voice_clone:
                raise RuntimeError("TTS 克隆模式需要配置 personification_tts_voice_clone 或 personification_tts_voice_clone_path")
            audio["voice"] = voice_clone

        return {
            "model": resolved_model,
            "messages": self._build_messages(
                text,
                mode=resolved_mode,
                style=style,
                user_hint=user_hint,
                voice_prompt=voice_prompt,
            ),
            "audio": audio,
        }

    def _build_messages(
        self,
        text: str,
        *,
        mode: str,
        style: str | None,
        user_hint: str | None,
        voice_prompt: str | None,
    ) -> list[dict[str, str]]:
        content = text.strip()
        style_value = str(style or "").strip()
        if style_value:
            normalized_style = style_value
            if STYLE_TAG_RE.match(content):
                content = STYLE_TAG_RE.sub("", content, count=1).strip()
            if not AUDIO_STYLE_PREFIX_RE.match(content):
                content = f"({normalized_style}){content}"

        messages: list[dict[str, str]] = []
        hint = str(user_hint or "").strip()
        if mode == TTS_MODE_DESIGN:
            prompt = str(voice_prompt or "").strip()
            if not prompt:
                raise RuntimeError("TTS 音色设计模式需要配置 voice_prompt")
            if hint:
                prompt = f"{prompt}\n朗读要求：{hint}"
            messages.append({"role": "user", "content": prompt})
        elif hint or mode == TTS_MODE_CLONE:
            messages.append({"role": "user", "content": hint})
        messages.append({"role": "assistant", "content": content})
        return messages

    def _extract_explicit_style(self, text: str) -> tuple[str | None, str]:
        raw = str(text or "")
        match = STYLE_TAG_RE.match(raw)
        if not match:
            return None, raw
        return match.group(1).strip(), raw[match.end():]

    def _normalize_mode(self, value: Any) -> str:
        raw = str(value or "").strip().lower().replace("-", "_")
        return TTS_MODE_ALIASES.get(raw, TTS_MODE_PRESET)

    def _mode_from_model(self, model: str) -> str:
        normalized = str(model or "").strip().lower()
        if normalized.endswith("voiceclone"):
            return TTS_MODE_CLONE
        if normalized.endswith("voicedesign"):
            return TTS_MODE_DESIGN
        return TTS_MODE_PRESET

    def _load_clone_voice_data_url(self, path_or_data: str | None) -> str | None:
        raw = str(path_or_data or "").strip()
        if not raw:
            return None
        if raw.startswith("data:"):
            return raw
        path = Path(raw).expanduser()
        if not path.is_file():
            return None
        size = path.stat().st_size
        if size > MAX_CLONE_VOICE_BYTES:
            raise RuntimeError("TTS 克隆音频样本不能超过 10 MB")
        mime_type = mimetypes.guess_type(path.name)[0] or ""
        if mime_type in {"audio/mp3", "audio/mpeg"}:
            mime_type = "audio/mpeg"
        elif mime_type != "audio/wav":
            suffix = path.suffix.lower()
            if suffix == ".mp3":
                mime_type = "audio/mpeg"
            elif suffix == ".wav":
                mime_type = "audio/wav"
            else:
                raise RuntimeError("TTS 克隆音频样本仅支持 mp3 或 wav")
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    def _safe_split_index(self, text: str, max_chars: int) -> int:
        punctuations = "。！？!?；;，,\n "
        for index in range(max_chars, max(1, max_chars // 2), -1):
            if text[index - 1] in punctuations:
                return index
        return max_chars

    def _cleanup_old_files(self, *, max_age_seconds: int = 86400) -> None:
        if not self.cache_dir.exists():
            return
        now_ts = time.time()
        for file_path in self.cache_dir.iterdir():
            try:
                if not file_path.is_file():
                    continue
                if now_ts - file_path.stat().st_mtime > max_age_seconds:
                    file_path.unlink(missing_ok=True)
            except Exception:
                continue

    def _merge_punctuation_only_segments(self, segments: Sequence[str]) -> list[str]:
        merged: list[str] = []
        for segment in segments:
            value = str(segment or "").strip()
            if not value:
                continue
            if all(ch in "。！？!?；;，,\n " for ch in value) and merged:
                merged[-1] = f"{merged[-1]}{value}"
                continue
            merged.append(value)
        return merged

__all__ = ["TtsService", "TtsStyleDecision", "extract_persona_tts_config"]

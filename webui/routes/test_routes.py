from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Query

from ...core.operation_diagnostics import detail, diagnostic, exception_diagnostic, step
from ...core.sensitive_data import sanitize_text
from ..deps import AdminIdentity, require_admin

_TEST_ALL_TIMEOUT_SECONDS = 40
_PROMPT_PREVIEW_MAX_BYTES = 256 * 1024
_DIAGNOSTIC_FIELDS = (
    "ok",
    "code",
    "phase",
    "title",
    "message",
    "details",
    "steps",
    "warnings",
    "suggestion",
    "retryable",
    "partial",
    "outcome_unknown",
    "operation_id",
    "trace_id",
)


def _attach_diagnostic(payload: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["diagnostic"] = report
    for field in _DIAGNOSTIC_FIELDS:
        result.setdefault(field, report[field])
    return result


def _provider_details(provider: dict[str, Any] | None) -> tuple:
    item = provider or {}
    return (
        detail("Provider", str(item.get("name") or "路由模型")),
        detail("API type", str(item.get("api_type") or "routed")),
        detail("Model", str(item.get("model") or "由路由决定")),
    )


def _exception_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(chain) < 6:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def _exception_status(chain: list[BaseException]) -> int:
    for item in chain:
        values = (
            getattr(item, "status_code", None),
            getattr(getattr(item, "response", None), "status_code", None),
        )
        for value in values:
            try:
                status = int(value or 0)
            except (TypeError, ValueError):
                continue
            if status:
                return status
    return 0


def _provider_failure_report(
    exc: BaseException,
    *,
    provider: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    chain = _exception_chain(exc)
    status = _exception_status(chain)
    class_names = " ".join(type(item).__name__.lower() for item in chain)
    # Exception text is used only for classification. It is never copied into the response.
    internal_text = " ".join(str(item).lower() for item in chain)
    if status in {401, 403} or any(
        marker in class_names
        for marker in ("authentication", "permissiondenied", "unauthorized", "forbidden")
    ) or any(
        marker in internal_text
        for marker in ("unauthorized", "forbidden", "invalid api key", "authentication failed", "credentials", "access token 为空")
    ):
        response_status = 502
        code = "provider_test_auth_failed"
        phase = "provider_auth"
        title = "Provider 认证未通过"
        message = "Provider 拒绝了本次模型测试的认证信息。"
        suggestion = "检查 API Key、OAuth/CLI 登录状态和模型访问权限后重试。"
        retryable = False
    elif any(isinstance(item, (asyncio.TimeoutError, TimeoutError, httpx.TimeoutException)) for item in chain) or (
        "timeout" in class_names or "timed out" in internal_text
    ):
        response_status = 504
        code = "provider_test_timeout"
        phase = "provider_request"
        title = "Provider 模型测试超时"
        message = "等待 Provider 返回模型响应超时，未取得可用结果。"
        suggestion = "检查 Provider 状态和网络延迟，必要时提高该 Provider 的 timeout。"
        retryable = True
    elif any(isinstance(item, (httpx.RequestError, ConnectionError)) for item in chain) or any(
        marker in class_names
        for marker in ("connectionerror", "connecterror", "networkerror", "protocolerror", "dnserror")
    ) or any(
        marker in internal_text
        for marker in ("connection reset", "connection refused", "server disconnected", "dns", "tls", "proxy error")
    ):
        response_status = 502
        code = "provider_test_network_failed"
        phase = "provider_request"
        title = "Provider 网络连接失败"
        message = "服务器无法稳定连接 Provider，未取得模型响应。"
        suggestion = "检查 DNS、代理、TLS 与服务器出站网络后重试。"
        retryable = True
    elif any(isinstance(item, (ValueError, UnicodeDecodeError)) for item in chain) or any(
        marker in class_names for marker in ("jsondecode", "parseerror", "decodingerror")
    ) or any(
        marker in internal_text
        for marker in ("response parse", "响应解析", "非 json 响应", "流式响应解析失败")
    ):
        response_status = 502
        code = "provider_test_parse_failed"
        phase = "response_parse"
        title = "Provider 响应无法解析"
        message = "Provider 返回内容不符合当前模型调用协议。"
        suggestion = "确认 API type、endpoint 和模型协议匹配后重试。"
        retryable = False
    else:
        response_status = 500
        code = "provider_test_internal_error"
        phase = "provider_test"
        title = "Provider 模型测试异常中断"
        message = "模型测试发生内部异常，未向客户端返回原始异常或 Provider 输出。"
        suggestion = "请根据 Trace ID 查看脱敏日志并检查 Provider 配置。"
        retryable = True

    report = exception_diagnostic(
        exc,
        phase=phase,
        title=title,
        message=message,
        suggestion=suggestion,
        retryable=retryable,
    )
    report["code"] = code
    report["phase"] = phase
    report["title"] = title
    report["message"] = message
    report["error"] = message
    report["details"] = [
        *(item.to_dict() for item in _provider_details(provider)),
        detail("异常类型", type(exc).__name__, "error").to_dict(),
        *([detail("HTTP status", status, "error").to_dict()] if status else []),
    ]
    report["steps"] = [
        step("prepare_request", "准备模型测试请求", "ok", "测试消息已完成本地校验。", details=_provider_details(provider)).to_dict(),
        step("provider_request", "调用 Provider", "error", message).to_dict(),
        step("response_parse", "解析模型响应", "skipped", "Provider 调用阶段未通过。").to_dict(),
    ]
    return response_status, report


def _log_provider_failure(runtime: Any, exc: BaseException, report: dict[str, Any]) -> None:
    logger = getattr(runtime, "logger", None)
    if logger is None:
        return
    try:
        logger.warning(
            f"[webui] provider test failed: {type(exc).__name__} "
            f"code={report.get('code', '')} trace={report.get('trace_id', '')}"
        )
    except Exception:
        pass


def _response_payload(response: Any, *, duration_ms: int) -> dict[str, Any]:
    usage_obj = getattr(response, "usage", None) or {}
    if not isinstance(usage_obj, dict):
        usage_obj = {
            "prompt_tokens": getattr(usage_obj, "prompt_tokens", 0),
            "completion_tokens": getattr(usage_obj, "completion_tokens", 0),
            "total_tokens": getattr(usage_obj, "total_tokens", 0),
        }
    return {
        "content": str(getattr(response, "content", "") or ""),
        "finish_reason": str(getattr(response, "finish_reason", "") or ""),
        "duration_ms": duration_ms,
        "usage": {
            "prompt_tokens": int(usage_obj.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(usage_obj.get("completion_tokens", 0) or 0),
            "total_tokens": int(usage_obj.get("total_tokens", 0) or 0),
        },
        "model_used": str(getattr(response, "model_used", "") or ""),
    }


def _provider_response_report(
    payload: dict[str, Any],
    *,
    provider: dict[str, Any] | None,
    blocked_reason: str = "",
) -> dict[str, Any]:
    provider_info = _provider_details(provider)
    if blocked_reason:
        return diagnostic(
            ok=False,
            code="provider_test_blocked",
            phase="provider_policy",
            title="Provider 响应被安全策略拦截",
            message="Provider 返回了结构化安全拦截信号，本次没有可用模型内容。",
            details=provider_info,
            steps=(
                step("prepare_request", "准备模型测试请求", "ok", "测试消息已完成本地校验。"),
                step("provider_request", "调用 Provider", "ok", "Provider 已返回响应。"),
                step("provider_policy", "检查 Provider 拦截状态", "error", "响应包含安全拦截信号。"),
                step("response_parse", "解析模型响应", "skipped", "安全拦截响应不作为模型内容展示。"),
            ),
            suggestion="检查请求内容和 Provider safety policy；不要通过重复提交尝试绕过安全策略。",
            retryable=False,
        )
    if not str(payload.get("content") or "").strip():
        return diagnostic(
            ok=False,
            code="provider_test_model_empty",
            phase="model_output",
            title="Provider 返回空模型内容",
            message="Provider 请求完成，但响应中没有可展示的模型文本。",
            details=provider_info,
            steps=(
                step("prepare_request", "准备模型测试请求", "ok", "测试消息已完成本地校验。"),
                step("provider_request", "调用 Provider", "ok", "Provider 已返回响应。"),
                step("provider_policy", "检查 Provider 拦截状态", "ok", "未发现结构化安全拦截信号。"),
                step("response_parse", "解析模型响应", "warn", "响应可读取，但模型文本为空。"),
            ),
            suggestion="检查模型 ID、finish reason、Provider quota 和 endpoint 协议。",
            retryable=True,
        )
    return diagnostic(
        ok=True,
        code="provider_test_complete",
        phase="operation_complete",
        title="Provider 模型测试完成",
        message="Provider 已返回可用模型内容。",
        details=(
            *provider_info,
            detail("响应耗时", f"{int(payload.get('duration_ms') or 0)} ms", "ok"),
            detail("实际模型", str(payload.get("model_used") or "未报告"), "ok"),
        ),
        steps=(
            step("prepare_request", "准备模型测试请求", "ok", "测试消息已完成本地校验。"),
            step("provider_request", "调用 Provider", "ok", "Provider 已返回响应。"),
            step("provider_policy", "检查 Provider 拦截状态", "ok", "未发现结构化安全拦截信号。"),
            step("response_parse", "解析模型响应", "ok", "已取得非空模型文本。"),
        ),
    )


def _persona_payload(
    payload: dict[str, Any],
    *,
    ok: bool,
    code: str,
    title: str,
    message: str,
    phase: str,
    suggestion: str = "",
    retryable: bool = False,
) -> dict[str, Any]:
    source = str(payload.get("source") or "未标记")
    is_file = bool(payload.get("is_file"))
    report = diagnostic(
        ok=ok,
        code=code,
        phase=phase,
        title=title,
        message=message,
        details=(
            detail("来源", source),
            detail("载入方式", "file" if is_file else "inline" if payload.get("exists") else "path_lookup"),
            detail("字节数", int(payload.get("size") or 0), "ok" if ok else "warn"),
        ),
        steps=(
            step("resolve_source", "解析人设 prompt 来源", "ok", "已确定 prompt 来源。"),
            step(
                "read_prompt",
                "读取人设 prompt",
                "ok" if ok else "error",
                message,
            ),
        ),
        suggestion=suggestion,
        retryable=retryable,
    )
    return _attach_diagnostic(payload, report)


def _tool_caller(runtime) -> Any | None:
    bundle = getattr(runtime, "runtime_bundle", None)
    if bundle is None:
        return None
    # 优先 reply_processor_deps.runtime.agent_tool_caller，其次 bundle.agent_tool_caller
    deps = getattr(bundle, "reply_processor_deps", None)
    if deps is not None:
        runtime_inner = getattr(deps, "runtime", None)
        if runtime_inner is not None:
            caller = getattr(runtime_inner, "agent_tool_caller", None)
            if caller is not None:
                return caller
    return getattr(bundle, "agent_tool_caller", None)


def build_test_router(*, runtime) -> APIRouter:
    router = APIRouter(prefix="/api/test", tags=["test"])

    @router.post("/chat")
    async def test_chat(
        body: dict = Body(default_factory=dict),
        _: AdminIdentity = Depends(require_admin),
    ) -> dict:
        prompt = str(body.get("prompt", "") or "").strip()
        if not prompt:
            raise HTTPException(
                status_code=400,
                detail=diagnostic(
                    ok=False,
                    code="provider_test_prompt_required",
                    phase="request_validation",
                    title="模型测试缺少用户消息",
                    message="prompt 不能为空。",
                    steps=(step("prepare_request", "校验模型测试请求", "error", "没有可发送的用户消息。"),),
                    suggestion="填写用户消息后重新发送。",
                    retryable=True,
                ),
            )
        system = str(body.get("system", "") or "你是测试助手，简洁回复。")
        use_builtin_search = bool(body.get("use_builtin_search", False))
        caller = _tool_caller(runtime)
        if caller is None:
            raise HTTPException(
                status_code=503,
                detail=diagnostic(
                    ok=False,
                    code="provider_test_caller_unavailable",
                    phase="runtime_preflight",
                    title="模型调用器未就绪",
                    message="当前 runtime 没有可用的 agent_tool_caller，尚未发起 Provider 请求。",
                    steps=(
                        step("prepare_request", "校验模型测试请求", "ok", "测试消息有效。"),
                        step("resolve_caller", "读取模型调用器", "error", "agent_tool_caller 不可用。"),
                    ),
                    suggestion="等待插件 runtime 初始化完成，或检查 Agent/Provider 配置后重试。",
                    retryable=True,
                ),
            )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        started = time.time()
        try:
            response = await caller.chat_with_tools(
                messages=messages,
                tools=[],
                use_builtin_search=use_builtin_search,
            )
        except Exception as exc:
            status_code, report = _provider_failure_report(exc)
            _log_provider_failure(runtime, exc, report)
            raise HTTPException(status_code=status_code, detail=report) from exc
        duration_ms = int((time.time() - started) * 1000)
        try:
            payload = _response_payload(response, duration_ms=duration_ms)
            from ...core.safety_filter import detect_api_block

            blocked = sanitize_text(detect_api_block(response), limit=200)
        except Exception as exc:
            status_code, report = _provider_failure_report(exc)
            _log_provider_failure(runtime, exc, report)
            raise HTTPException(status_code=status_code, detail=report) from exc
        report = _provider_response_report(payload, provider=None, blocked_reason=blocked)
        if blocked:
            payload["blocked_reason"] = blocked
            payload["error"] = report["message"]
        elif not report["ok"]:
            payload["error"] = report["message"]
        return _attach_diagnostic(payload, report)

    @router.post("/chat-all")
    async def test_chat_all(
        body: dict = Body(default_factory=dict),
        _: AdminIdentity = Depends(require_admin),
    ) -> dict:
        """向当前配置的每一个 provider 各发一次测试，返回各自内容与延迟。"""
        prompt = str(body.get("prompt", "") or "").strip()
        if not prompt:
            raise HTTPException(
                status_code=400,
                detail=diagnostic(
                    ok=False,
                    code="provider_test_prompt_required",
                    phase="request_validation",
                    title="Provider 测试缺少用户消息",
                    message="prompt 不能为空。",
                    steps=(step("prepare_request", "校验 Provider 测试请求", "error", "没有可发送的用户消息。"),),
                    suggestion="填写用户消息后重新测试。",
                    retryable=True,
                ),
            )
        system = str(body.get("system", "") or "你是测试助手，简洁回复。")
        plugin_config = getattr(runtime, "plugin_config", None)
        logger = getattr(runtime, "logger", None)
        if plugin_config is None:
            raise HTTPException(
                status_code=503,
                detail=diagnostic(
                    ok=False,
                    code="provider_test_runtime_unavailable",
                    phase="runtime_preflight",
                    title="Provider 测试运行时未就绪",
                    message="当前 runtime 没有可用的 plugin_config。",
                    steps=(step("runtime_preflight", "检查 Provider 测试运行时", "error", "plugin_config 不可用。"),),
                    suggestion="等待插件启动完成后重试。",
                    retryable=True,
                ),
            )

        from ...core import ai_routes
        from ...core.safety_filter import detect_api_block

        providers = ai_routes.list_primary_providers(plugin_config, logger)
        if not providers:
            raise HTTPException(
                status_code=400,
                detail=diagnostic(
                    ok=False,
                    code="provider_test_no_providers",
                    phase="provider_discovery",
                    title="没有可测试的 Provider",
                    message="当前配置没有可用的主 Provider。",
                    steps=(
                        step("prepare_request", "校验 Provider 测试请求", "ok", "测试消息有效。"),
                        step("provider_discovery", "读取主 Provider 列表", "error", "没有发现可用 Provider。"),
                    ),
                    suggestion="在配置中心填写 api_pools 或 legacy primary Provider 后重试。",
                    retryable=False,
                ),
            )

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]

        async def _probe(index: int, provider: dict) -> dict:
            name = str(provider.get("name", "") or "") or f"{provider.get('api_type', '')}:{provider.get('model', '')}"
            base = {
                "index": index,
                "name": name,
                "api_type": str(provider.get("api_type", "") or ""),
                "model": str(provider.get("model", "") or ""),
                "priority": int(provider.get("priority", index) or 0),
            }
            started = time.time()
            try:
                caller = ai_routes.build_single_provider_caller(plugin_config, provider)
                response = await asyncio.wait_for(
                    caller.chat_with_tools(messages=messages, tools=[], use_builtin_search=False),
                    timeout=_TEST_ALL_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                _status_code, report = _provider_failure_report(exc, provider=provider)
                _log_provider_failure(runtime, exc, report)
                base.update(duration_ms=int((time.time() - started) * 1000), error=report["message"])
                return _attach_diagnostic(base, report)
            duration_ms = int((time.time() - started) * 1000)
            try:
                response_payload = _response_payload(response, duration_ms=duration_ms)
                blocked = sanitize_text(detect_api_block(response), limit=200)
            except Exception as exc:
                _status_code, report = _provider_failure_report(exc, provider=provider)
                _log_provider_failure(runtime, exc, report)
                base.update(duration_ms=duration_ms, error=report["message"])
                return _attach_diagnostic(base, report)
            report = _provider_response_report(response_payload, provider=provider, blocked_reason=blocked)
            base.update(
                duration_ms=duration_ms,
                content=str(response_payload["content"])[:2000],
                finish_reason=response_payload["finish_reason"],
                blocked_reason=blocked,
                model_used=response_payload["model_used"],
                error="" if report["ok"] else report["message"],
            )
            return _attach_diagnostic(base, report)

        results = await asyncio.gather(*[_probe(i, p) for i, p in enumerate(providers)])
        success_count = sum(1 for item in results if item.get("ok") is True)
        failed_count = len(results) - success_count
        partial = bool(success_count and failed_count)
        retryable = any(bool((item.get("diagnostic") or {}).get("retryable")) for item in results if not item.get("ok"))
        if not failed_count:
            code = "provider_test_all_complete"
            title = "全部 Provider 测试完成"
            message = f"{success_count} 个 Provider 均返回了可用模型内容。"
        elif partial:
            code = "provider_test_all_partial"
            title = "部分 Provider 测试未通过"
            message = f"{success_count} 个 Provider 通过，{failed_count} 个未通过。"
        else:
            code = "provider_test_all_failed"
            title = "全部 Provider 测试未通过"
            message = f"{failed_count} 个 Provider 均未返回可用模型内容。"
        report = diagnostic(
            ok=not failed_count,
            code=code,
            phase="operation_complete",
            title=title,
            message=message,
            details=(
                detail("Provider 总数", len(results)),
                detail("通过", success_count, "ok" if success_count else "info"),
                detail("未通过", failed_count, "warn" if partial else "error" if failed_count else "ok"),
            ),
            steps=tuple(
                step(
                    f"provider_{int(item.get('index') or 0)}",
                    f"测试 Provider · {item.get('name') or '未命名'}",
                    "ok" if item.get("ok") else "error",
                    str((item.get("diagnostic") or {}).get("message") or "测试完成。"),
                    details=(detail("诊断 code", str((item.get("diagnostic") or {}).get("code") or "unknown")),),
                )
                for item in results
            ),
            suggestion="展开每个 Provider 的 diagnostic，按 code 和阶段逐项排查。" if failed_count else "无需处理。",
            retryable=retryable,
            partial=partial,
        )
        return _attach_diagnostic({"count": len(results), "results": list(results)}, report)

    @router.get("/persona-prompt")
    async def persona_prompt_preview(
        path: str = Query(default=""),
        _: AdminIdentity = Depends(require_admin),
    ) -> dict:
        """预览人设提示词文件内容。

        不传 path 时预览当前配置生效的人设文件（prompt_path / system_path /
        system_prompt 指向的文件）；传 path 时预览该指定路径（管理员操作，文本只读）。
        """
        plugin_config = getattr(runtime, "plugin_config", None)
        if plugin_config is None:
            raise HTTPException(
                status_code=503,
                detail=diagnostic(
                    ok=False,
                    code="persona_prompt_runtime_unavailable",
                    phase="runtime_preflight",
                    title="人设 prompt 预览运行时未就绪",
                    message="当前 runtime 没有可用的 plugin_config。",
                    steps=(step("runtime_preflight", "检查人设 prompt 运行时", "error", "plugin_config 不可用。"),),
                    suggestion="等待插件启动完成后重试。",
                    retryable=True,
                ),
            )

        from ...core.prompt_loader import _resolve_candidate_path

        def _inline(text: str, src: str) -> dict:
            return _persona_payload(
                {
                    "exists": True,
                    "is_file": False,
                    "source": src,
                    "resolved_path": "",
                    "content": text,
                    "size": len(text.encode("utf-8")),
                },
                ok=True,
                code="persona_prompt_inline_loaded",
                phase="operation_complete",
                title="内联人设 prompt 已载入",
                message="已从当前运行时配置读取内联 system_prompt。",
            )

        def _safe_is_file(p: Any) -> bool:
            try:
                return p.is_file()
            except OSError:  # 文件名过长等 → 视为非文件
                return False

        requested = str(path or "").strip().strip('"').strip("'")
        source = "指定路径"
        inline_fallback = ""  # 若按路径解析不到文件，回退展示的内联文本
        if not requested:
            requested = str(
                getattr(plugin_config, "personification_prompt_path", "")
                or getattr(plugin_config, "personification_system_path", "")
                or ""
            ).strip().strip('"').strip("'")
            source = "prompt_path / system_path"
            if not requested:
                raw_sp = str(getattr(plugin_config, "personification_system_prompt", "") or "").strip()
                if not raw_sp:
                    raise HTTPException(
                        status_code=404,
                        detail=diagnostic(
                            ok=False,
                            code="persona_prompt_not_configured",
                            phase="source_resolution",
                            title="没有可预览的人设 prompt",
                            message="未配置人设文件路径，且 system_prompt 为空。",
                            steps=(step("resolve_source", "解析人设 prompt 来源", "error", "没有找到路径或内联文本。"),),
                            suggestion="在配置中心设置 prompt_path、system_path 或 system_prompt。",
                            retryable=False,
                        ),
                    )
                # system_prompt 较短时可能是一个文件路径；解析不到文件则按内联文本展示
                if len(raw_sp) < 260:
                    requested = raw_sp.strip('"').strip("'")
                    source = "system_prompt（按路径解析）"
                    inline_fallback = raw_sp
                else:
                    return _inline(raw_sp, "system_prompt（内联文本）")

        try:
            resolved = _resolve_candidate_path(requested)
            is_file = _safe_is_file(resolved)
        except OSError:  # 路径过长/非法 → 当作"不是文件"
            resolved = None
            is_file = False
        except Exception as exc:
            report = exception_diagnostic(
                exc,
                phase="source_resolution",
                title="人设 prompt 路径无法解析",
                message="服务器无法解析指定的人设 prompt 路径。",
                suggestion="检查路径格式和服务器文件系统后重试。",
                retryable=False,
            )
            report["code"] = "persona_prompt_path_invalid"
            report["steps"] = [
                step("resolve_source", "解析人设 prompt 来源", "error", "路径解析异常，未读取文件。").to_dict()
            ]
            raise HTTPException(status_code=400, detail=report) from exc
        if not is_file:
            if inline_fallback:
                return _inline(inline_fallback, "system_prompt（内联文本）")
            return _persona_payload(
                {
                    "exists": False,
                    "is_file": False,
                    "source": source,
                    "resolved_path": str(resolved.absolute()) if resolved is not None else requested,
                    "content": "",
                    "size": 0,
                },
                ok=False,
                code="persona_prompt_path_not_found",
                phase="path_lookup",
                title="人设 prompt 文件不存在",
                message="指定路径没有对应的可读文件。",
                suggestion="检查服务器路径，或留空以读取当前生效配置。",
                retryable=False,
            )
        try:
            size = resolved.stat().st_size
            if size > _PROMPT_PREVIEW_MAX_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=diagnostic(
                        ok=False,
                        code="persona_prompt_too_large",
                        phase="file_read",
                        title="人设 prompt 文件过大",
                        message="文件超过 WebUI prompt 预览上限。",
                        details=(
                            detail("来源", source),
                            detail("文件字节数", size, "error"),
                            detail("预览上限", _PROMPT_PREVIEW_MAX_BYTES),
                        ),
                        steps=(
                            step("resolve_source", "解析人设 prompt 来源", "ok", "已定位文件。"),
                            step("read_prompt", "读取人设 prompt", "error", "文件大小超过预览上限，未读取正文。"),
                        ),
                        suggestion="缩小 prompt 文件，或在服务器上直接检查该文件。",
                        retryable=False,
                    ),
                )
            content = resolved.read_text(encoding="utf-8", errors="replace")
        except HTTPException:
            raise
        except Exception as exc:
            report = exception_diagnostic(
                exc,
                phase="file_read",
                title="人设 prompt 文件读取失败",
                message="服务器无法读取人设 prompt 文件，未向客户端返回原始异常。",
                suggestion="根据 Trace ID 检查脱敏日志、文件权限与编码状态。",
                retryable=True,
            )
            report["code"] = "persona_prompt_read_failed"
            report["details"] = [
                detail("来源", source).to_dict(),
                detail("异常类型", type(exc).__name__, "error").to_dict(),
            ]
            report["steps"] = [
                step("resolve_source", "解析人设 prompt 来源", "ok", "已定位文件。").to_dict(),
                step("read_prompt", "读取人设 prompt", "error", "文件读取异常，未返回 prompt 正文。").to_dict(),
            ]
            raise HTTPException(status_code=500, detail=report) from exc
        return _persona_payload(
            {
                "exists": True,
                "is_file": True,
                "source": source,
                "resolved_path": str(resolved.absolute()),
                "content": content,
                "size": size,
            },
            ok=True,
            code="persona_prompt_file_loaded",
            phase="operation_complete",
            title="人设 prompt 文件已载入",
            message="已读取人设 prompt 文件，可在 WebUI 中预览。",
        )

    return router

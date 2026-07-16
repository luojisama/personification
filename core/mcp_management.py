from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import re
import shutil
import stat
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse, urlsplit, urlunsplit

import httpx

from ..agent.tool_registry import AgentTool, ToolRegistry
from ..skill_runtime.mcp_compat import McpStdioClient
from .db import connect_sync
from .paths import get_data_dir
from .safe_image_download import resolve_public_url


OFFICIAL_MCP_REGISTRY = "https://registry.modelcontextprotocol.io"
_SAFE_PREFIX = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
_SAFE_NPM_PACKAGE = re.compile(r"^(?:@[a-z0-9][a-z0-9._-]{0,99}/)?[a-z0-9][a-z0-9._-]{0,99}$")
_SAFE_PYPI_PACKAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
_EXACT_SEMVER = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-(?:(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_EXACT_PEP440 = re.compile(
    r"""
    (?:(?P<epoch>[0-9]+)!)?
    (?P<release>[0-9]+(?:\.[0-9]+)*)
    (?P<pre>
        [-_.]?(?P<pre_label>alpha|a|beta|b|preview|pre|c|rc)[-_.]?(?P<pre_number>[0-9]+)?
    )?
    (?P<post>
        (?:-(?P<post_number1>[0-9]+))
        |(?:[-_.]?(?P<post_label>post|rev|r)[-_.]?(?P<post_number2>[0-9]+)?)
    )?
    (?P<dev>[-_.]?dev[-_.]?(?P<dev_number>[0-9]+)?)?
    (?:\+(?P<local>[a-z0-9]+(?:[-_.][a-z0-9]+)*))?
    """,
    re.IGNORECASE | re.VERBOSE,
)
_MAX_REGISTRY_QUERY_CHARS = 1000
_MAX_REGISTRY_CURSOR_CHARS = 16384
_MAX_REGISTRY_INPUT_CHARS = 18000
_SECRET_LOCK = threading.RLock()


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, type(default)):
        return value
    try:
        parsed = json.loads(str(value or ""))
    except Exception:
        return default
    return parsed if isinstance(parsed, type(default)) else default


def _package_support_error(package: dict[str, Any]) -> str:
    package_type = str(package.get("registryType") or "").strip().lower()
    transport = package.get("transport") if isinstance(package.get("transport"), dict) else {}
    if package_type not in {"npm", "pypi"} or transport.get("type") != "stdio":
        return "Only npm/PyPI stdio packages are supported."
    identifier = str(package.get("identifier") or "").strip()
    identifier_pattern = _SAFE_NPM_PACKAGE if package_type == "npm" else _SAFE_PYPI_PACKAGE
    if not identifier_pattern.fullmatch(identifier):
        return "Package identifier is invalid or could be interpreted as a launcher option."
    version = str(package.get("version") or "").strip()
    if len(version) > 255:
        return "Package version is too long."
    if package_type == "npm":
        if not _EXACT_SEMVER.fullmatch(version):
            return "npm package must use an exact three-part SemVer version."
    elif not _is_exact_pep440_version(version):
        return "PyPI package must use an exact PEP 440 version with at least three release segments."
    file_sha256 = str(package.get("fileSha256") or "").strip()
    if file_sha256:
        if not re.fullmatch(r"[a-f0-9]{64}", file_sha256):
            return "Package fileSha256 metadata is invalid."
        return "fileSha256 cannot be proven for the artifact executed by npx/uvx; quick install fails closed."
    registry_base = str(package.get("registryBaseUrl") or "").strip().rstrip("/").lower()
    allowed_bases = {
        "npm": {"", "https://registry.npmjs.org"},
        "pypi": {"", "https://pypi.org", "https://pypi.org/simple"},
    }
    if registry_base not in allowed_bases[package_type]:
        return "Custom package registry URLs are not supported by quick install."
    runtime_hint = str(package.get("runtimeHint") or "").strip().lower()
    expected_hint = "npx" if package_type == "npm" else "uvx"
    if runtime_hint and runtime_hint != expected_hint:
        return f"Package requires unsupported runtime: {runtime_hint}."
    if package.get("runtimeArguments"):
        return "Custom runtime arguments are not supported by quick install."
    input_keys: list[str] = []
    for spec in list(package.get("environmentVariables") or []):
        if not isinstance(spec, dict):
            continue
        if "{" in str(spec.get("value") or "") or spec.get("variables"):
            return "Templated environment variables are not supported by quick install."
        if spec.get("value") is None:
            input_keys.append(str(spec.get("name") or "").strip())
    for spec in list(package.get("packageArguments") or []):
        if not isinstance(spec, dict):
            continue
        if bool(spec.get("isSecret", False)):
            return "Secret package arguments cannot be passed safely."
        if bool(spec.get("isRepeated", False)):
            return "Repeated package arguments are not supported by quick install."
        if str(spec.get("type") or "positional") not in {"named", "positional"}:
            return "Package argument type is unsupported."
        if "{" in str(spec.get("value") or "") or spec.get("variables"):
            return "Templated package arguments are not supported by quick install."
        if spec.get("value") is None:
            input_keys.append(str(spec.get("name") or spec.get("valueHint") or "").strip())
    configurable_keys = [key for key in input_keys if key]
    if len(configurable_keys) != len(set(configurable_keys)):
        return "Package contains duplicate configurable input names."
    return ""


def _is_exact_pep440_version(value: str) -> bool:
    text = str(value or "")
    if not text or text != text.strip() or any(character.isspace() for character in text):
        return False
    match = _EXACT_PEP440.fullmatch(text)
    return match is not None and len(match.group("release").split(".")) >= 3


def _package_digest(package: dict[str, Any]) -> str:
    payload = json.dumps(package, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def mcp_registry_sources(plugin_config: Any) -> list[dict[str, str]]:
    sources = [{"id": "official", "name": "Official MCP Registry", "url": OFFICIAL_MCP_REGISTRY, "preview": True}]
    raw = getattr(plugin_config, "personification_mcp_registry_sources", None)
    if isinstance(raw, str):
        raw = _loads(raw, [])
    if not isinstance(raw, list):
        return sources
    for item in raw:
        if not isinstance(item, dict) or item.get("enabled") is False:
            continue
        url = str(item.get("url") or "").strip().rstrip("/")
        try:
            parsed = urlparse(url)
            port = parsed.port
        except ValueError:
            continue
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or port not in {None, 443}
            or len(url) > 500
        ):
            continue
        source_id = "registry_" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
        sources.append({"id": source_id, "name": str(item.get("name") or source_id)[:80], "url": url, "preview": False})
    return sources


def resolve_registry_source(plugin_config: Any, source_id: str) -> dict[str, str]:
    for source in mcp_registry_sources(plugin_config):
        if source["id"] == source_id:
            return source
    raise ValueError("unknown MCP Registry source")


def _pinned_registry_target(url: str, approved_ip: str) -> tuple[str, str, str]:
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https" or parsed.port not in {None, 443}:
        raise ValueError("MCP Registry must use HTTPS on port 443")
    host = str(parsed.hostname or "")
    address = ipaddress.ip_address(approved_ip)
    connection_host = f"[{address}]" if address.version == 6 else str(address)
    connection_url = urlunsplit(("https", connection_host, parsed.path or "/", parsed.query, ""))
    return connection_url, host, host


class McpRegistryClient:
    def __init__(self, plugin_config: Any) -> None:
        self.plugin_config = plugin_config
        self.timeout = max(3, min(int(getattr(plugin_config, "personification_mcp_registry_timeout", 20) or 20), 60))
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}

    async def _get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        *,
        fresh: bool = False,
    ) -> dict[str, Any]:
        cache_key = url + "?" + json.dumps(params or {}, sort_keys=True)
        cached = self._cache.get(cache_key)
        if not fresh and cached and time.time() - cached[0] < 300:
            return cached[1]
        try:
            original_url, approved_ip = await resolve_public_url(url)
        except Exception as exc:
            raise ValueError("MCP Registry must resolve to a public HTTPS address") from exc
        connection_url, host_header, sni_hostname = _pinned_registry_target(original_url, approved_ip)
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False, trust_env=False) as client:
            request = client.build_request(
                "GET",
                connection_url,
                params=params,
                headers={"Accept": "application/json", "Host": host_header},
            )
            request.extensions["sni_hostname"] = sni_hostname
            response = await client.send(request, stream=True, follow_redirects=False)
            try:
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 4 * 1024 * 1024:
                        raise ValueError("MCP Registry response is too large")
            finally:
                await response.aclose()
        try:
            payload = json.loads(body)
        except Exception as exc:
            raise ValueError("invalid MCP Registry response") from exc
        if not isinstance(payload, dict):
            raise ValueError("invalid MCP Registry response")
        if len(self._cache) >= 128:
            oldest_key = min(self._cache, key=lambda key: self._cache[key][0])
            self._cache.pop(oldest_key, None)
        self._cache[cache_key] = (time.time(), payload)
        return payload

    async def search(self, source: dict[str, str], query: str, *, limit: int = 20, cursor: str = "") -> dict[str, Any]:
        query_value = str(query or "").strip()
        cursor_value = str(cursor or "")
        if len(query_value) > _MAX_REGISTRY_QUERY_CHARS:
            raise ValueError("MCP Registry search query is too large")
        if len(cursor_value) > _MAX_REGISTRY_CURSOR_CHARS:
            raise ValueError("MCP Registry cursor is too large")
        if len(query_value) + len(cursor_value) > _MAX_REGISTRY_INPUT_CHARS:
            raise ValueError("MCP Registry search input is too large")
        params: dict[str, Any] = {"version": "latest", "limit": max(1, min(int(limit or 20), 50))}
        if query_value:
            params["search"] = query_value
        if cursor_value:
            params["cursor"] = cursor_value
        payload = await self._get(source["url"] + "/v0.1/servers", params)
        results = []
        for item in list(payload.get("servers") or []):
            if not isinstance(item, dict):
                continue
            server = item.get("server") if isinstance(item.get("server"), dict) else item
            if not isinstance(server, dict):
                continue
            response_meta = item.get("_meta") if isinstance(item.get("_meta"), dict) else {}
            official = response_meta.get("io.modelcontextprotocol.registry/official") or {}
            if not isinstance(official, dict):
                official = {}
            results.append(
                {
                    "name": str(server.get("name") or ""),
                    "title": str(server.get("title") or server.get("name") or ""),
                    "description": str(server.get("description") or ""),
                    "version": str(server.get("version") or ""),
                    "status": str(official.get("status") or "unknown"),
                    "status_message": str(official.get("statusMessage") or ""),
                    "repository": server.get("repository") if isinstance(server.get("repository"), dict) else {},
                    "website": str(server.get("websiteUrl") or ""),
                    "website_url": str(server.get("websiteUrl") or ""),
                    "schema": str(server.get("$schema") or ""),
                    "stdio_packages": sum(
                        1 for package in list(server.get("packages") or [])
                        if isinstance(package, dict) and (package.get("transport") or {}).get("type") == "stdio" and package.get("registryType") in {"npm", "pypi"}
                    ),
                    "remote_count": len(list(server.get("remotes") or [])),
                    "source_id": source["id"],
                }
            )
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        next_cursor = metadata.get("nextCursor")
        if next_cursor is not None and not isinstance(next_cursor, str):
            raise ValueError("MCP Registry returned an invalid cursor")
        return {"servers": results, "next_cursor": next_cursor or "", "source": source}

    async def detail(self, source: dict[str, str], server_name: str, *, fresh: bool = False) -> dict[str, Any]:
        name = str(server_name or "").strip()
        if not name or len(name) > 240:
            raise ValueError("invalid MCP server name")
        payload = await self._get(
            source["url"] + f"/v0.1/servers/{quote(name, safe='')}/versions/latest",
            fresh=fresh,
        )
        server = payload.get("server") if isinstance(payload.get("server"), dict) else payload
        if not isinstance(server, dict) or str(server.get("name") or "") != name:
            raise ValueError("MCP server detail does not match request")
        response_meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
        official = response_meta.get("io.modelcontextprotocol.registry/official") or {}
        if not isinstance(official, dict):
            official = {}
        packages = []
        for index, package in enumerate(list(server.get("packages") or [])):
            if not isinstance(package, dict):
                continue
            transport = package.get("transport") if isinstance(package.get("transport"), dict) else {}
            support_error = _package_support_error(package)
            inputs = []
            input_groups = (
                ("environment", list(package.get("environmentVariables") or [])),
                ("argument", list(package.get("packageArguments") or [])),
            )
            for location, values in input_groups:
                for value in values:
                    if not isinstance(value, dict):
                        continue
                    if value.get("value") is not None:
                        continue
                    key = str(value.get("name") or value.get("valueHint") or "").strip()
                    if not key:
                        continue
                    is_secret = bool(value.get("isSecret", False))
                    inputs.append({
                        "key": key,
                        "description": str(value.get("description") or ""),
                        "required": bool(value.get("isRequired", False)),
                        "secret": is_secret,
                        "default": str(value.get("default") or ""),
                        "choices": [str(item) for item in list(value.get("choices") or [])],
                        "format": str(value.get("format") or "string"),
                        "location": location,
                    })
            packages.append({
                "index": index,
                "digest": _package_digest(package),
                "registry_type": str(package.get("registryType") or ""),
                "identifier": str(package.get("identifier") or ""),
                "version": str(package.get("version") or server.get("version") or ""),
                "transport": str(transport.get("type") or ""),
                "fileSha256": str(package.get("fileSha256") or ""),
                "supported": not support_error,
                "unsupported_reason": support_error,
                "inputs": inputs,
            })
        return {
            "server": {
                "name": name,
                "title": str(server.get("title") or name),
                "description": str(server.get("description") or ""),
                "version": str(server.get("version") or ""),
                "status": str(official.get("status") or "unknown"),
                "status_message": str(official.get("statusMessage") or ""),
                "repository": server.get("repository") if isinstance(server.get("repository"), dict) else {},
                "website": str(server.get("websiteUrl") or ""),
                "website_url": str(server.get("websiteUrl") or ""),
                "schema": str(server.get("$schema") or ""),
            },
            "packages": packages,
            "raw": server,
            "source": source,
            "warning": "Registry namespace verification does not mean the package was security-audited.",
        }


class McpSecretStore:
    def __init__(self, plugin_config: Any) -> None:
        configured = str(getattr(plugin_config, "personification_mcp_secret_file", "") or "").strip()
        self.path = Path(configured) if configured else get_data_dir(plugin_config) / "mcp" / "secrets.json"

    def _read(self) -> dict[str, dict[str, str]]:
        if not self.path.is_file():
            return {}
        try:
            parsed = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError("MCP Secret store is unreadable") from exc
        if not isinstance(parsed, dict) or any(not isinstance(value, dict) for value in parsed.values()):
            raise RuntimeError("MCP Secret store has an invalid structure")
        return parsed

    def _write(self, payload: dict[str, dict[str, str]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex[:8]}.tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        try:
            os.chmod(temp, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        temp.replace(self.path)
        try:
            os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    def set(self, installation_id: str, values: dict[str, str]) -> None:
        with _SECRET_LOCK:
            payload = self._read()
            payload[installation_id] = {str(key): str(value) for key, value in values.items()}
            self._write(payload)

    def get(self, installation_id: str) -> dict[str, str]:
        with _SECRET_LOCK:
            return dict(self._read().get(installation_id) or {})

    def delete(self, installation_id: str) -> None:
        with _SECRET_LOCK:
            payload = self._read()
            if installation_id in payload:
                payload.pop(installation_id, None)
                self._write(payload)


class McpStore:
    def list_installations(self) -> list[dict[str, Any]]:
        with connect_sync() as conn:
            rows = conn.execute("SELECT * FROM mcp_installations ORDER BY created_at DESC").fetchall()
        return [self._installation(row) for row in rows]

    def get_installation(self, installation_id: str) -> dict[str, Any] | None:
        with connect_sync() as conn:
            row = conn.execute("SELECT * FROM mcp_installations WHERE installation_id=?", (installation_id,)).fetchone()
        return self._installation(row) if row is not None else None

    def _installation(self, row: Any) -> dict[str, Any]:
        item = dict(row)
        for field, default in (("args_json", []), ("env_json", {}), ("secret_names_json", []), ("metadata_json", {})):
            item[field[:-5]] = _loads(item.pop(field, ""), default)
        item["desired_enabled"] = bool(item.get("desired_enabled"))
        item["secrets_configured"] = bool(item.get("secret_names"))
        return item

    def tools(self, installation_id: str) -> list[dict[str, Any]]:
        with connect_sync() as conn:
            rows = conn.execute("SELECT * FROM mcp_tool_policies WHERE installation_id=? ORDER BY registered_name", (installation_id,)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["parameters"] = _loads(item.pop("parameters_json", "{}"), {})
            item["output_schema"] = _loads(item.pop("output_schema_json", "{}"), {})
            item["annotations"] = _loads(item.pop("annotations_json", "{}"), {})
            item["inputSchema"] = item["parameters"]
            item["outputSchema"] = item["output_schema"]
            item["enabled"] = bool(item.get("enabled"))
            item["publisher_read_only"] = bool(item.get("publisher_read_only"))
            result.append(item)
        return result

    def save_installation(self, item: dict[str, Any], tools: list[dict[str, Any]]) -> None:
        now = time.time()
        with connect_sync() as conn:
            conn.execute(
                """INSERT INTO mcp_installations(
                    installation_id,source_id,source_url,server_name,server_title,server_version,package_type,
                    package_identifier,command,args_json,env_json,secret_names_json,name_prefix,desired_enabled,
                    observed_status,metadata_json,last_error,created_by,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    item["installation_id"], item["source_id"], item["source_url"], item["server_name"], item.get("server_title", ""),
                    item["server_version"], item["package_type"], item["package_identifier"], item["command"],
                    json.dumps(item["args"], ensure_ascii=False), json.dumps(item["env"], ensure_ascii=False),
                    json.dumps(item["secret_names"], ensure_ascii=False), item["name_prefix"], 1, "ready",
                    json.dumps(item.get("metadata") or {}, ensure_ascii=False), "", item.get("created_by", ""), now, now,
                ),
            )
            for tool in tools:
                conn.execute(
                    """INSERT INTO mcp_tool_policies(
                        installation_id,remote_name,registered_name,title,description,parameters_json,
                        output_schema_json,annotations_json,enabled,risk_level,side_effect,publisher_read_only,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        item["installation_id"], tool["remote_name"], tool["registered_name"], tool.get("title", ""),
                        tool["description"], json.dumps(tool["parameters"], ensure_ascii=False),
                        json.dumps(tool.get("output_schema") or {}, ensure_ascii=False),
                        json.dumps(tool.get("annotations") or {}, ensure_ascii=False), int(tool["enabled"]),
                        tool["risk_level"], tool["side_effect"], int(tool["publisher_read_only"]), now,
                    ),
                )
            conn.commit()

    def sync_tools(
        self,
        installation_id: str,
        prefix: str,
        remote_tools: list[dict[str, Any]],
    ) -> dict[str, int]:
        incoming = [_tool_policy(installation_id, prefix, item) for item in remote_tools]
        remote_names = [item["remote_name"] for item in incoming]
        registered_names = [item["registered_name"] for item in incoming]
        if len(remote_names) != len(set(remote_names)):
            raise ValueError("MCP server exposed duplicate tool names")
        if len(registered_names) != len(set(registered_names)):
            raise ValueError("MCP tool names collide after normalization")

        now = time.time()
        with connect_sync() as conn:
            rows = conn.execute(
                "SELECT * FROM mcp_tool_policies WHERE installation_id=?",
                (installation_id,),
            ).fetchall()
            existing = {str(row["remote_name"]): dict(row) for row in rows}
            added = updated = 0
            for policy in incoming:
                old = existing.get(policy["remote_name"])
                parameters_json = json.dumps(policy["parameters"], ensure_ascii=False)
                output_schema_json = json.dumps(policy["output_schema"], ensure_ascii=False)
                annotations_json = json.dumps(policy["annotations"], ensure_ascii=False)
                if old is None:
                    conn.execute(
                        """INSERT INTO mcp_tool_policies(
                            installation_id,remote_name,registered_name,title,description,parameters_json,
                            output_schema_json,annotations_json,enabled,risk_level,side_effect,
                            publisher_read_only,updated_at
                        ) VALUES(?,?,?,?,?,?,?,?,0,'review','unknown',?,?)""",
                        (
                            installation_id, policy["remote_name"], policy["registered_name"], policy["title"],
                            policy["description"], parameters_json, output_schema_json, annotations_json,
                            int(policy["publisher_read_only"]), now,
                        ),
                    )
                    added += 1
                    continue
                changed = any(
                    (
                        str(old.get("title") or "") != policy["title"],
                        str(old.get("description") or "") != policy["description"],
                        _loads(old.get("parameters_json"), {}) != policy["parameters"],
                        _loads(old.get("output_schema_json"), {}) != policy["output_schema"],
                        _loads(old.get("annotations_json"), {}) != policy["annotations"],
                        bool(old.get("publisher_read_only")) != policy["publisher_read_only"],
                    )
                )
                conn.execute(
                    """UPDATE mcp_tool_policies SET
                        title=?,description=?,parameters_json=?,output_schema_json=?,annotations_json=?,
                        publisher_read_only=?,updated_at=?
                    WHERE installation_id=? AND remote_name=?""",
                    (
                        policy["title"], policy["description"], parameters_json, output_schema_json,
                        annotations_json, int(policy["publisher_read_only"]), now,
                        installation_id, policy["remote_name"],
                    ),
                )
                updated += int(changed)
            removed_names = set(existing) - set(remote_names)
            if removed_names:
                conn.executemany(
                    "DELETE FROM mcp_tool_policies WHERE installation_id=? AND remote_name=?",
                    [(installation_id, name) for name in removed_names],
                )
            conn.commit()
        return {"added": added, "updated": updated, "removed": len(removed_names), "total": len(incoming)}

    def set_protocol_metadata(
        self,
        installation_id: str,
        *,
        protocol_version: str,
        server_info: dict[str, Any],
        capabilities: dict[str, Any],
    ) -> None:
        with connect_sync() as conn:
            row = conn.execute(
                "SELECT metadata_json FROM mcp_installations WHERE installation_id=?",
                (installation_id,),
            ).fetchone()
            if row is None:
                raise KeyError(installation_id)
            metadata = _loads(row["metadata_json"], {})
            metadata["protocol_version"] = str(protocol_version)
            metadata["server_info"] = dict(server_info)
            metadata["capabilities"] = dict(capabilities)
            conn.execute(
                "UPDATE mcp_installations SET metadata_json=?,updated_at=? WHERE installation_id=?",
                (json.dumps(metadata, ensure_ascii=False), time.time(), installation_id),
            )
            conn.commit()

    def set_installation_enabled(self, installation_id: str, enabled: bool) -> None:
        with connect_sync() as conn:
            if conn.execute(
                "UPDATE mcp_installations SET desired_enabled=?,updated_at=? WHERE installation_id=?",
                (int(enabled), time.time(), installation_id),
            ).rowcount != 1:
                raise KeyError(installation_id)
            conn.commit()

    def set_status(self, installation_id: str, status_value: str, error: str = "") -> None:
        with connect_sync() as conn:
            conn.execute(
                "UPDATE mcp_installations SET observed_status=?,last_error=?,updated_at=? WHERE installation_id=?",
                (status_value, str(error)[:300], time.time(), installation_id),
            )
            conn.commit()

    def set_tool_enabled(self, installation_id: str, remote_name: str, enabled: bool, *, approve_side_effect: bool = False) -> None:
        if enabled and not approve_side_effect:
            raise ValueError("enabling an MCP tool requires explicit risk approval")
        with connect_sync() as conn:
            assignments = "enabled=?,updated_at=?"
            values: list[Any] = [int(enabled), time.time()]
            if enabled and approve_side_effect:
                assignments += ",risk_level='high',side_effect='external'"
            values.extend((installation_id, remote_name))
            if conn.execute(
                f"UPDATE mcp_tool_policies SET {assignments} WHERE installation_id=? AND remote_name=?",
                values,
            ).rowcount != 1:
                raise KeyError(remote_name)
            conn.commit()

    def delete(self, installation_id: str) -> None:
        with connect_sync() as conn:
            if conn.execute("DELETE FROM mcp_installations WHERE installation_id=?", (installation_id,)).rowcount != 1:
                raise KeyError(installation_id)
            conn.commit()


def _minimal_process_env(extra: dict[str, str]) -> dict[str, str]:
    allowed = {
        "PATH", "SystemRoot", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
        "LANG", "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS",
    }
    env = {key: value for key, value in os.environ.items() if key in allowed}
    env.update({str(key): str(value) for key, value in extra.items()})
    return env


def _input_value(spec: dict[str, Any], inputs: dict[str, Any]) -> str:
    key = str(spec.get("name") or spec.get("valueHint") or "").strip()
    if spec.get("value") is not None:
        value = str(spec.get("value"))
    else:
        value = str(inputs.get(key, spec.get("default", "")) or "")
    if len(value) > 32768:
        raise ValueError(f"MCP input is too large: {key}")
    if bool(spec.get("isRequired", False)) and not value:
        raise ValueError(f"required MCP input is missing: {key}")
    choices = [str(item) for item in list(spec.get("choices") or [])]
    if value and choices and value not in choices:
        raise ValueError(f"invalid MCP input choice: {key}")
    return value


def _package_arguments(package: dict[str, Any], inputs: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for spec in list(package.get("packageArguments") or []):
        if not isinstance(spec, dict):
            continue
        if bool(spec.get("isSecret", False)):
            raise ValueError("Secret MCP package arguments are unsupported because process arguments are observable")
        value = _input_value(spec, inputs)
        if not value:
            continue
        if str(spec.get("type") or "positional") == "named":
            name = str(spec.get("name") or "").strip()
            if not re.fullmatch(r"(?:-{1,2})?[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name):
                raise ValueError("named MCP argument has an invalid flag name")
            flag = name if name.startswith("-") else "--" + name
            result.append(f"{flag}={value}")
        else:
            result.append(value)
    return result


def build_launch_plan(package: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    package_type = str(package.get("registryType") or "").strip().lower()
    transport = package.get("transport") if isinstance(package.get("transport"), dict) else {}
    identifier = str(package.get("identifier") or "").strip()
    version = str(package.get("version") or "").strip()
    support_error = _package_support_error(package)
    if support_error:
        raise ValueError(support_error)
    env: dict[str, str] = {}
    secrets: dict[str, str] = {}
    secret_names: list[str] = []
    for spec in list(package.get("environmentVariables") or []):
        if not isinstance(spec, dict):
            continue
        name = str(spec.get("name") or "").strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", name):
            raise ValueError("invalid MCP environment variable name")
        value = _input_value(spec, inputs)
        if bool(spec.get("isSecret", False)):
            secret_names.append(name)
            if value:
                secrets[name] = value
        elif value:
            env[name] = value
    package_args = _package_arguments(package, inputs)
    if package_type == "npm":
        command = shutil.which("npx") or shutil.which("npx.cmd") or "npx"
        args = ["--yes", f"{identifier}@{version}", *package_args]
    else:
        command = shutil.which("uvx") or "uvx"
        args = ["--from", f"{identifier}=={version}", identifier, *package_args]
    return {
        "package_type": package_type,
        "package_identifier": identifier,
        "version": version,
        "command": command,
        "args": args,
        "env": env,
        "secrets": secrets,
        "secret_names": secret_names,
    }


def _tool_policy(installation_id: str, prefix: str, item: dict[str, Any]) -> dict[str, Any]:
    remote_name = str(item.get("name") or "").strip()
    if not remote_name or len(remote_name) > 200:
        raise ValueError("MCP tool name is missing or too long")
    safe_remote = re.sub(r"[^A-Za-z0-9_-]+", "_", remote_name).strip("_")[:40] or "tool"
    registered_name = (prefix + safe_remote)[:64]
    raw_annotations = item.get("annotations")
    if raw_annotations is not None and not isinstance(raw_annotations, dict):
        raise ValueError(f"MCP tool has invalid annotations: {remote_name}")
    annotations = dict(raw_annotations or {})
    if len(json.dumps(annotations, ensure_ascii=False)) > 64 * 1024:
        raise ValueError(f"MCP tool has oversized annotations: {remote_name}")
    publisher_read_only = annotations.get("readOnlyHint") is True and annotations.get("destructiveHint") is not True
    parameters = item.get("inputSchema") if isinstance(item.get("inputSchema"), dict) else {}
    if parameters.get("type") != "object" or len(json.dumps(parameters, ensure_ascii=False)) > 256 * 1024:
        raise ValueError(f"MCP tool has an invalid or oversized input schema: {remote_name}")
    raw_output_schema = item.get("outputSchema")
    if raw_output_schema is not None and not isinstance(raw_output_schema, dict):
        raise ValueError(f"MCP tool has an invalid output schema: {remote_name}")
    output_schema = dict(raw_output_schema or {})
    if len(json.dumps(output_schema, ensure_ascii=False)) > 256 * 1024:
        raise ValueError(f"MCP tool has an oversized output schema: {remote_name}")
    title = str(item.get("title") or annotations.get("title") or "").strip()
    if len(title) > 500:
        raise ValueError(f"MCP tool title is too long: {remote_name}")
    return {
        "installation_id": installation_id,
        "remote_name": remote_name,
        "registered_name": registered_name,
        "title": title,
        "description": str(item.get("description") or f"MCP tool {remote_name}")[:1200],
        "parameters": parameters,
        "output_schema": output_schema,
        "annotations": annotations,
        "enabled": False,
        "risk_level": "review",
        "side_effect": "unknown",
        "publisher_read_only": publisher_read_only,
    }


class McpRuntimeManager:
    def __init__(self, runtime: Any, registry: ToolRegistry) -> None:
        self.runtime = runtime
        self.registry = registry
        self.store = McpStore()
        self.secrets = McpSecretStore(runtime.plugin_config)
        self.registry_client = McpRegistryClient(runtime.plugin_config)
        self._clients: dict[str, McpStdioClient] = {}
        self._tool_names: dict[str, set[str]] = {}
        self._lock = asyncio.Lock()

    def _client(self, item: dict[str, Any]) -> McpStdioClient:
        env_values = dict(item.get("env") or {})
        stored_secrets = self.secrets.get(item["installation_id"])
        env_values.update(
            {
                name: stored_secrets[name]
                for name in item.get("secret_names") or []
                if name in stored_secrets
            }
        )
        cwd = get_data_dir(self.runtime.plugin_config) / "mcp" / "runtime" / item["installation_id"][:16]
        cwd.mkdir(parents=True, exist_ok=True)
        return McpStdioClient(
            command=str(item["command"]),
            args=[str(value) for value in item.get("args") or []],
            env=_minimal_process_env(env_values),
            cwd=str(cwd),
            timeout=max(3, int(getattr(self.runtime.plugin_config, "personification_skill_mcp_timeout", 20) or 20)),
        )

    async def _stop_unlocked(self, installation_id: str) -> None:
        names = self._tool_names.pop(installation_id, set())
        self.registry.remove_names(names)
        client = self._clients.pop(installation_id, None)
        if client is not None:
            await client.__aexit__(None, None, None)

    async def _mark_process_exited_unlocked(self, installation_id: str) -> None:
        await self._stop_unlocked(installation_id)
        self.store.set_status(installation_id, "error", "MCP process exited unexpectedly")

    async def refresh_process_states(self) -> int:
        async with self._lock:
            exited = [
                installation_id
                for installation_id, client in self._clients.items()
                if not client.is_running
            ]
            for installation_id in exited:
                await self._mark_process_exited_unlocked(installation_id)
            detached = [
                item["installation_id"]
                for item in self.store.list_installations()
                if item.get("observed_status") == "running" and item["installation_id"] not in self._clients
            ]
            for installation_id in detached:
                self.registry.remove_names(self._tool_names.pop(installation_id, set()))
                self.store.set_status(installation_id, "error", "MCP process is not attached to current runtime")
            return len(exited) + len(detached)

    async def _call_managed_tool(
        self,
        installation_id: str,
        remote_name: str,
        arguments: dict[str, Any],
    ) -> str:
        async with self._lock:
            client = self._clients.get(installation_id)
            if client is None or not client.is_running:
                await self._mark_process_exited_unlocked(installation_id)
                raise RuntimeError("managed MCP process is unavailable")
        try:
            return await client.call_tool(remote_name, arguments)
        except BaseException:
            async with self._lock:
                if self._clients.get(installation_id) is client and not client.is_running:
                    await self._mark_process_exited_unlocked(installation_id)
            raise

    async def _activate_unlocked(self, installation_id: str) -> dict[str, int]:
        item = self.store.get_installation(installation_id)
        if item is None:
            raise KeyError(installation_id)
        await self._stop_unlocked(installation_id)
        if not item["desired_enabled"]:
            self.store.set_status(installation_id, "stopped")
            return {"added": 0, "updated": 0, "removed": 0, "total": len(self.store.tools(installation_id))}
        client = self._client(item)
        try:
            await client.__aenter__()
            remote_tool_list = [tool for tool in await client.list_tools() if isinstance(tool, dict)]
            catalog = self.store.sync_tools(installation_id, item["name_prefix"], remote_tool_list)
            self.store.set_protocol_metadata(
                installation_id,
                protocol_version=client.protocol_version,
                server_info=client.server_info,
                capabilities=client.capabilities,
            )
            policies = self.store.tools(installation_id)
            enabled_policies = [policy for policy in policies if policy["enabled"]]
            if not enabled_policies:
                await client.__aexit__(None, None, None)
                self.store.set_status(installation_id, "ready")
                return catalog
            names: set[str] = set()
            registry_version, registry_snapshot = self.registry.snapshot()
            staged_tools: list[AgentTool] = []
            for policy in enabled_policies:
                registered_name = policy["registered_name"]
                existing = registry_snapshot.get(registered_name)
                if existing is not None and str((existing.metadata or {}).get("mcp_installation_id") or "") != installation_id:
                    raise ValueError(f"MCP tool name conflicts with existing tool: {registered_name}")

                async def _handler(_remote_name=policy["remote_name"], **kwargs: Any) -> str:
                    return await self._call_managed_tool(installation_id, _remote_name, kwargs)

                staged_tools.append(
                    AgentTool(
                        name=registered_name,
                        description=policy["description"],
                        parameters=policy["parameters"],
                        handler=_handler,
                        local=False,
                        metadata={
                            "category": "mcp",
                            "source_kind": "mcp_managed",
                            "mcp_installation_id": installation_id,
                            "remote_name": policy["remote_name"],
                            "title": policy["title"],
                            "output_schema": policy["output_schema"],
                            "annotations": policy["annotations"],
                            "transport": "stdio",
                            "risk_level": policy["risk_level"],
                            "side_effect": policy["side_effect"],
                            "retryable": policy["side_effect"] == "none",
                        },
                    )
                )
                names.add(registered_name)
            retained_tools = [tool for name, tool in registry_snapshot.items() if name not in names]
            self.registry.replace_all([*retained_tools, *staged_tools], expected_version=registry_version)
            self._clients[installation_id] = client
            self._tool_names[installation_id] = names
            self.store.set_status(installation_id, "running")
            return catalog
        except BaseException as exc:
            try:
                await client.__aexit__(None, None, None)
            finally:
                self.store.set_status(installation_id, "error", type(exc).__name__)
            raise

    async def activate(self, installation_id: str) -> None:
        async with self._lock:
            await self._activate_unlocked(installation_id)

    async def install(
        self,
        *,
        source: dict[str, str],
        detail: dict[str, Any],
        package_index: int,
        package_digest: str,
        inputs: dict[str, Any],
        prefix: str,
        creator: str,
    ) -> dict[str, Any]:
        async with self._lock:
            raw = detail["raw"]
            packages = list(raw.get("packages") or [])
            if package_index < 0 or package_index >= len(packages) or not isinstance(packages[package_index], dict):
                raise ValueError("invalid MCP package selection")
            package = packages[package_index]
            if not package_digest or _package_digest(package) != str(package_digest):
                raise ValueError("MCP package metadata changed; refresh details and confirm again")
            plan = build_launch_plan(package, inputs)
            installation_id = uuid.uuid4().hex
            normalized_prefix = str(prefix or "").strip()
            if not normalized_prefix:
                normalized_prefix = "mcp_" + hashlib.sha256(detail["server"]["name"].encode("utf-8")).hexdigest()[:8] + "_"
            if not _SAFE_PREFIX.fullmatch(normalized_prefix):
                raise ValueError("MCP tool prefix must be 2-32 lowercase letters, numbers or underscores")
            self.secrets.set(installation_id, plan["secrets"])
            item = {
                "installation_id": installation_id,
                "source_id": source["id"],
                "source_url": source["url"],
                "server_name": detail["server"]["name"],
                "server_title": detail["server"]["title"],
                "server_version": plan["version"],
                "package_type": plan["package_type"],
                "package_identifier": plan["package_identifier"],
                "command": plan["command"],
                "args": plan["args"],
                "env": plan["env"],
                "secret_names": plan["secret_names"],
                "name_prefix": normalized_prefix,
                "metadata": {
                    "repository": detail["server"].get("repository", {}),
                    "website": detail["server"].get("website", ""),
                    "schema": detail["server"].get("schema", ""),
                    "status": detail["server"].get("status", "unknown"),
                    "fileSha256": str(package.get("fileSha256") or ""),
                    "package_digest": package_digest,
                    "warning": detail.get("warning", ""),
                },
                "created_by": creator,
            }
            try:
                client = self._client({**item, "desired_enabled": True})
                try:
                    await client.__aenter__()
                    remote_tools = await client.list_tools()
                    protocol_metadata = {
                        "protocol_version": client.protocol_version,
                        "server_info": client.server_info,
                        "capabilities": client.capabilities,
                    }
                finally:
                    await client.__aexit__(None, None, None)
                tools = [_tool_policy(installation_id, normalized_prefix, tool) for tool in remote_tools]
                if not tools:
                    raise ValueError("MCP server exposed no tools")
                if len({tool["registered_name"] for tool in tools}) != len(tools):
                    raise ValueError("MCP tool names collide after normalization")
                item["metadata"].update(protocol_metadata)
                self.store.save_installation(item, tools)
            except Exception:
                self.secrets.delete(installation_id)
                raise
            return self.public_installation(installation_id) or {}

    def public_installation(self, installation_id: str) -> dict[str, Any] | None:
        item = self.store.get_installation(installation_id)
        if item is None:
            return None
        item["tools"] = self.store.tools(installation_id)
        configured_secrets = self.secrets.get(installation_id)
        secret_names = list(item.get("secret_names") or [])
        secrets_ready = all(
            bool(configured_secrets.get(name)) for name in item.get("secret_names") or []
        )
        item["secrets_required"] = bool(secret_names)
        item["secrets_configured"] = bool(secret_names) and secrets_ready
        client = self._clients.get(installation_id)
        process_running = client is not None and client.is_running
        registered_names = self._tool_names.get(installation_id, set())
        authorized_count = registered_count = effective_count = 0
        for tool in item["tools"]:
            authorized = bool(tool.get("enabled"))
            registered_tool = self.registry.get(str(tool.get("registered_name") or ""))
            registered = (
                str(tool.get("registered_name") or "") in registered_names
                and registered_tool is not None
                and str((registered_tool.metadata or {}).get("mcp_installation_id") or "") == installation_id
            )
            effective = bool(item["desired_enabled"] and process_running and authorized and registered)
            tool["authorized"] = authorized
            tool["registered"] = registered
            tool["effective"] = effective
            authorized_count += int(authorized)
            registered_count += int(registered)
            effective_count += int(effective)
        observed_status = str(item.get("observed_status") or "stopped")
        item["process_state"] = "exited" if client is not None and not process_running else observed_status
        item["run_allowed"] = bool(item["desired_enabled"] and secrets_ready and authorized_count > 0)
        item["authorized_count"] = authorized_count
        item["registered_count"] = registered_count
        item["effective_count"] = effective_count
        item["tool_count"] = len(item["tools"])
        item.pop("env", None)
        item.pop("args", None)
        return item

    def list_public(self) -> list[dict[str, Any]]:
        result = []
        for stored in self.store.list_installations():
            item = self.public_installation(stored["installation_id"])
            if item is not None:
                result.append(item)
        return result

    async def reload(self) -> dict[str, int]:
        async with self._lock:
            for installation_id in list(self._clients):
                await self._stop_unlocked(installation_id)
            running = ready = failed = 0
            catalog_added = catalog_updated = catalog_removed = 0
            for item in self.store.list_installations():
                if not item["desired_enabled"]:
                    continue
                try:
                    catalog = await self._activate_unlocked(item["installation_id"])
                    catalog_added += int(catalog.get("added") or 0)
                    catalog_updated += int(catalog.get("updated") or 0)
                    catalog_removed += int(catalog.get("removed") or 0)
                    if item["installation_id"] in self._clients:
                        running += 1
                    else:
                        ready += 1
                except Exception:
                    failed += 1
            return {
                "running": running,
                "ready": ready,
                "failed": failed,
                "catalog_added": catalog_added,
                "catalog_updated": catalog_updated,
                "catalog_removed": catalog_removed,
            }

    async def toggle_installation(self, installation_id: str, enabled: bool) -> dict[str, Any]:
        async with self._lock:
            self.store.set_installation_enabled(installation_id, enabled)
            await self._activate_unlocked(installation_id)
            return self.public_installation(installation_id) or {}

    async def toggle_tool(
        self,
        installation_id: str,
        remote_name: str,
        enabled: bool,
        *,
        approve_side_effect: bool = False,
    ) -> dict[str, Any]:
        async with self._lock:
            self.store.set_tool_enabled(
                installation_id,
                remote_name,
                enabled,
                approve_side_effect=approve_side_effect,
            )
            await self._activate_unlocked(installation_id)
            return self.public_installation(installation_id) or {}

    async def delete(self, installation_id: str) -> None:
        async with self._lock:
            await self._stop_unlocked(installation_id)
            self.store.delete(installation_id)
            self.secrets.delete(installation_id)

    async def shutdown(self) -> None:
        async with self._lock:
            for installation_id in list(self._clients):
                await self._stop_unlocked(installation_id)
                self.store.set_status(installation_id, "stopped")


_MANAGERS: dict[int, McpRuntimeManager] = {}


def get_mcp_manager(runtime: Any) -> McpRuntimeManager:
    bundle = getattr(runtime, "runtime_bundle", None)
    registry = getattr(bundle, "tool_registry", None) if bundle is not None else None
    if registry is None:
        raise RuntimeError("tool registry is unavailable")
    key = id(registry)
    manager = _MANAGERS.get(key)
    if manager is None or manager.registry is not registry:
        manager = McpRuntimeManager(runtime, registry)
        _MANAGERS[key] = manager
    else:
        manager.runtime = runtime
    return manager


async def shutdown_mcp_managers() -> None:
    for manager in list(_MANAGERS.values()):
        await manager.shutdown()
    _MANAGERS.clear()


__all__ = [
    "McpRegistryClient",
    "McpRuntimeManager",
    "McpSecretStore",
    "McpStore",
    "build_launch_plan",
    "get_mcp_manager",
    "mcp_registry_sources",
    "resolve_registry_source",
    "shutdown_mcp_managers",
]

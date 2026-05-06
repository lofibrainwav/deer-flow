"""Kingdom 프록시. EROS, 시스템 지표, cmux 상태를 제공합니다."""

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import redis.asyncio as redis
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/kingdom", tags=["kingdom"])

MAX_PROMPT_LEN = 4000


class CmuxDispatchRequest(BaseModel):
    """cmux 표면으로 보낼 명령입니다."""

    prompt: str = ""
    surface: int | str = 1
    model: str = "claude"


class CmuxPreviewRequest(BaseModel):
    """명령 실행 전 의도 분석 요청입니다."""

    prompt: str = ""

# ── EROS ─────────────────────────────────────────────────────────────────────
# Kingdom writes EROS data to Redis db0 (default), not db3 (route cache).
# Create a dedicated connection to db0 for reading EROS data.


# Module-level Redis connection — reused across requests (avoids per-request connect overhead)
_eros_redis: redis.Redis | None = None


async def _get_eros_redis() -> redis.Redis | None:
    """Get (or create) Redis connection to db0 — singleton, not per-request."""
    global _eros_redis
    if _eros_redis is None:
        try:
            redis_url = os.getenv("KINGDOM_REDIS_URL", "redis://127.0.0.1:6380/0")
            _eros_redis = redis.from_url(redis_url, decode_responses=False)
            await _eros_redis.ping()
            logger.info("EROS Redis connected: %s", redis_url)
        except Exception as e:
            logger.warning("Could not connect to Kingdom Redis (db0): %s", e)
            return None
    return _eros_redis


@router.get("/eros", summary="Get EROS Score", description="Read EROS score (6 pillars + s_score) from Kingdom Redis.")
async def get_eros():
    """Read EROS score from Redis key `kingdom:eros:score`."""
    redis = await _get_eros_redis()
    if not redis:
        return {"success": False, "error": "Redis unavailable"}

    try:
        data = await redis.hgetall("kingdom:eros:score")
        if not data:
            return {"success": False, "error": "EROS data not found"}

        def float_or(val, default=0.0):
            try:
                if isinstance(val, bytes):
                    val = val.decode()
                return float(val)
            except (TypeError, ValueError):
                return default

        def str_or(val, default=""):
            if isinstance(val, bytes):
                val = val.decode()
            return val if val else default

        return {
            "success": True,
            "data": {
                "s_score": float_or(data.get(b"s_score")),
                "decision": str_or(data.get(b"decision")),
                "phase": str_or(data.get(b"phase")),
                "benevolence": float_or(data.get(b"benevolence")),
                "truth": float_or(data.get(b"truth")),
                "goodness": float_or(data.get(b"goodness")),
                "beauty": float_or(data.get(b"beauty")),
                "filial_piety": float_or(data.get(b"filial_piety")),
                "eternity": float_or(data.get(b"eternity")),
                "timestamp": str_or(data.get(b"timestamp")),
            },
        }
    except Exception as e:
        logger.error("Failed to read EROS from Redis: %s", e)
        return {"success": False, "error": str(e)}


# ── cmux ─────────────────────────────────────────────────────────────────────

_cmux_paths: dict[str, str | None] | None = None
_orchestra_surfaces: dict[str, Any] | None = None
_sse_clients: set[asyncio.Queue[dict[str, Any]]] = set()


def _decode_value(value: Any, default: str = "") -> str:
    """Redis 바이트 값을 문자열로 변환합니다."""
    if isinstance(value, bytes):
        return value.decode()
    if value is None:
        return default
    return str(value)


def _normalize_surface_id(surface: int | str) -> str:
    """surface:1 형식과 1 형식을 같은 표면 번호로 맞춥니다."""
    text = str(surface).strip()
    return text.removeprefix("surface:") or "1"


def _default_cmux_bin() -> str:
    """운영체제별 cmux 기본 위치를 반환합니다."""
    if sys.platform == "darwin":
        return "/Applications/cmux.app/Contents/Resources/bin/cmux"
    return "/usr/local/bin/cmux"


def _default_cmux_socket() -> str:
    """운영체제별 cmux 소켓 기본 위치를 반환합니다."""
    if sys.platform == "darwin":
        return str(Path.home() / "Library/Application Support/cmux/cmux.sock")
    return str(Path(os.getenv("XDG_RUNTIME_DIR", "/tmp")) / "cmux.sock")


def _detect_cmux_paths() -> dict[str, str | None]:
    """환경변수, PATH, 기본 위치 순서로 cmux 실행 파일과 소켓을 찾습니다."""
    global _cmux_paths
    if _cmux_paths is not None:
        return _cmux_paths

    cmux_bin = os.getenv("CMUX_BIN") or shutil.which("cmux") or _default_cmux_bin()
    socket_path = os.getenv("CMUX_SOCKET_PATH")

    if not socket_path:
        try:
            result = subprocess.run(
                [cmux_bin, "identify"],
                capture_output=True,
                check=True,
                text=True,
                timeout=5,
            )
            parsed = json.loads(result.stdout.strip())
            socket_path = parsed.get("socket_path") or _default_cmux_socket()
        except Exception as exc:
            logger.warning("cmux socket detection failed: %s", exc)
            socket_path = _default_cmux_socket()

    _cmux_paths = {"bin": cmux_bin, "socket": socket_path}
    return _cmux_paths


def _run_cmux(args: list[str], timeout: int = 10) -> str:
    """셸 없이 cmux 명령을 실행합니다."""
    paths = _detect_cmux_paths()
    cmux_bin = paths["bin"]
    socket_path = paths["socket"]
    if not cmux_bin:
        raise RuntimeError("cmux binary not found")

    full_args = [cmux_bin]
    if socket_path:
        full_args.extend(["--socket", socket_path])
    full_args.extend(args)

    result = subprocess.run(
        full_args,
        capture_output=True,
        check=True,
        text=True,
        timeout=timeout,
    )
    return result.stdout.strip()


def _get_current_workspace_id() -> str | None:
    """현재 cmux 작업 공간을 조회합니다."""
    try:
        return _run_cmux(["current-window"], timeout=5)
    except Exception:
        return None


def _parse_surface_tree() -> dict[str, dict[str, str]]:
    """cmux tree 출력에서 표면과 작업 공간 매핑을 추출합니다."""
    try:
        output = _run_cmux(["tree", "--all"], timeout=10)
    except Exception as exc:
        logger.warning("cmux tree read failed: %s", exc)
        return {}

    surfaces: dict[str, dict[str, str]] = {}
    current_workspace: str | None = None

    for line in output.splitlines():
        workspace_match = re.search(r'workspace\s+(\S+)\s+"([^"]+)"', line)
        if workspace_match:
            current_workspace = workspace_match.group(1)
            continue

        surface_match = re.search(r'surface\s+(\S+)\s+\[terminal\]\s+"([^"]+)"', line)
        if surface_match and current_workspace:
            surface_id = surface_match.group(1).removeprefix("surface:")
            surfaces[surface_id] = {
                "workspace": current_workspace,
                "label": surface_match.group(2),
            }

    return surfaces


def _read_json_file(path: Path) -> dict[str, Any]:
    """JSON 파일이 없거나 깨진 경우 빈 객체를 반환합니다."""
    try:
        if not path.exists():
            return {}
        parsed = json.loads(path.read_text())
        if isinstance(parsed, dict):
            return parsed
    except Exception as exc:
        logger.warning("json read failed for %s: %s", path, exc)
    return {}


def _read_orchestra_surfaces() -> dict[str, Any]:
    """cmux 오케스트라 표면 설정을 읽습니다."""
    global _orchestra_surfaces
    if _orchestra_surfaces is not None:
        return _orchestra_surfaces

    config_path = Path(
        os.getenv(
            "ORCHESTRA_CONFIG_PATH",
            str(Path.home() / ".claude/skills/cmux-orchestrator/config/orchestra-config.json"),
        )
    )
    parsed = _read_json_file(config_path)
    surfaces = parsed.get("surfaces", {})
    _orchestra_surfaces = surfaces if isinstance(surfaces, dict) else {}
    return _orchestra_surfaces


def _read_eagle_surfaces() -> dict[str, Any]:
    """외부 표면 상태 파일을 읽습니다."""
    status_path = Path(os.getenv("EAGLE_STATUS_PATH", "/tmp/cmux-eagle-status.json"))
    parsed = _read_json_file(status_path)
    surfaces = parsed.get("surfaces", parsed)
    return surfaces if isinstance(surfaces, dict) else {}


async def _read_surface_status_from_redis() -> dict[str, dict[str, Any]]:
    """Redis에 저장된 cmux 표면 상태를 읽습니다."""
    redis_client = await _get_eros_redis()
    if not redis_client:
        return {}

    surfaces: dict[str, dict[str, Any]] = {}
    try:
        async for key in redis_client.scan_iter("cmux:surface:*"):
            key_text = _decode_value(key)
            surface_id = key_text.split(":")[-1]
            data = await redis_client.hgetall(key)
            surfaces[surface_id] = {
                _decode_value(field): _decode_value(value)
                for field, value in data.items()
            }
    except Exception as exc:
        logger.warning("surface status redis read failed: %s", exc)
    return surfaces


async def _update_surface_state(surface: str, state: str, model: str) -> None:
    """Redis에 표면 상태를 기록합니다."""
    redis_client = await _get_eros_redis()
    if not redis_client:
        return

    try:
        await redis_client.hset(
            f"cmux:surface:{surface}",
            mapping={
                "state": state,
                "model": model or "unknown",
                "lastUpdate": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        )
    except Exception as exc:
        logger.warning("surface state update failed: %s", exc)


async def _get_surface_status() -> dict[str, dict[str, Any]]:
    """cmux, Redis, 상태 파일을 합쳐 표면 상태를 반환합니다."""
    surfaces = await _read_surface_status_from_redis()
    surface_tree = _parse_surface_tree()
    eagle_surfaces = _read_eagle_surfaces()
    orchestra_surfaces = _read_orchestra_surfaces()

    surface_ids = set(surfaces) | set(surface_tree)
    for surface_id in eagle_surfaces:
        surface_ids.add(_normalize_surface_id(surface_id))

    def sort_key(value: str) -> tuple[int, int | str]:
        if value.isdigit():
            return (0, int(value))
        return (1, value)

    for surface_id in sorted(surface_ids, key=sort_key):
        surface = surfaces.setdefault(
            surface_id,
            {"state": "UNKNOWN", "model": "unknown", "lastUpdate": None},
        )
        eagle = eagle_surfaces.get(surface_id) or eagle_surfaces.get(f"surface:{surface_id}") or {}
        tree_info = surface_tree.get(surface_id) or {}
        configured_surface = orchestra_surfaces.get(surface_id) or {}

        if isinstance(eagle, dict):
            surface["state"] = eagle.get("status") or eagle.get("state") or surface.get("state", "UNKNOWN")
            if eagle.get("ai"):
                surface["ai"] = eagle["ai"]

        if tree_info:
            surface["workspace"] = tree_info.get("workspace")
            surface["label"] = tree_info.get("label")

        label = tree_info.get("label", "")
        label_parts = label.split(":", 1) if ":" in label else []
        if isinstance(configured_surface, dict) and configured_surface.get("model"):
            surface["model"] = configured_surface["model"]
            if configured_surface.get("ai"):
                surface["ai"] = configured_surface["ai"]
        elif label_parts:
            surface["model"] = label_parts[0].strip() or surface.get("model", "unknown")

        if len(label_parts) > 1:
            surface["state"] = label_parts[1].strip().upper() or surface.get("state", "UNKNOWN")

    return surfaces


def _dispatch_prompt_to_surface(surface: str, prompt: str) -> bool:
    """cmux 표면에 명령을 입력하고 엔터를 전송합니다."""
    surface_tree = _parse_surface_tree()
    surface_id = _normalize_surface_id(surface)
    surface_ref = f"surface:{surface_id}"
    info = surface_tree.get(surface_id)
    if not info:
        logger.error("cmux surface %s not found", surface_id)
        return False

    current_workspace = _get_current_workspace_id()
    workspace_args = []
    if current_workspace and info.get("workspace") != current_workspace:
        workspace_args = ["--workspace", info["workspace"]]

    try:
        _run_cmux(["send", "--surface", surface_ref, *workspace_args, prompt], timeout=10)
        _run_cmux(["send-key", "--surface", surface_ref, *workspace_args, "enter"], timeout=10)
        return True
    except Exception as exc:
        logger.error("cmux dispatch failed: %s", exc)
        return False


def _preview_prompt(prompt: str) -> dict[str, Any]:
    """간단한 키워드 기반 실행 계획을 계산합니다."""
    lower_prompt = prompt.lower()
    summary = "작업 실행"
    estimated_steps = 1
    tools: list[str] = []
    risk_level = "low"

    if "code" in lower_prompt or "implement" in lower_prompt or "write" in lower_prompt:
        tools.extend(["editor", "terminal"])
        estimated_steps = 2
        summary = "코드 작성 또는 수정"
    if "test" in lower_prompt or "검증" in lower_prompt:
        tools.append("test-runner")
        estimated_steps = max(estimated_steps, 2)
        summary = "코드 검증"
    if "deploy" in lower_prompt or "배포" in lower_prompt:
        risk_level = "high"
        estimated_steps = 3
        summary = "배포 작업"
        tools.extend(["deploy", "health-check"])
    if "search" in lower_prompt or "검색" in lower_prompt:
        tools.append("web-search")
        estimated_steps = max(estimated_steps, 2)
        summary = "검색 및 분석"
    if "git" in lower_prompt or "commit" in lower_prompt:
        tools.append("git")
        risk_level = "medium"
        estimated_steps = max(estimated_steps, 2)
        summary = "Git 작업"
    if "debug" in lower_prompt or "에러" in lower_prompt:
        tools.extend(["terminal", "logs"])
        risk_level = "medium"
        summary = "디버깅"
    if not tools:
        tools.append("cmux")
        summary = "일반 명령 실행"

    return {
        "summary": summary,
        "estimatedSteps": estimated_steps,
        "tools": list(dict.fromkeys(tools)),
        "riskLevel": risk_level,
    }


async def _broadcast_sse(event: str, data: dict[str, Any]) -> None:
    """현재 연결된 SSE 클라이언트에 이벤트를 보냅니다."""
    dead_clients: list[asyncio.Queue[dict[str, Any]]] = []
    for queue in _sse_clients:
        try:
            queue.put_nowait({"event": event, "data": data})
        except asyncio.QueueFull:
            dead_clients.append(queue)
    for queue in dead_clients:
        _sse_clients.discard(queue)


@router.get("/cmux/status", summary="Get cmux surface status")
async def get_cmux_status():
    """cmux 표면 상태를 반환합니다."""
    try:
        surfaces = await _get_surface_status()
        return {"success": True, "surfaces": surfaces, "data": surfaces}
    except Exception as exc:
        logger.error("Failed to read cmux status: %s", exc)
        return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})


@router.post("/cmux/dispatch", summary="Dispatch prompt to cmux surface")
async def dispatch_cmux_prompt(request: CmuxDispatchRequest):
    """cmux 표면에 명령을 전달합니다."""
    prompt = request.prompt.strip()
    surface = _normalize_surface_id(request.surface)
    model = request.model or "unknown"

    if not prompt:
        return JSONResponse(status_code=400, content={"success": False, "error": "prompt is required"})
    if len(prompt) > MAX_PROMPT_LEN:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": f"prompt exceeds maximum length of {MAX_PROMPT_LEN}"},
        )

    task_id = str(uuid.uuid4())
    await _update_surface_state(surface, "WORKING", model)
    await _broadcast_sse(
        "dispatch",
        {
            "taskId": task_id,
            "surface": int(surface) if surface.isdigit() else surface,
            "model": model,
            "prompt": prompt[:100],
            "status": "dispatched",
        },
    )

    if not _dispatch_prompt_to_surface(surface, prompt):
        await _update_surface_state(surface, "ERROR", model)
        await _broadcast_sse(
            "surface-update",
            {"surface": int(surface) if surface.isdigit() else surface, "state": "ERROR", "model": model},
        )
        return JSONResponse(status_code=500, content={"success": False, "error": "Failed to send to cmux surface"})

    return {
        "success": True,
        "data": {"taskId": task_id, "status": "dispatched"},
        "taskId": task_id,
        "status": "dispatched",
    }


@router.post("/cmux/preview", summary="Preview cmux dispatch intent")
async def preview_cmux_prompt(request: CmuxPreviewRequest):
    """cmux 디스패치 전 예상 실행 계획을 반환합니다."""
    prompt = request.prompt.strip()
    if not prompt:
        return JSONResponse(status_code=400, content={"success": False, "error": "prompt is required"})
    return {"success": True, "data": _preview_prompt(prompt)}


@router.get("/cmux/subscribe", summary="Subscribe cmux surface events")
async def subscribe_cmux_events(request: Request):
    """cmux 표면 이벤트를 SSE로 전송합니다."""
    client_id = str(uuid.uuid4())
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
    _sse_clients.add(queue)

    async def event_generator():
        try:
            yield {
                "event": "connected",
                "data": json.dumps(
                    {"clientId": client_id, "message": "SSE connected to Kingdom cmux"},
                    ensure_ascii=False,
                ),
            }
            while not await request.is_disconnected():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                    yield {
                        "event": event["event"],
                        "data": json.dumps(event["data"], ensure_ascii=False),
                    }
                except TimeoutError:
                    yield {
                        "event": "ping",
                        "data": json.dumps({"timestamp": int(time.time() * 1000)}),
                    }
        finally:
            _sse_clients.discard(queue)

    return EventSourceResponse(event_generator())


# ── System Metrics ────────────────────────────────────────────────────────────

_cpu_prev: tuple[int, ...] | None = None
_cpu_prev_idle: int | None = None


def _read_cpu_percent() -> float:
    """Read CPU % from /proc/stat (Linux container-compatible)."""
    global _cpu_prev, _cpu_prev_idle
    try:
        stat = Path("/proc/stat").read_text().splitlines()[0]
        vals = [int(x) for x in stat.split()[1:]]
        total = sum(vals)
        idle = vals[3]
        if _cpu_prev is not None and _cpu_prev_idle is not None:
            total_delta = total - sum(_cpu_prev)
            idle_delta = idle - _cpu_prev_idle
            if total_delta > 0:
                pct = (1 - idle_delta / total_delta) * 100
                _cpu_prev = tuple(vals)
                _cpu_prev_idle = idle
                return round(pct, 1)
        _cpu_prev = tuple(vals)
        _cpu_prev_idle = idle
        return 0.0
    except Exception:
        return 0.0


def _read_mem_percent() -> float:
    """Read memory % from /proc/meminfo (Linux container-compatible)."""
    try:
        lines = Path("/proc/meminfo").read_text().splitlines()
        mem = {}
        for line in lines:
            parts = line.split()
            if len(parts) >= 2:
                mem[parts[0]] = int(parts[1]) * 1024
        total = mem.get("MemTotal:", 1)
        free = mem.get("MemFree:", 0)
        buffers = mem.get("Buffers:", 0)
        cached = mem.get("Cached:", 0)
        used = total - free - buffers - cached
        return round(max(used / total, 0) * 100, 1)
    except Exception:
        return 0.0


async def _ping_latency_ms() -> float:
    """Measure round-trip to Docker gateway itself as a latency proxy."""
    try:
        from app.gateway.config import get_gateway_config
        cfg = get_gateway_config()
        start = time.monotonic()
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(cfg.host, cfg.port),
            timeout=1.0,
        )
        writer.close()
        await writer.wait_closed()
        return round((time.monotonic() - start) * 1000, 1)
    except Exception:
        return 0.5


@router.get(
    "/system/metrics",
    summary="Get System Metrics",
    description="CPU, memory, latency — reads /proc inside the Docker container.",
)
async def get_system_metrics():
    """Return CPU/memory/latency from /proc and loopback ping."""
    try:
        cpu = _read_cpu_percent()
        mem = _read_mem_percent()
        latency = await _ping_latency_ms()

        return {
            "success": True,
            "data": {
                "cpuUsage": cpu,
                "memoryUsage": mem,
                "networkLatency": latency,
                "totalMemory": 0,
                "usedMemory": 0,
                "freeMemory": 0,
                "cpuCores": os.cpu_count() or 1,
                "loadAverage": [],
                "uptime": time.time(),
                "hostname": os.uname().nodename,
                "platform": os.uname().sysname,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        }
    except Exception as e:
        logger.error("Failed to read system metrics: %s", e)
        return {"success": False, "error": str(e)}

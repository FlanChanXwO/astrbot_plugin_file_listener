from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from enum import Enum
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .logger import logger
from .models import FilterContext


class LinkValidationResult(Enum):
    """文件链接探测结果。"""

    VALID = "valid"
    INVALID = "invalid"
    INDETERMINATE = "indeterminate"


LinkProbe = Callable[[str], Awaitable[LinkValidationResult]]
DEFAULT_LINK_VALIDATION_TIMEOUT_SECONDS = 5.0


def _classify_http_error(error: HTTPError) -> LinkValidationResult:
    status = error.code
    if status == 416:
        content_range = error.headers.get("Content-Range", "")
        if content_range.rstrip().endswith("/0"):
            return LinkValidationResult.VALID
        return LinkValidationResult.INDETERMINATE
    if status in {408, 425, 429} or 500 <= status < 600:
        return LinkValidationResult.INDETERMINATE
    if 400 <= status < 500:
        return LinkValidationResult.INVALID
    return LinkValidationResult.INDETERMINATE


def _probe_file_link_sync(url: str, timeout: float) -> LinkValidationResult:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return LinkValidationResult.INVALID

    request = Request(
        url,
        method="GET",
        headers={
            "Range": "bytes=0-0",
            "Accept-Encoding": "identity",
            "User-Agent": "AstrBot-File-Listener/1.0",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            status = response.getcode()
            if status in {200, 206}:
                return LinkValidationResult.VALID
            if 400 <= status < 500:
                return LinkValidationResult.INVALID
            return LinkValidationResult.INDETERMINATE
    except HTTPError as exc:
        return _classify_http_error(exc)
    except (URLError, TimeoutError, OSError):
        return LinkValidationResult.INDETERMINATE
    except ValueError:
        return LinkValidationResult.INVALID


async def probe_file_link(
    url: str,
    *,
    timeout: float = DEFAULT_LINK_VALIDATION_TIMEOUT_SECONDS,
) -> LinkValidationResult:
    """使用 Range GET 探测文件 URL，避免完整下载文件。"""
    return await asyncio.to_thread(_probe_file_link_sync, url, timeout)


class FileLinkValidationFilter:
    """从当前 DirectLink 工作集剔除已确认失效的文件 URL。"""

    def __init__(self, probe: LinkProbe | None = None):
        self._probe = probe or probe_file_link

    async def __call__(self, context: FilterContext) -> FilterContext:
        kept = []
        for file_event in context.current_files:
            url = file_event.file_url
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                kept.append(file_event)
                continue
            try:
                result = await self._probe(url)
            except Exception:
                logger.warning(
                    "文件链接校验器异常，跳过本次校验: file=%s",
                    file_event.file_name,
                    exc_info=True,
                )
                kept.append(file_event)
                continue
            if result is LinkValidationResult.INVALID:
                logger.info(
                    "文件链接已失效，DirectLink 将跳过: file=%s",
                    file_event.file_name,
                )
                continue
            kept.append(file_event)

        context.current_files[:] = kept
        return context

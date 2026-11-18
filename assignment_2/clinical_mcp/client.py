"""Route each operation through an SDK MCP session and retain actual JSON-RPC envelopes."""

import asyncio
import os
import sys
from contextlib import asynccontextmanager

import httpx2
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from mcp.types import REQUEST_TIMEOUT, PaginatedRequestParams


REMOTE_URL = "https://pubmed.caseyjhand.com/mcp"
REMOTE_TOOL = "pubmed_search_articles"
REMOTE_TIMEOUT = 12


class RequestFailed(Exception):
    def __init__(self, message: str, trace: list):
        super().__init__(message)
        self.trace = trace


class TracedStream:
    def __init__(self, stream, trace: list, direction: str, attempt: int):
        self.stream, self.trace, self.direction, self.attempt = stream, trace, direction, attempt

    def __getattr__(self, name):
        return getattr(self.stream, name)

    async def __aenter__(self):
        await self.stream.__aenter__()
        return self

    async def __aexit__(self, *exc):
        return await self.stream.__aexit__(*exc)

    def __aiter__(self):
        return self

    async def __anext__(self):
        message = await self.stream.__anext__()
        self.record(message)
        return message

    async def receive(self):
        message = await self.stream.receive()
        self.record(message)
        return message

    async def send(self, message):
        self.record(message)
        await self.stream.send(message)

    def record(self, message):
        if not isinstance(message, Exception):
            self.trace.append({"direction": self.direction, "attempt": self.attempt,
                               "packet": message.message.model_dump(mode="json", by_alias=True, exclude_unset=True)})


@asynccontextmanager
async def transport(source: str):
    if source == "stdio":
        command = StdioServerParameters(command=sys.executable, args=["-m", "clinical_mcp.server"])
        async with stdio_client(command) as streams:
            yield streams
    elif source == "sse":
        port = int(os.getenv("PIMIIM_SSE_PORT", "8000"))
        async with sse_client(f"http://127.0.0.1:{port}/sse", timeout=1.5, sse_read_timeout=5) as streams:
            yield streams
    elif source == "pubmed":
        async with httpx2.AsyncClient(timeout=httpx2.Timeout(10, connect=4), follow_redirects=False) as http:
            async with streamable_http_client(REMOTE_URL, http_client=http) as streams:
                yield streams
    else:
        raise ValueError(f"Неизвестный источник MCP: {source}")


async def list_tools(session):
    tools = []
    cursors = set()
    params = None
    while True:
        page = await session.list_tools(params=params)
        tools.extend(item.model_dump(mode="json", by_alias=True, exclude_none=True) for item in page.tools)
        cursor = page.next_cursor
        if not cursor:
            return tools
        if cursor in cursors:
            raise ValueError("Сервер повторил курсор страницы инструментов.")
        cursors.add(cursor)
        params = PaginatedRequestParams(cursor=cursor)


def retryable(error: BaseException) -> bool:
    if isinstance(error, BaseExceptionGroup):
        return all(retryable(item) for item in error.exceptions)
    if isinstance(error, httpx2.HTTPStatusError):
        return error.response.status_code in {502, 503, 504}
    if isinstance(error, MCPError):
        return error.code == REQUEST_TIMEOUT
    return isinstance(error, (TimeoutError, ConnectionError, httpx2.TransportError))


def error_text(error: BaseException) -> str:
    if isinstance(error, BaseExceptionGroup):
        return " · ".join(dict.fromkeys(error_text(item) for item in error.exceptions))
    if isinstance(error, TimeoutError):
        return "истекло время ожидания"
    return str(error) or type(error).__name__


async def request(source: str, tool: str | None = None, arguments: dict | None = None):
    trace = []
    if source == "pubmed" and tool is not None and tool != REMOTE_TOOL:
        raise RequestFailed("Этот внешний инструмент не разрешён в приложении.", trace)
    delays = (0, 0.25, 0.75) if source == "sse" else (0,)
    for attempt, delay in enumerate(delays, 1):
        await asyncio.sleep(delay)
        try:
            async with asyncio.timeout(REMOTE_TIMEOUT if source == "pubmed" else 5):
                async with transport(source) as (incoming, outgoing):
                    read = TracedStream(incoming, trace, "←", attempt)
                    write = TracedStream(outgoing, trace, "→", attempt)
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        if tool is None:
                            result = await list_tools(session)
                            if source == "pubmed":
                                result = [item for item in result if item["name"] == REMOTE_TOOL]
                        else:
                            reply = await session.call_tool(tool, arguments or {})
                            result = reply.model_dump(mode="json", by_alias=True, exclude_none=True)
                return result, trace
        except Exception as error:
            if attempt == len(delays) or not retryable(error):
                raise RequestFailed(f"Источник {source} · {error_text(error)}", trace) from error

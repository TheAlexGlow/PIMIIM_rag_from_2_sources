"""Check the real transports. Start clinical_mcp.network beforehand."""

import argparse
import asyncio

from clinical_mcp.client import REMOTE_TOOL, request


def check_trace(trace, method):
    outgoing = next(event["packet"] for event in trace
                    if event["direction"] == "→" and event["packet"].get("method") == method)
    assert outgoing["jsonrpc"] == "2.0", outgoing
    response = next(event["packet"] for event in trace
                    if event["direction"] == "←" and event["packet"].get("id") == outgoing["id"])
    assert "result" in response, response


async def main(remote):
    for source, minimum in [("stdio", 3), ("sse", 5)]:
        tools, trace = await request(source)
        assert len(tools) >= minimum + 1, tools
        check_trace(trace, "initialize")
        check_trace(trace, "tools/list")
        result, trace = await request(source, "bmi", {"weight_kg": 72, "height_cm": 175})
        assert abs(result["structuredContent"]["value"] - 23.5102) < 0.0001, result
        check_trace(trace, "tools/call")
        result, _ = await request(source, "list_calculators")
        assert len(result["structuredContent"]["calculators"]) == minimum, result
        invalid, _ = await request(source, "bmi", {"weight_kg": 72, "height_cm": 0})
        assert invalid["structuredContent"]["warning"], invalid
        wrong_type, _ = await request(source, "bmi", {"weight_kg": "bad", "height_cm": 175})
        assert wrong_type.get("isError"), wrong_type
        print(f"{source}: initialize, tools/list, tools/call, invalid range and invalid type OK")
    if remote:
        tools, _ = await request("pubmed")
        assert REMOTE_TOOL in {tool["name"] for tool in tools}, tools
        result, trace = await request("pubmed", REMOTE_TOOL,
                                      {"query": "hypertension systematic review[pt]", "maxResults": 3, "summaryCount": 3})
        assert not result.get("isError") and (result.get("structuredContent") or result.get("content")), result
        check_trace(trace, "tools/call")
        print("pubmed: tools/list and live tools/call OK")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remote", action="store_true")
    asyncio.run(main(parser.parse_args().remote))

"""The stdio entry point, also used to register the shared calculators."""

import logging
import sys

from pydantic import TypeAdapter

from .calculators import bmi, body_surface_area, egfr_ckd_epi, glasgow_coma_scale, mean_arterial_pressure
from .catalog import CATALOG


STDIO_TOOLS = (bmi, body_surface_area, glasgow_coma_scale)
NETWORK_TOOLS = (*STDIO_TOOLS, mean_arterial_pressure, egfr_ckd_epi)


def make_server(network: bool = False):
    from mcp.server import MCPServer
    from mcp.types import ToolAnnotations

    transport = "SSE" if network else "stdio"
    server = MCPServer(f"PIMIIM clinical calculators ({transport})", version="1.0.0")
    functions = NETWORK_TOOLS if network else STDIO_TOOLS
    read_only = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)
    for function in functions:
        info = CATALOG[function.__name__]
        server.add_tool(function, title=info.title, description=f"{info.category}. {info.description}", annotations=read_only)

    @server.tool(title="Каталог клинических калькуляторов", annotations=read_only)
    def list_calculators() -> dict:
        """Описания, единицы, границы этой реализации и первоисточники."""
        items = []
        for function in functions:
            info = CATALOG[function.__name__]
            fields = {key: TypeAdapter(annotation).json_schema()
                      for key, annotation in function.__annotations__.items() if key != "return"}
            items.append({"name": function.__name__, "title": info.title, "category": info.category,
                          "description": info.description, "source": info.source, "parameters": fields})
        return {"calculators": items}

    return server


if __name__ == "__main__":
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    make_server().run(transport="stdio")

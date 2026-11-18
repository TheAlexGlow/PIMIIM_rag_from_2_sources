"""Run a separate Uvicorn process listening on the local SSE port."""

import argparse
import os

from .server import make_server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=int(os.getenv("PIMIIM_SSE_PORT", "8000")))
    args = parser.parse_args()
    make_server(network=True).run(transport="sse", host="127.0.0.1", port=args.port,
                                  sse_path="/sse", message_path="/messages/")


if __name__ == "__main__":
    main()

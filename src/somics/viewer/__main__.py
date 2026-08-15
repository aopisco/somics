"""Run the viewer API: `uv run python -m somics.viewer`."""

import argparse
import logging

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the somics 3D viewer API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    uvicorn.run("somics.viewer.api:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()

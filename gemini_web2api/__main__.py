"""Entry point: python -m gemini_web2api"""
import argparse
import logging
import os
from pathlib import Path

from .config import CONFIG, load_config, find_config
from .models import MODELS
from .gemini import HAS_HTTPX, logger
from .server import GeminiHandler, ThreadedServer
from . import __version__


def main():
    parser = argparse.ArgumentParser(description="Gemini Web to OpenAI API")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--config", type=str, default=None)
    parser.add_argument("--cookie-file", type=str, default=None)
    parser.add_argument("--proxy", type=str, default=None, help="HTTP proxy, e.g. http://127.0.0.1:7890")
    parser.add_argument("--version", action="version", version=f"gemini-web2api {__version__}")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    config_path = args.config or os.environ.get("GEMINI_WEB2API_CONFIG") or find_config()
    if config_path:
        load_config(config_path)

    if args.port:
        CONFIG["port"] = args.port
    if args.cookie_file:
        CONFIG["cookie_file"] = args.cookie_file
    if args.proxy:
        CONFIG["proxy"] = args.proxy

    port = CONFIG["port"]
    server = ThreadedServer((CONFIG["host"], port), GeminiHandler)
    logger.info(f"gemini-web2api v{__version__}")
    logger.info(f"Listening: http://0.0.0.0:{port}")
    logger.info(f"Base URL:  http://localhost:{port}/v1")
    logger.info(f"Models:    {', '.join(MODELS.keys())}")
    cookie_file = CONFIG.get("cookie_file")
    if not cookie_file:
        logger.info("Cookie:    none (anonymous)")
    elif Path(cookie_file).exists():
        logger.info(f"Cookie:    {cookie_file}")
    else:
        logger.warning(f"Cookie:    {cookie_file} not found, requests will be anonymous")
    logger.info(f"Proxy:     {CONFIG.get('proxy') or 'system env'}")
    logger.info(f"Streaming: {'httpx (true streaming)' if HAS_HTTPX else 'urllib (buffered)'}")
    logger.info(f"Temporary: {'yes' if CONFIG.get('temporary_chats', False) else 'no'}")
    if not CONFIG.get("temporary_chats", False) and not (cookie_file and Path(cookie_file).exists()):
        logger.warning("Temporary chats are off but no cookie is loaded: chats will not be saved to any account")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopped.")
        server.shutdown()


if __name__ == "__main__":
    main()

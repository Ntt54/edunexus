"""Entry point for the EduNexus (local AI tutor) web server."""

from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ollama-tutor-webgui",
        description="EduNexus — professeur IA local : bibliothèque, exercices, répétition espacée",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        choices=["127.0.0.1", "localhost"],
        help="Bind address — loopback only (default: 127.0.0.1)",
    )
    parser.add_argument("--port", type=int, default=9215, help="Port (default: 9215)")
    parser.add_argument(
        "--url",
        default=None,
        help="Ollama server URL (default: OLLAMA_HOST env or http://localhost:11434)",
    )
    args = parser.parse_args()

    if args.url:
        import os

        os.environ["OLLAMA_HOST"] = args.url

    try:
        import uvicorn
    except ImportError:
        print("FastAPI/uvicorn manquant. Installez avec :")
        print("  pip install 'ollama-tutor[web]'   (ou pip install fastapi 'uvicorn[standard]')")
        raise SystemExit(1)

    from .server import create_app

    app = create_app()
    print("✓ EduNexus démarré", flush=True)
    print(f"  Interface web : http://{args.host}:{args.port}/tutor", flush=True)
    print("  (Ctrl+C pour arrêter)", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

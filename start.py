import os

import uvicorn


def main():
    cpu_count = os.cpu_count() or 4
    # Allocate workers: 2 ~ cpu_count, capped at 8 for typical cloud instances
    workers = min(max(2, cpu_count // 2), 8)

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))

    print(f"Starting FaceVault with {workers} workers on {host}:{port}")

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        workers=workers,
        log_level="info",
        timeout_keep_alive=30,
        limit_concurrency=1000,
        backlog=2048,
    )


if __name__ == "__main__":
    main()

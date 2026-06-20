import multiprocessing
import uvicorn


def run_http():
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, log_level="info")


def run_https():
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8443,
        ssl_keyfile="/app/key.pem",
        ssl_certfile="/app/cert.pem",
        log_level="info",
    )


if __name__ == "__main__":
    p1 = multiprocessing.Process(target=run_http, daemon=True)
    p2 = multiprocessing.Process(target=run_https, daemon=True)
    p1.start()
    p2.start()
    p1.join()
    p2.join()

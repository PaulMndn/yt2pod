import os

from waitress import serve

from . import create_app


def main():
    app = create_app()
    app.extensions["yt2pod_worker"].start()
    serve(app, host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8000")),
          threads=max(1, int(os.environ.get("THREADS", "1"))))


if __name__ == "__main__":
    main()

"""ASGI entrypoint: `uvicorn main:app`."""
from shopping.web.app import create_app

app = create_app()

import logging

logging.basicConfig(level=logging.INFO)

from fastapi import FastAPI

from app.routers import oracle
from fastapi.middleware.cors import CORSMiddleware


app = FastAPI(title="Spiritual Oracapp")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000","https://spiritual-oracle-web.netlify.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(oracle.router)


@app.get("/")
def read_root() -> dict:
    return {"message": "Spiritual Oracle is running."}

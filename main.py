from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from collections import deque
from time import time
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = FastAPI()

limits = {}

@app.middleware("http")
async def rate_limit(request: Request, call_next):
    user_id = request.headers.get("X-User-ID", "default")
    now = time()
    window = 60
    max_requests = 100

    if user_id not in limits:
        logger.debug(f"New user {user_id}")
        limits[user_id] = deque()

    while limits[user_id] and now - limits[user_id][0] > window:
        limits[user_id].popleft()

    current_count = len(limits[user_id])
    logger.debug(f"User {user_id}: {current_count} requests")

    if current_count >= max_requests:
        logger.info(f"Rate limit hit for {user_id}: {current_count} >= {max_requests}")
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded"}
        )

    limits[user_id].append(now)
    response = await call_next(request)
    return response

@app.get("/products")
async def get_products():
    return {"products": ["item1", "item2", "item3"]}

@app.post("/cart")
async def add_to_cart(item: str):
    return {"message": f"Added {item} to cart"}
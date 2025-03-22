from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import redis.asyncio as redis
from time import time
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = FastAPI()

# Redis client (will use env vars in Step 6, hardcoded for now)
redis_client = redis.Redis(host="redis", port=6379, db=0, decode_responses=True)

@app.middleware("http")
async def rate_limit(request: Request, call_next):
    user_id = request.headers.get("X-User-ID", "default")
    now = time()
    window = 60
    max_requests = 100
    key = f"rate:{user_id}"

    # Remove timestamps outside the window
    await redis_client.zremrangebyscore(key, 0, now - window)

    # Get current request count
    current_count = await redis_client.zcard(key)
    logger.debug(f"User {user_id}: {current_count} requests")

    # Check limit
    if current_count >= max_requests:
        logger.info(f"Rate limit hit for {user_id}: {current_count} >= {max_requests}")
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded"}
        )

    # Add current request timestamp
    await redis_client.zadd(key, {str(now): now})
    await redis_client.expire(key, window)  # Auto-expire after window
    logger.debug(f"Added request, new count: {current_count + 1}")

    response = await call_next(request)
    return response

@app.get("/products")
async def get_products():
    return {"products": ["item1", "item2", "item3"]}

@app.post("/cart")
async def add_to_cart(item: str):
    return {"message": f"Added {item} to cart"}

@app.on_event("shutdown")
async def shutdown():
    logger.info("Closing Redis connection")
    await redis_client.close()
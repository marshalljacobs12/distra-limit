from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import redis.asyncio as redis
from time import time
import logging
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configure logging
log_level = os.getenv("LOG_LEVEL", "DEBUG").upper()
logging.basicConfig(level=getattr(logging, log_level))
logger = logging.getLogger(__name__)

app = FastAPI()

# Redis configuration from environment
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))
redis_client = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=REDIS_DB,
    decode_responses=True
)

# Rate limit configuration from environment
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))  # Seconds
RATE_LIMIT_MAX_REQUESTS = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "100"))

@app.middleware("http")
async def rate_limit(request: Request, call_next):
    user_id = request.headers.get("X-User-ID", "default")
    now = time()
    window = RATE_LIMIT_WINDOW
    max_requests = RATE_LIMIT_MAX_REQUESTS
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
    await redis_client.expire(key, window) # Auto-expire after window
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
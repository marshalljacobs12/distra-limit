from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import redis.asyncio as redis
from time import time
import logging
import os
from dotenv import load_dotenv

load_dotenv()

# Configure logging
log_level = os.getenv("LOG_LEVEL", "DEBUG").upper()
logging.basicConfig(level=getattr(logging, log_level))
logger = logging.getLogger(__name__)

app = FastAPI()

# Redis configuration
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))
redis_client = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=REDIS_DB,
    decode_responses=True
)

# Rate limit defaults
DEFAULT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))
DEFAULT_MAX_REQUESTS = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "100"))

# Endpoint-specific rate limits (loaded from env)
ENDPOINT_LIMITS = {
    "/products": {
        "window": int(os.getenv("RATE_LIMIT_WINDOW_PRODUCTS", DEFAULT_WINDOW)),
        "max_requests": int(os.getenv("RATE_LIMIT_MAX_REQUESTS_PRODUCTS", DEFAULT_MAX_REQUESTS))
    },
    "/cart": {
        "window": int(os.getenv("RATE_LIMIT_WINDOW_CART", DEFAULT_WINDOW)),
        "max_requests": int(os.getenv("RATE_LIMIT_MAX_REQUESTS_CART", DEFAULT_MAX_REQUESTS))
    }
}

class CartItem(BaseModel):
    item: str

@app.middleware("http")
async def rate_limit(request: Request, call_next):
    user_id = request.headers.get("X-User-ID", "default")
    path = request.url.path  # e.g., "/products" or "/cart"
    now = time()

    # Get endpoint-specific limits, fall back to defaults
    limits = ENDPOINT_LIMITS.get(path, {
        "window": DEFAULT_WINDOW,
        "max_requests": DEFAULT_MAX_REQUESTS
    })
    window = limits["window"]
    max_requests = limits["max_requests"]
    key = f"rate:{user_id}:{path}"

    # Get current request count
    await redis_client.zremrangebyscore(key, 0, now - window)
    current_count = await redis_client.zcard(key)
    logger.debug(f"User {user_id} at {path}: {current_count} requests")

    # Check limit
    if current_count >= max_requests:
        logger.info(f"Rate limit hit for {user_id} at {path}: {current_count} >= {max_requests}")
        return JSONResponse(
            status_code=429,
            content={"detail": f"Rate limit exceeded for {path}"}
        )

    # Add current request timestamp
    await redis_client.zadd(key, {str(now): now})
    await redis_client.expire(key, window) # Auto-expire after window
    logger.debug(f"Added request at {path}, new count: {current_count + 1}")

    response = await call_next(request)
    return response

@app.get("/products")
async def get_products():
    return {"products": ["item1", "item2", "item3"]}

@app.post("/cart")
async def add_to_cart(cart_item: CartItem):
    logger.debug(f"Received POST to /cart with item: {cart_item.item}")
    return {"message": f"Added {cart_item.item} to cart"}

@app.on_event("shutdown")
async def shutdown():
    logger.info("Closing Redis connection")
    await redis_client.close()
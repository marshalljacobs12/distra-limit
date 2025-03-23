from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from prometheus_fastapi_instrumentator import Instrumentator
from prometheus_client import Counter, Gauge
import redis.asyncio as redis
from time import time
import logging
import os
from dotenv import load_dotenv
from typing import Dict, Optional

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
DEFAULT_BURST = int(os.getenv("RATE_LIMIT_BURST", "20"))
NUM_INSTANCES = int(os.getenv("NUM_INSTANCES", "2"))  # Default to 2 instances

# Endpoint-specific rate limits (loaded from env)
ENDPOINT_LIMITS = {
    "/products": {
        "window": int(os.getenv("RATE_LIMIT_WINDOW_PRODUCTS", DEFAULT_WINDOW)),
        "max_requests": int(os.getenv("RATE_LIMIT_MAX_REQUESTS_PRODUCTS", DEFAULT_MAX_REQUESTS)),
        "burst": int(os.getenv("RATE_LIMIT_BURST_PRODUCTS", DEFAULT_BURST))
    },
    "/cart": {
        "window": int(os.getenv("RATE_LIMIT_WINDOW_CART", DEFAULT_WINDOW)),
        "max_requests": int(os.getenv("RATE_LIMIT_MAX_REQUESTS_CART", DEFAULT_MAX_REQUESTS)),
        "burst": int(os.getenv("RATE_LIMIT_BURST_CART", DEFAULT_BURST))
    }
}
logger.info(f"Loaded ENDPOINT_LIMITS: {ENDPOINT_LIMITS}")

# Metrics
REQUESTS_TOTAL = Counter(
    "http_requests_total", "Total HTTP requests", ["endpoint", "status"]
)
RATE_LIMIT_HITS = Counter(
    "rate_limit_hits_total", "Total rate limit hits", ["endpoint"]
)
TOKENS_REMAINING = Gauge(
    "tokens_remaining", "Remaining tokens in bucket", ["endpoint", "user_id"]
)
REDIS_FAILURES = Counter(
    "redis_failures_total", "Total Redis connection failures"
)

# Instrument FastAPI for default metrics
instrumentator = Instrumentator().instrument(app)

# Lua script for atomic token bucket check and update 
TOKEN_BUCKET_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local max_requests = tonumber(ARGV[3])
local burst = tonumber(ARGV[4])
local replenish_rate = max_requests / window

-- Get current tokens and last update time
local tokens = redis.call('HGET', key, 'tokens') or max_requests + burst
local last_update = redis.call('HGET', key, 'last_update') or now
tokens = tonumber(tokens)
last_update = tonumber(last_update)

-- Calculate tokens replenished since last update
local elapsed = now - last_update
local new_tokens = math.min(max_requests + burst, tokens + (elapsed * replenish_rate))

-- Check if enough tokens for this request
if new_tokens >= 1 then
    new_tokens = new_tokens - 1
    redis.call('HMSET', key, 'tokens', new_tokens, 'last_update', now)
    redis.call('EXPIRE', key, window)
    return 1  -- Success
else
    return 0  -- Rate limited
end
"""

# Declare token_bucket_sha and use_redis at module level
token_bucket_sha: Optional[str] = None
use_redis: bool = True  # Flag to toggle Redis usage

# In-memory token bucket for fallback
class InMemoryTokenBucket:
    def __init__(self, max_requests: int, burst: int, window: int, num_instances: int):
        self.max_requests = max_requests // num_instances  # Divide by number of instances
        self.burst = burst // num_instances
        self.window = window
        self.replenish_rate = self.max_requests / window
        self.buckets: Dict[str, tuple[float, float]] = {}  # key: (tokens, last_update)
        logger.info(f"In-memory bucket initialized: max_requests={self.max_requests}, burst={self.burst}, num_instances={num_instances}")

    def check_and_update(self, key: str, now: float) -> bool:
        tokens, last_update = self.buckets.get(key, (self.max_requests + self.burst, now))
        elapsed = now - last_update
        new_tokens = min(self.max_requests + self.burst, tokens + (elapsed * self.replenish_rate))
        logger.debug(f"In-memory bucket: key={key}, tokens={tokens}, elapsed={elapsed}, new_tokens={new_tokens}")
        if new_tokens >= 1:
            new_tokens -= 1
            self.buckets[key] = (new_tokens, now)
            logger.debug(f"Allowed: new_tokens={new_tokens}")
            return True
        logger.debug(f"Denied: new_tokens={new_tokens}")
        return False

fallback_buckets: Dict[str, InMemoryTokenBucket] = {}

@app.on_event("startup")
async def startup_event():
    global token_bucket_sha, use_redis
    try:
        await redis_client.ping()
        logger.info("Redis connection successful")
        # Load Lua script during startup
        token_bucket_sha = await redis_client.script_load(TOKEN_BUCKET_SCRIPT)
        logger.info("Token bucket script loaded")
        use_redis = True
    except Exception as e:
        REDIS_FAILURES.inc()
        logger.error(f"Redis connection failed: {e}. Using in-memory fallback.")
        token_bucket_sha = "in_memory"  # Flag for initial failure
        use_redis = False
    instrumentator.expose(app)  # Expose /metrics endpoint

class CartItem(BaseModel):
    item: str

@app.middleware("http")
async def rate_limit(request: Request, call_next):
    global use_redis
    user_id = request.headers.get("X-User-ID", "default")
    path = request.url.path  # e.g., "/products" or "/cart"
    now = time()

    # Get endpoint-specific limits, fall back to defaults
    limits = ENDPOINT_LIMITS.get(path, {
        "window": DEFAULT_WINDOW,
        "max_requests": DEFAULT_MAX_REQUESTS,
        "burst": DEFAULT_BURST
    })
    window = limits["window"]
    max_requests = limits["max_requests"]
    burst = limits["burst"]
    key = f"rate:{user_id}:{path}"

    allowed: Optional[bool] = None
    tokens_left: float = max_requests + burst  # Default for metrics if Redis fails

    if use_redis:  # Try Redis first
        try:
            allowed = await redis_client.evalsha(
                token_bucket_sha,
                1,  # Number of keys
                key,
                now,
                window,
                max_requests,
                burst
            )
            tokens_left_raw = await redis_client.hget(key, "tokens")
            tokens_left = float(tokens_left_raw) if tokens_left_raw is not None else max_requests + burst
        except Exception as e:
            REDIS_FAILURES.inc()
            logger.warning(f"Redis failed during request: {e}. Switching to in-memory fallback.")
            use_redis = False

    if not use_redis:  # Use in-memory fallback
        if path not in fallback_buckets:
            fallback_buckets[path] = InMemoryTokenBucket(max_requests, burst, window, NUM_INSTANCES)
        bucket = fallback_buckets[path]
        allowed = bucket.check_and_update(key, now)
        tokens_left = bucket.buckets.get(key, (bucket.max_requests + bucket.burst, now))[0]

    TOKENS_REMAINING.labels(endpoint=path, user_id=user_id).set(tokens_left)
    logger.debug(f"User {user_id} at {path}: Allowed={allowed}, Tokens={tokens_left}")

    if allowed is None:
        logger.error(f"Allowed is None for {key} - defaulting to deny")
        allowed = False

    if not allowed:
        RATE_LIMIT_HITS.labels(endpoint=path).inc()
        logger.info(f"Rate limit hit for {user_id} at {path}: No tokens available")
        REQUESTS_TOTAL.labels(endpoint=path, status="429").inc()
        return JSONResponse(
            status_code=429,
            content={"detail": f"Rate limit exceeded for {path}"}
        )

    response = await call_next(request)
    REQUESTS_TOTAL.labels(endpoint=path, status=str(response.status_code)).inc()
    return response

@app.get("/products")
async def get_products():
    return {"products": ["item1", "item2", "item3"]}

@app.post("/cart")
async def add_to_cart(cart_item: CartItem):
    logger.debug(f"Received POST to /cart with item: {cart_item.item}")
    return {"message": f"Added {cart_item.item} to cart"}

@app.get("/health")
async def health_check():
    redis_status = "up"
    try:
        await redis_client.ping()
    except Exception as e:
        redis_status = f"down: {str(e)}"
        REDIS_FAILURES.inc()
    return {
        "status": "healthy" if redis_status == "up" else "degraded",
        "redis": redis_status,
        "fallback": "active" if not use_redis else "inactive"
    }

@app.on_event("shutdown")
async def shutdown():
    logger.info("Closing Redis connection")
    try:
        await redis_client.close()
    except Exception as e:
        logger.warning(f"Error closing Redis: {e}")
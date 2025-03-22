import requests
import time

BASE_URL = "http://localhost:8000/products"
USER_ID = "test_user"
HEADERS = {"X-User-ID": USER_ID}
MAX_REQUESTS = 100
TEST_REQUESTS = 105

def test_rate_limit():
    print(f"Testing {MAX_REQUESTS} req/min for '{USER_ID}'")
    results = []
    start_time = time.time()
    for i in range(TEST_REQUESTS):
        try:
            response = requests.get(BASE_URL, headers=HEADERS, timeout=5)
            status = response.status_code
            print(f"Request {i+1}: {status}")
            results.append(status)
        except requests.RequestException as e:
            print(f"Request {i+1} failed: {e}")
            results.append(None)
        time.sleep(0.01)
    duration = time.time() - start_time
    print(f"\nCompleted in {duration:.2f} seconds")
    print(f"200s: {results.count(200)}")
    print(f"429s: {results.count(429)}")
    print(f"Other: {sum(1 for r in results if r not in [200, 429] and r is not None)}")
    print(f"Failed: {results.count(None)}")

if __name__ == "__main__":
    print("Ensure FastAPI is running...")
    time.sleep(2)
    test_rate_limit()
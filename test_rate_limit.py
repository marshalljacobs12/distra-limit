import requests
import time

BASE_URL = "http://localhost"
USER_ID = "test_user"
HEADERS = {"X-User-ID": USER_ID, "Content-Type": "application/json"}

ENDPOINTS = {
    "/products": {"max_requests": 100, "burst": 20, "test_requests": 130},
    "/cart": {"max_requests": 50, "burst": 10, "test_requests": 70}
}

def test_rate_limit(endpoint, max_requests, burst, test_requests):
    url = f"{BASE_URL}{endpoint}"
    print(f"\nTesting {max_requests} req/min + {burst} burst for '{USER_ID}' on {endpoint}")
    results = []
    start_time = time.time()
    for i in range(test_requests):
        try:
            if endpoint == "/products":
                response = requests.get(url, headers=HEADERS, timeout=5)
            else:  # /cart
                response = requests.post(
                    url,
                    headers=HEADERS,
                    json={"item": "test"},
                    timeout=5
                )
            status = response.status_code
            print(f"Request {i+1}: {status}")
            results.append(status)
        except requests.RequestException as e:
            print(f"Request {i+1} failed: {e}")
            results.append(None)
        time.sleep(0.01)  # Simulate burst
    duration = time.time() - start_time
    print(f"Completed in {duration:.2f} seconds")
    print(f"200s: {results.count(200)}")
    print(f"429s: {results.count(429)}")
    print(f"Other: {sum(1 for r in results if r not in [200, 429] and r is not None)}")
    print(f"Failed: {results.count(None)}")

if __name__ == "__main__":
    print("Ensure Docker Compose is running...")
    time.sleep(2)
    for endpoint, config in ENDPOINTS.items():
        test_rate_limit(endpoint, config["max_requests"], config["burst"], config["test_requests"])
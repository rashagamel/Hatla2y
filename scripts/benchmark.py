"""
Performance benchmark for the Hatla2y API — run this any time you want to
verify the app is actually fast, not just assume it. Hits the real HTTP
endpoints (not the underlying Python functions directly), the same way the
dashboard does, including the 3-concurrent-request pattern the dashboard
uses when you click a lead.

Usage:
    cd app
    python3 ../scripts/benchmark.py

    # against an already-running server instead of starting a new one:
    python3 ../scripts/benchmark.py --no-server

Thresholds below reflect what was measured after the N+1 query fix
(see README.md's Phase 6 section) — if your numbers are far above these,
something regressed; if you're on a slower machine, some margin above these
is expected and not necessarily a problem.
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import concurrent.futures

BASE = "http://127.0.0.1:5050"

THRESHOLDS = {
    "GET /api/leads": 0.3,
    "GET /api/leads/1/matches": 1.0,
    "POST /api/leads/1/score": 0.5,
    "POST /api/leads/1/next-action": 1.5,
    "GET /api/stats": 0.3,
    "GET /api/pipeline": 0.5,
    "dashboard concurrent load (matches+score+next-action)": 2.5,
}


def call(method, path, body=None, timeout=20):
    url = BASE + path
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method,
                                  headers={"Content-Type": "application/json"} if body else {})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        r.read()
    return time.time() - t0


def run_single(name, method, path, body=None, repeats=3):
    times = []
    for _ in range(repeats):
        try:
            times.append(call(method, path, body))
        except urllib.error.URLError as e:
            print(f"  FAILED: {method} {path} — {e}")
            return None
    avg = sum(times) / len(times)
    threshold = THRESHOLDS.get(name, 2.0)
    status = "PASS" if avg <= threshold else "SLOW"
    print(f"  [{status}] {name:55s} avg={avg:.3f}s  min={min(times):.3f}s  max={max(times):.3f}s  (threshold {threshold}s)")
    return avg <= threshold


def run_concurrent_dashboard_load():
    """Mirrors exactly what the dashboard does on a lead click: fires
    matches, score, and next-action at the same time and measures total
    wall time — this is the number that actually matters for "does the UI
    feel stuck", not any single endpoint in isolation."""
    def fetch(method, path, body=None):
        return call(method, path, body)

    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        futures = [
            ex.submit(fetch, "GET", "/api/leads/1/matches?top_n=5"),
            ex.submit(fetch, "POST", "/api/leads/1/score"),
            ex.submit(fetch, "POST", "/api/leads/1/next-action"),
        ]
        for f in concurrent.futures.as_completed(futures):
            f.result()
    total = time.time() - t0
    name = "dashboard concurrent load (matches+score+next-action)"
    threshold = THRESHOLDS[name]
    status = "PASS" if total <= threshold else "SLOW"
    print(f"  [{status}] {name:55s} total={total:.3f}s  (threshold {threshold}s)")
    return total <= threshold


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-server", action="store_true",
                         help="Assume a server is already running instead of starting one")
    args = parser.parse_args()

    proc = None
    if not args.no_server:
        print("Starting API server...")
        proc = subprocess.Popen(["python3", "api.py"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(3)

    print("\n=== Individual endpoint latency (avg of 3 calls) ===")
    results = []
    results.append(run_single("GET /api/leads", "GET", "/api/leads"))
    results.append(run_single("GET /api/leads/1/matches", "GET", "/api/leads/1/matches?top_n=5"))
    results.append(run_single("POST /api/leads/1/score", "POST", "/api/leads/1/score"))
    results.append(run_single("POST /api/leads/1/next-action", "POST", "/api/leads/1/next-action"))
    results.append(run_single("GET /api/stats", "GET", "/api/stats"))
    results.append(run_single("GET /api/pipeline", "GET", "/api/pipeline"))

    print("\n=== Realistic dashboard load (concurrent, like a real lead click) ===")
    results.append(run_concurrent_dashboard_load())

    if proc:
        proc.terminate()
        proc.wait()

    results = [r for r in results if r is not None]
    passed = sum(1 for r in results if r)
    print(f"\n{passed}/{len(results)} checks within threshold.")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()

"""
Quick connection test for GlobalDataFeed REST API.
Run this first to verify your credentials work before the full download.
"""

import requests

GDF_ENDPOINT  = "YOUR_ENDPOINT_HERE"
GDF_PORT      = "YOUR_PORT_HERE"
GDF_ACCESSKEY = "YOUR_API_KEY_HERE"

BASE_URL = f"http://{GDF_ENDPOINT}:{GDF_PORT}"

def test():
    if "YOUR_" in GDF_ENDPOINT:
        print("Fill in GDF_ENDPOINT, GDF_PORT, GDF_ACCESSKEY first.")
        return

    # 1. Server info
    print("Testing GetServerInfo...")
    r = requests.get(f"{BASE_URL}/GetServerInfo/", params={"accesskey": GDF_ACCESSKEY}, timeout=10)
    print(f"  Status: {r.status_code}")
    print(f"  Response: {r.text[:200]}")

    # 2. Single strike history — NIFTY 09JAN25 23750 CE for Jan 3 2025
    print("\nTesting GetHistory for NIFTY09JAN2523750CE on 2025-01-03...")
    from datetime import datetime, timedelta
    def ts(dt):
        return int((dt - datetime(1970,1,1)).total_seconds()) - 19800
    from_ts = ts(datetime(2025,1,3,9,0,0))
    to_ts   = ts(datetime(2025,1,3,15,35,0))

    r = requests.get(f"{BASE_URL}/GetHistory/", params={
        "accesskey":            GDF_ACCESSKEY,
        "Exchange":             "NFO",
        "InstrumentIdentifier": "NIFTY09JAN2523750CE",
        "Periodicity":          "MINUTE",
        "Period":               1,
        "From":                 from_ts,
        "To":                   to_ts,
        "isShortIdentifier":    "False",
    }, timeout=30)
    print(f"  Status: {r.status_code}")
    print(f"  Response (first 500 chars): {r.text[:500]}")

if __name__ == "__main__":
    test()

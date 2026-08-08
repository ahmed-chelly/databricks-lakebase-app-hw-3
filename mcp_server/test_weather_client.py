#!/usr/bin/env python
"""
Local smoke test for open_meteo_client.py.

Hits the real Open-Meteo API directly (no Databricks auth, no secrets
required) so you can sanity-check the adapter before wiring it into the MCP
server or deploying anything.

Run:
    python test_weather_client.py
"""

import sys

import open_meteo_client
from open_meteo_client import WeatherLookupError


def test_resolve_location():
    print("\n=== Testing resolve_location ===")
    try:
        place = open_meteo_client.resolve_location("Chicago")
        print(f"  Resolved: {place}")
        assert place["latitude"] and place["longitude"]
        return True
    except (WeatherLookupError, AssertionError) as e:
        print(f"  FAILED: {e}")
        return False


def test_get_current():
    print("\n=== Testing get_current ===")
    try:
        place = open_meteo_client.resolve_location("Austin, TX")
        current = open_meteo_client.get_current(place["latitude"], place["longitude"])
        print(f"  {place['display_name']}: {current}")
        assert current["temperature_f"] is not None
        return True
    except (WeatherLookupError, AssertionError) as e:
        print(f"  FAILED: {e}")
        return False


def test_get_daily_forecast():
    print("\n=== Testing get_daily_forecast ===")
    try:
        place = open_meteo_client.resolve_location("New York")
        forecast = open_meteo_client.get_daily_forecast(place["latitude"], place["longitude"], 5)
        for day in forecast:
            print(f"  {day}")
        assert len(forecast) == 5
        return True
    except (WeatherLookupError, AssertionError) as e:
        print(f"  FAILED: {e}")
        return False


def test_bad_location():
    print("\n=== Testing bad location returns a clean error ===")
    try:
        open_meteo_client.resolve_location("Nowhereville Zzyzx Qxqx")
        print("  FAILED: expected WeatherLookupError, got a result")
        return False
    except WeatherLookupError as e:
        print(f"  Correctly raised WeatherLookupError: {e}")
        return True


def main():
    results = [
        test_resolve_location(),
        test_get_current(),
        test_get_daily_forecast(),
        test_bad_location(),
    ]
    passed = sum(results)
    print(f"\n{passed}/{len(results)} passed")
    return all(results)


if __name__ == "__main__":
    sys.exit(0 if main() else 1)

"""Simple market data prototyping utilities.

This module contains lightweight example functions for local prototyping.
Replace with real connectors and robust logic for production use.
"""
from datetime import datetime, timedelta
import random

def generate_dummy_prices(symbol: str, start_date: str, end_date: str):
    """Generate a list of (date, price) tuples between start and end (inclusive).

    Args:
        symbol: ticker symbol (unused in dummy impl)
        start_date: ISO date string YYYY-MM-DD
        end_date: ISO date string YYYY-MM-DD

    Returns:
        List of dicts: [{"date": "YYYY-MM-DD", "price": float}, ...]
    """
    s = datetime.fromisoformat(start_date)
    e = datetime.fromisoformat(end_date)
    days = (e - s).days + 1
    prices = []
    base = 100.0
    for i in range(days):
        date = (s + timedelta(days=i)).date().isoformat()
        # random walk
        base += random.uniform(-1.5, 1.5)
        prices.append({"date": date, "price": round(base, 2)})
    return prices

def simple_moving_average(prices, window=5):
    """Compute simple moving average over price records.

    `prices` is a list of dicts with key `price`.
    Returns list of (date, sma).
    """
    vals = [p["price"] for p in prices]
    dates = [p["date"] for p in prices]
    sma = []
    for i in range(len(vals)):
        if i + 1 < window:
            sma.append({"date": dates[i], "sma": None})
        else:
            window_vals = vals[i + 1 - window:i + 1]
            sma.append({"date": dates[i], "sma": round(sum(window_vals) / window, 4)})
    return sma

if __name__ == "__main__":
    data = generate_dummy_prices("AAPL", "2026-01-01", "2026-01-10")
    print(data)
    print(simple_moving_average(data, window=3))

# price_fetcher.py
#
# Fetches real-time electricity prices from the Octopus Energy Agile tariff API.
#
# What is the Octopus Agile tariff?
#   Octopus Agile is a UK electricity tariff where the price changes every 30 minutes
#   based on wholesale electricity market prices. So at 2am when nobody's using power,
#   prices can drop to 2p/kWh or even go negative (they pay YOU to use electricity).
#   At 6pm when everyone gets home and puts the kettle on, it might jump to 35p/kWh.
#
# Why does this matter for smart energy management?
#   If your fog node knows the current price, it can make smarter decisions:
#     - "Price is cheap right now → charge the battery / charge the EV"
#     - "Price is expensive → use the battery instead of grid, delay the dishwasher"
#   This is called demand-side response and it's a big deal in smart grid research.
#   The Octopus API is free and public — no API key needed for the price data.
#   That's why I picked it for this project rather than making up fake prices.

import time
import requests
from datetime import datetime, timezone


# The Agile tariff API endpoint — returns half-hourly prices for the next 24 hours.
# Region A = East England (doesn't matter much for this project, any region works
# for demonstrating the concept).
OCTOPUS_API_URL = (
    "https://api.octopus.energy/v1/products/"
    "AGILE-FLEX-22-11-25/electricity-tariffs/"
    "E-1R-AGILE-FLEX-22-11-25-A/standard-unit-rates/"
)

FALLBACK_PRICE_PENCE = 25.0   # pence/kWh — UK average as of 2024, used if API fails
CACHE_DURATION_SECONDS = 1800  # 30 minutes — Agile prices only change every 30 min anyway


class PriceFetcher:

    def __init__(self):
        self._cached_price = None      # the last price we successfully fetched
        self._cache_timestamp = None   # when we fetched it (time.time() value)

    def _is_cache_valid(self):
        # Check if our cached price is still fresh enough to use.
        # No point hammering the API every 30 seconds when the price only
        # changes every 30 minutes — that would be wasteful and rude to their servers.
        if self._cached_price is None or self._cache_timestamp is None:
            return False
        age_seconds = time.time() - self._cache_timestamp
        return age_seconds < CACHE_DURATION_SECONDS

    def _fetch_from_api(self):
        # Actually call the Octopus API and parse the response.
        # Returns the price in pence/kWh, or None if anything goes wrong.
        #
        # Things that could go wrong here:
        #   - No internet connection (laptop on a train, etc.)
        #   - Octopus API is down (rare but happens)
        #   - API response format changes (they update their API occasionally)
        #   - Request times out (slow connection)
        # In all these cases we catch the exception and return None,
        # which tells get_current_price() to use the fallback instead.

        try:
            response = requests.get(OCTOPUS_API_URL, timeout=5)
            response.raise_for_status()  # raises an exception if status is 4xx or 5xx

            data = response.json()
            results = data.get("results", [])

            if not results:
                print("[FOG] Octopus API returned no price results.")
                return None

            # The API returns rates sorted by valid_from descending — most recent first.
            # We want the current half-hour slot, so we take the first result that
            # has already started (valid_from is in the past).
            now = datetime.now(timezone.utc)

            for rate in results:
                valid_from_str = rate.get("valid_from")
                if not valid_from_str:
                    continue

                valid_from = datetime.fromisoformat(valid_from_str)

                # Some Python versions need the UTC timezone to be explicit
                if valid_from.tzinfo is None:
                    valid_from = valid_from.replace(tzinfo=timezone.utc)

                if valid_from <= now:
                    # This is the most recent rate that has actually started
                    price = rate.get("value_inc_vat")  # price including VAT in pence/kWh
                    return float(price)

            # If we get here, all rates are in the future (unlikely but handle it)
            print("[FOG] Octopus API: could not find a current rate in the response.")
            return None

        except requests.exceptions.Timeout:
            print("[FOG] Octopus API request timed out (>5s). Using fallback price.")
            return None
        except requests.exceptions.ConnectionError:
            print("[FOG] No network connection — cannot reach Octopus API.")
            return None
        except requests.exceptions.HTTPError as e:
            print(f"[FOG] Octopus API returned an error: {e}")
            return None
        except (KeyError, ValueError, TypeError) as e:
            # This would catch cases where the response JSON structure changed
            print(f"[FOG] Could not parse Octopus API response: {e}")
            return None

    def get_current_price(self):
        # Main method — returns the current electricity price in pence/kWh.
        # Uses the cache if it's still fresh, otherwise fetches from the API.
        # Falls back to 25p/kWh if the API is unavailable.

        if self._is_cache_valid():
            # Cache is still good — no need to call the API again
            return self._cached_price

        # Cache is stale or empty — time to fetch a fresh price
        fetched_price = self._fetch_from_api()

        if fetched_price is not None:
            self._cached_price = fetched_price
            self._cache_timestamp = time.time()
            print(f"[FOG] Current electricity price: {fetched_price:.1f}p/kWh (live)")
            return self._cached_price
        else:
            # API failed — use fallback. We don't update the cache timestamp here
            # so we'll try the API again on the next call (rather than caching a failure
            # for 30 minutes, which would mean we'd never retry).
            print(f"[FOG] Using fallback price: {FALLBACK_PRICE_PENCE}p/kWh (API unavailable)")
            return FALLBACK_PRICE_PENCE

    def calculate_cost(self, power_kw, duration_minutes):
        # Work out how much it costs (in pence) to run a load of power_kw
        # for duration_minutes at the current electricity price.
        #
        # Formula: cost = power (kW) × time (hours) × price (p/kWh)
        #
        # Example: EV charger at 7.4kW running for 90 minutes at 18p/kWh
        #   = 7.4 × (90/60) × 18 = 7.4 × 1.5 × 18 = 199.8p = £1.99
        #
        # This is useful for the dashboard — telling the homeowner "your EV
        # session cost £2.14 so far" is much more useful than "7.4kW was drawn".

        price_pence = self.get_current_price()
        duration_hours = duration_minutes / 60
        cost_pence = power_kw * duration_hours * price_pence

        return round(cost_pence, 2)


# Quick manual test — run this file directly to check the API is reachable
if __name__ == "__main__":
    fetcher = PriceFetcher()

    price = fetcher.get_current_price()
    print(f"Price: {price}p/kWh")

    # Test the cost calculator with a few realistic examples
    ev_cost = fetcher.calculate_cost(power_kw=7.4, duration_minutes=120)
    print(f"EV charging (7.4kW for 2 hours): {ev_cost}p = £{ev_cost/100:.2f}")

    kettle_cost = fetcher.calculate_cost(power_kw=2.0, duration_minutes=3)
    print(f"Kettle (2kW for 3 minutes): {kettle_cost}p")

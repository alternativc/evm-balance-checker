"""
Centralised exchange (CEX) balance integrations.

To add an exchange: subclass BaseExchange in its own module, implement
_fetch_raw_balances(), and register the class in EXCHANGES below.
"""

from .base import BaseExchange, ExchangeBalance, ExchangeConfig, ExchangeError
from .bitfinex import BitfinexExchange
from .kraken import KrakenExchange

# Exchange name (the "exchange" field in EXCHANGES_CONFIG) -> implementation
EXCHANGES = {cls.name: cls for cls in (KrakenExchange, BitfinexExchange)}


def create_exchange(config: ExchangeConfig) -> BaseExchange:
    """Create the client for an exchange account"""
    if config.exchange not in EXCHANGES:
        raise ValueError(
            f"Unsupported exchange '{config.exchange}'. Supported exchanges: {sorted(EXCHANGES)}"
        )
    return EXCHANGES[config.exchange](config)


__all__ = [
    'BaseExchange', 'ExchangeBalance', 'ExchangeConfig', 'ExchangeError',
    'KrakenExchange', 'BitfinexExchange', 'EXCHANGES', 'create_exchange',
]

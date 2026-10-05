"""
Common interface for centralised exchange (CEX) balance integrations.

Every exchange implementation subclasses BaseExchange and only implements
_fetch_raw_balances(); symbol normalization, aggregation and filtering are shared,
so all exchanges look the same to the monitor.
"""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

import requests


@dataclass
class ExchangeConfig:
    """Configuration for one account on a centralised exchange"""
    exchange: str  # Exchange name, e.g. "kraken", "bitfinex"
    account: str  # Stable machine name of the account, e.g. "naka-main"
    label: str  # Human-readable display name
    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)
    tenant: str = ''  # Service provider the account belongs to
    service: str = ''
    pool: str = ''
    role: str = ''
    symbols: List[str] = field(default_factory=list)  # Only export these symbols; empty = all
    wallet_types: List[str] = field(default_factory=list)  # Only export these wallet types; empty = all
    symbol_map: Dict[str, str] = field(default_factory=dict)  # Extra exchange code -> symbol overrides
    api_url: Optional[str] = None  # Override the exchange's API base URL


@dataclass
class ExchangeBalance:
    """Balance of one asset in one wallet of an exchange account"""
    wallet_type: str
    token_symbol: str
    total: float
    available: Optional[float] = None  # None when the exchange doesn't report it


class ExchangeError(Exception):
    """Raised when balances could not be fetched from an exchange"""

    def __init__(self, message: str, error_type: str = 'api_error'):
        super().__init__(message)
        self.error_type = error_type  # Used as the error_type metric label


# (wallet_type, raw exchange asset code, total, available)
RawBalance = Tuple[str, str, float, Optional[float]]


class BaseExchange(ABC):
    """Base class for exchange balance clients"""

    name = ''  # Value of the "exchange" field in EXCHANGES_CONFIG
    default_api_url = ''
    default_wallet_type = ''  # Wallet type used for configured symbols the exchange doesn't return
    default_symbol_map: Dict[str, str] = {}  # Exchange asset code -> common symbol

    def __init__(self, config: ExchangeConfig):
        self.config = config
        self.api_url = (config.api_url or self.default_api_url).rstrip('/')
        self.symbol_map = {**self.default_symbol_map, **{k.upper(): v.upper() for k, v in config.symbol_map.items()}}
        self._last_nonce = 0

        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'EVMBalanceMonitor/1.0'})

    @abstractmethod
    def _fetch_raw_balances(self) -> Iterable[RawBalance]:
        """Fetch balances from the exchange API as raw (wallet_type, asset, total, available) tuples"""

    def _raise_for_api_error(self, data):
        """Raise ExchangeError if a decoded response body is an exchange-specific error"""

    def normalize_symbol(self, asset: str) -> str:
        """Map an exchange asset code to the common token symbol used in metrics"""
        asset = asset.upper()
        return self.symbol_map.get(asset, asset)

    def get_balances(self) -> List[ExchangeBalance]:
        """Fetch, normalize, aggregate and filter balances. Raises ExchangeError on failure."""
        balances: Dict[Tuple[str, str], ExchangeBalance] = {}
        for wallet_type, asset, total, available in self._fetch_raw_balances():
            symbol = self.normalize_symbol(asset)
            key = (wallet_type, symbol)
            if key in balances:
                # Several exchange codes can map to the same symbol, add them up
                existing = balances[key]
                existing.total += total
                if existing.available is not None and available is not None:
                    existing.available += available
                else:
                    existing.available = None
            else:
                balances[key] = ExchangeBalance(wallet_type, symbol, total, available)

        if self.config.wallet_types:
            balances = {k: v for k, v in balances.items() if k[0] in self.config.wallet_types}

        if self.config.symbols:
            symbols = {s.upper() for s in self.config.symbols}
            balances = {k: v for k, v in balances.items() if k[1] in symbols}
            # Report configured symbols the exchange didn't return as 0, so dashboards don't show gaps
            wallet_type = self.config.wallet_types[0] if self.config.wallet_types else self.default_wallet_type
            for symbol in symbols - {k[1] for k in balances}:
                balances[(wallet_type, symbol)] = ExchangeBalance(wallet_type, symbol, 0.0, 0.0)

        return list(balances.values())

    def _nonce(self) -> int:
        """Strictly increasing nonce in microseconds, as required by exchange private APIs"""
        nonce = max(int(time.time() * 1_000_000), self._last_nonce + 1)
        self._last_nonce = nonce
        return nonce

    def _request(self, method: str, path: str, **kwargs):
        """Send an HTTP request to the exchange and return the decoded JSON body"""
        try:
            response = self.session.request(method, self.api_url + path, timeout=30, **kwargs)
        except requests.exceptions.RequestException as e:
            raise ExchangeError(f"Request to {self.name} failed: {e}", 'request_failed')

        if response.status_code in (401, 403):
            raise ExchangeError(f"{self.name} rejected the API credentials (HTTP {response.status_code})", 'auth_error')

        try:
            data = response.json()
        except ValueError as e:  # json.JSONDecodeError and requests' JSONDecodeError are both ValueErrors
            raise ExchangeError(f"Invalid JSON from {self.name} (HTTP {response.status_code}): {e}", 'json_decode')

        # Exchanges return JSON error bodies with non-2xx statuses too, so let the subclass classify them first
        self._raise_for_api_error(data)
        if response.status_code >= 400:
            raise ExchangeError(f"{self.name} returned HTTP {response.status_code}: {data}", 'request_failed')
        return data

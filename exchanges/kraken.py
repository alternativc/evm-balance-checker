"""
Kraken balance client.

Uses the private BalanceEx endpoint (API key permission: "Query Funds").
Docs: https://docs.kraken.com/api/
"""

import base64
import hashlib
import hmac
import logging
import os
import urllib.parse
from typing import Dict, Iterable, Optional

from .base import BaseExchange, ExchangeError, RawBalance

# Same logger name as the monitor, so log lines are labelled consistently
logger = logging.getLogger(os.getenv('LOGGER_NAME', 'evm_balance_monitor'))


class KrakenExchange(BaseExchange):
    """Kraken spot, staking and earn balances"""

    name = 'kraken'
    default_api_url = 'https://api.kraken.com'
    default_wallet_type = 'spot'

    # Kraken altnames that differ from the common symbol
    default_symbol_map = {'XBT': 'BTC', 'XDG': 'DOGE'}

    # Asset code suffixes (e.g. "DOT.S", "USDT.F") -> wallet_type label
    WALLET_SUFFIXES = {
        'S': 'staked',  # Staked (legacy staking)
        'M': 'staked',  # Opt-in rewards
        'B': 'staked',  # Bonded staking
        'P': 'staked',  # Parachain staking
        'F': 'earn',  # Auto-earn / flexible
        'HOLD': 'hold',
    }

    def __init__(self, config):
        super().__init__(config)
        self._altnames: Optional[Dict[str, str]] = None  # Kraken asset code -> altname, e.g. XXBT -> XBT

    def _load_altnames(self):
        """Load asset altnames from the public Assets endpoint, once"""
        if self._altnames is not None:
            return
        try:
            data = self._request('GET', '/0/public/Assets')
            self._altnames = {code: info['altname'] for code, info in data['result'].items()}
        except (ExchangeError, KeyError, AttributeError) as e:
            # Not fatal: fall back to raw codes this cycle and retry next time
            logger.warning(f"Could not load Kraken asset names, using raw codes: {e}")

    def normalize_symbol(self, asset: str) -> str:
        """Map a Kraken asset code (without wallet suffix) to its common symbol, e.g. XXBT -> BTC"""
        altname = (self._altnames or {}).get(asset, asset)
        return super().normalize_symbol(altname)

    def _raise_for_api_error(self, data):
        errors = data.get('error') if isinstance(data, dict) else None
        if errors:
            auth_failed = any(e.startswith(('EAPI:Invalid key', 'EAPI:Invalid signature', 'EGeneral:Permission denied'))
                              for e in errors)
            raise ExchangeError(f"Kraken error: {', '.join(errors)}", 'auth_error' if auth_failed else 'api_error')

    def _private(self, path: str, data: Optional[Dict[str, str]] = None) -> dict:
        """Call a signed private endpoint and return its result"""
        nonce = str(self._nonce())
        postdata = urllib.parse.urlencode({'nonce': nonce, **(data or {})})

        # API-Sign = base64(HMAC-SHA512(base64decode(secret), path + SHA256(nonce + postdata)))
        message = path.encode() + hashlib.sha256((nonce + postdata).encode()).digest()
        try:
            secret = base64.b64decode(self.config.api_secret)
        except ValueError as e:
            raise ExchangeError(f"Kraken API secret is not valid base64: {e}", 'auth_error')
        signature = base64.b64encode(hmac.new(secret, message, hashlib.sha512).digest()).decode()

        response = self._request('POST', path, data=postdata, headers={
            'API-Key': self.config.api_key,
            'API-Sign': signature,
            'Content-Type': 'application/x-www-form-urlencoded; charset=utf-8',
        })

        if 'result' not in response:
            raise ExchangeError("Kraken response has no result", 'no_result')
        return response['result']

    def _fetch_raw_balances(self) -> Iterable[RawBalance]:
        self._load_altnames()
        result = self._private('/0/private/BalanceEx')

        for code, info in result.items():
            base, _, suffix = code.partition('.')
            wallet_type = self.WALLET_SUFFIXES.get(suffix, suffix.lower()) if suffix else self.default_wallet_type
            total = float(info.get('balance', 0))
            # Funds held in open orders are not available
            available = total - float(info.get('hold_trade', 0))
            yield wallet_type, base, total, available

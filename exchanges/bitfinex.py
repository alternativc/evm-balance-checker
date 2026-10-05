"""
Bitfinex balance client.

Uses the authenticated wallets endpoint (API key permission: Wallets "Get wallet balances and addresses").
Docs: https://docs.bitfinex.com/reference/rest-auth-wallets
"""

import hashlib
import hmac
import json
from typing import Iterable

from .base import BaseExchange, ExchangeError, RawBalance


class BitfinexExchange(BaseExchange):
    """Bitfinex exchange, margin and funding wallet balances"""

    name = 'bitfinex'
    default_api_url = 'https://api.bitfinex.com'
    default_wallet_type = 'exchange'

    # Bitfinex currency codes that differ from the common symbol
    default_symbol_map = {
        'UST': 'USDT',
        'UDC': 'USDC',
        'EUT': 'EURT',
        'TSD': 'TUSD',
        'DSH': 'DASH',
        'IOT': 'IOTA',
        'QTM': 'QTUM',
        'ALG': 'ALGO',
        'MNA': 'MANA',
    }

    # Bitfinex error code for invalid or unauthorised API keys
    AUTH_ERROR_CODE = 10100

    def normalize_symbol(self, asset: str) -> str:
        """Map a Bitfinex currency to its common symbol, e.g. UST -> USDT, USTF0 -> USDT"""
        asset = asset.upper()
        # Derivatives collateral is reported with an "F0" suffix, e.g. USTF0
        if asset.endswith('F0') and len(asset) > 2:
            asset = asset[:-2]
        return super().normalize_symbol(asset)

    def _raise_for_api_error(self, data):
        # Errors come back as ["error", code, "message"]
        if isinstance(data, list) and data and data[0] == 'error':
            code = data[1] if len(data) > 1 else None
            message = data[2] if len(data) > 2 else 'unknown error'
            error_type = 'auth_error' if code == self.AUTH_ERROR_CODE else 'api_error'
            raise ExchangeError(f"Bitfinex error {code}: {message}", error_type)

    def _private(self, path: str, body: dict) -> list:
        """Call a signed authenticated endpoint and return its result"""
        nonce = str(self._nonce())
        raw_body = json.dumps(body)

        # bfx-signature = hex(HMAC-SHA384(secret, "/api" + path + nonce + body))
        message = f"/api{path}{nonce}{raw_body}".encode()
        signature = hmac.new(self.config.api_secret.encode(), message, hashlib.sha384).hexdigest()

        response = self._request('POST', path, data=raw_body, headers={
            'bfx-nonce': nonce,
            'bfx-apikey': self.config.api_key,
            'bfx-signature': signature,
            'Content-Type': 'application/json',
        })

        if not isinstance(response, list):
            raise ExchangeError(f"Unexpected Bitfinex response: {response}", 'no_result')
        return response

    def _fetch_raw_balances(self) -> Iterable[RawBalance]:
        # Each wallet: [WALLET_TYPE, CURRENCY, BALANCE, UNSETTLED_INTEREST, AVAILABLE_BALANCE, ...]
        for wallet in self._private('/v2/auth/r/wallets', {}):
            wallet_type, currency, balance = wallet[0], wallet[1], wallet[2]
            available = wallet[4] if len(wallet) > 4 else None
            yield (
                wallet_type,
                currency,
                float(balance or 0),
                float(available) if available is not None else None,
            )

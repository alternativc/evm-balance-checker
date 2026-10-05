#!/usr/bin/env bash
# Example: run the EVM Balance Monitor locally with inline configuration.
# Uses public RPC endpoints (no API keys needed); replace them for production use.
# Metrics: http://localhost:8000/metrics
set -euo pipefail

cd "$(dirname "$0")"

# Chains to query (chain_id is optional, resolved via eth_chainId when omitted)
export CHAINS_CONFIG='[
  {
    "name": "ethereum",
    "rpc_url": "https://ethereum-rpc.publicnode.com",
    "native_token_symbol": "ETH",
    "decimals": 18,
    "chain_id": 1
  },
  {
    "name": "avalanche",
    "rpc_url": "https://api.avax.network/ext/bc/C/rpc",
    "native_token_symbol": "AVAX",
    "decimals": 18,
    "chain_id": 43114
  }
]'

# ERC-20 tokens, referenced from ADDRESSES_CONFIG by chain + symbol
export TOKENS_CONFIG='[
  {
    "chain": "ethereum",
    "symbol": "USDC",
    "contract_address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    "decimals": 6
  },
  {
    "chain": "avalanche",
    "symbol": "USDT",
    "contract_address": "0x9702230A8Ea53601f5cD2dc00fDBc13d4dF4A8c7",
    "decimals": 6
  },
  {
    "chain": "avalanche",
    "symbol": "XAUT",
    "contract_address": "0x2775d5105276781B4b85bA6eA6a6653bEeD1dd32",
    "decimals": 6
  }
]'

# Addresses to monitor; service, pool and role are optional metric labels
export ADDRESSES_CONFIG='[
      {
        "address": "0x248E847ffB53520273786d15985d1a5e43a598ac",
        "label": "NAKA Avalanche Gateway",
        "service": "naka-gateway",
        "pool": "Payment Gateway Contract",
        "role": "deposit",
        "chains": ["avalanche"],
        "tokens": [
          {"chain": "avalanche", "symbol": "USDT"},
          {"chain": "avalanche", "symbol": "XAUT"}
        ]
      },
      {
        "address": "0xc9d48D11f365F15E99ED39AB8D8989476C68Ea52",
        "label": "NAKA Avalanche Gateway",
        "service": "naka-gateway",
        "pool": "XAUT Payment Processing",
        "role": "incoming",
        "chains": ["avalanche"],
        "tokens": [
          {"chain": "avalanche", "symbol": "XAUT"}
        ]
      },
      {
        "address": "0xc9d48D11f365F15E99ED39AB8D8989476C68Ea52",
        "label": "NAKA Avalanche Gateway",
        "service": "naka-gateway",
        "pool": "XAUT Payment Processing",
        "role": "outgoing",
        "chains": ["avalanche"],
        "tokens": [
          {"chain": "avalanche", "symbol": "USDT"}
        ]
      }
    ]'

# Centralised exchange accounts, grouped by tenant. Each account carries its own API credentials.
# This file now contains secrets: keep it out of git.
export EXCHANGES_CONFIG='[
  {
    "tenant": "naka",
    "service": "naka-gateway",
    "pool": "cex",
    "accounts": [
      {
        "exchange": "bitfinex",
        "account": "naka-main",
        "label": "NAKA Bitfinex",
        "role": "hot",
        "api_key": "...",
        "api_secret": "...",
        "wallet_types": ["exchange", "funding"]
      }
    ]
  }
]'

# Running the same configuration in Docker:
# export the variables from this file in your shell first, then pass them through to the container.
# Always quote "${VAR}": the JSON configs contain spaces and newlines.
# `-e VAR` without a value also copies VAR from your shell; use it for EXCHANGES_CONFIG,
# which holds the API secrets, to keep them out of `ps` and shell history.
#
#   docker build -t evm-balance-monitor .
#   docker run --rm -p 8000:8000 \
#     -e CHAINS_CONFIG="${CHAINS_CONFIG}" \
#     -e TOKENS_CONFIG="${TOKENS_CONFIG}" \
#     -e ADDRESSES_CONFIG="${ADDRESSES_CONFIG}" \
#     -e EXCHANGES_CONFIG \
#     -e UPDATE_INTERVAL -e LOGGER_NAME -e ENABLE_TRANSFER_METRICS \
#     -e TRANSFER_CONFIRMATIONS -e TRANSFER_MAX_BLOCK_RANGE \
#     evm-balance-monitor

# Optional settings
export PROMETHEUS_PORT=${PROMETHEUS_PORT:-8000}
export UPDATE_INTERVAL=60
export LOGGER_NAME=evm_balance_monitor
export ENABLE_TRANSFER_METRICS=true
export TRANSFER_CONFIRMATIONS=5
export TRANSFER_MAX_BLOCK_RANGE=1000

# Prefer the project virtualenv when present
PYTHON=python3
if [ -x .venv/bin/python ]; then
  PYTHON=.venv/bin/python
fi


exec "$PYTHON" evm_balance_monitor.py

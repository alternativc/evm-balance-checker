# EVM Balance Monitor

A Python script that monitors native and ERC-20 token balances across EVM-compatible chains, plus balances on centralised exchanges (Kraken, Bitfinex), and exposes them via Prometheus metrics.

## Features

- Monitor native token balances across multiple EVM chains
- Monitor ERC-20 token balances (e.g. USDC, USDT) via `balanceOf` calls
- ERC-20 inflow/outflow counters from `Transfer` event logs
- Centralised exchange balances (Kraken, Bitfinex) per wallet type, with total and available amounts
- `service`, `pool` and `role` labels on every address and exchange account, for filtering and Grafana dashboards
- Convert hex balance responses to decimal format, using per-token decimals
- Expose metrics via Prometheus
- Configurable via environment variables
- Support for multiple chains: Ethereum, Polygon, Arbitrum, Optimism, and more
- Comprehensive error handling and logging

## Installation

1. Clone or download the script
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Configuration

Configure the monitoring via environment variables:

### Required Environment Variables

#### CHAINS_CONFIG
JSON array of chain configurations:
```json
[
  {
    "name": "ethereum",
    "rpc_url": "https://eth-mainnet.g.alchemy.com/v2/YOUR_API_KEY",
    "native_token_symbol": "ETH",
    "decimals": 18,
    "chain_id": 1
  }
]
```

| Field | Required | Description |
|-------|----------|-------------|
| `name` | yes | Chain name, used as the `chain` label and referenced from the other configs |
| `rpc_url` | yes | JSON-RPC endpoint |
| `native_token_symbol` | yes | Symbol of the native token, e.g. `ETH`, `AVAX` |
| `decimals` | no | Native token decimals (default: 18) |
| `chain_id` | no | Exported as the `chain_id` label; looked up via `eth_chainId` when omitted |

#### ADDRESSES_CONFIG
JSON array of addresses to monitor, with the chains to check native balances on and/or the tokens to check:
```json
[
  {
    "address": "0x742d35Cc6634C0532925a3b8D8A8E7E1aA9C0e5B",
    "label": "wallet_1",
    "service": "naka-gateway",
    "pool": "ethereum-main",
    "role": "deposit",
    "chains": ["ethereum", "polygon"],
    "tokens": [
      {"chain": "ethereum", "symbol": "USDC"}
    ]
  }
]
```

| Field | Required | Description |
|-------|----------|-------------|
| `address` | yes | Address to monitor |
| `label` | yes | Human-readable display name, used as a metric label |
| `service` | no | Stable machine name of the owning service, e.g. `naka-gateway` (default: empty) |
| `pool` | no | Pool the address belongs to, e.g. `avalanche-main` (default: empty) |
| `role` | no | Address role, e.g. `deposit`, `payout`, `treasury`, `hot`, `cold` (default: empty) |
| `chains` | no* | Chain names to monitor the **native** balance on |
| `tokens` | no* | List of `{"chain": ..., "symbol": ...}` entries referencing tokens defined in `TOKENS_CONFIG` |

\* At least one of `chains` or `tokens` must be non-empty.

**Note**: Each address specifies exactly which chains and tokens to monitor, avoiding unnecessary cross-chain scanning for efficiency. Monitoring a token on a chain does not require listing that chain in `chains` — only list it there if you also want the native balance.

All chain and token references are validated at startup; an unknown chain or token causes the monitor to exit with a configuration error.

### Optional Environment Variables

#### TOKENS_CONFIG
JSON array of ERC-20 token definitions. If unset, token monitoring is skipped.
```json
[
  {
    "chain": "ethereum",
    "symbol": "USDC",
    "contract_address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    "decimals": 6
  }
]
```

| Field | Required | Description |
|-------|----------|-------------|
| `chain` | yes | Chain name, must match a `name` in `CHAINS_CONFIG` |
| `symbol` | yes | Token symbol; `(chain, symbol)` must be unique |
| `contract_address` | yes | ERC-20 contract address on that chain |
| `decimals` | no | Token decimals (default: 18). Set correctly — e.g. USDC/USDT use 6 |

#### Other

- `PROMETHEUS_PORT`: Port for Prometheus metrics server (default: 8000)
- `UPDATE_INTERVAL`: Update interval in seconds (default: 60)
- `LOGGER_NAME`: Name shown in each log line, e.g. to tell instances apart (default: `evm_balance_monitor`)
- `ENABLE_TRANSFER_METRICS`: Scan ERC-20 `Transfer` logs for flow counters (default: `true`)
- `TRANSFER_CONFIRMATIONS`: Blocks to stay behind the chain head, to avoid counting transfers that get reorged out (default: 5)
- `TRANSFER_MAX_BLOCK_RANGE`: Maximum blocks per `eth_getLogs` request; lower it if your RPC rejects large ranges (default: 1000)

#### EXCHANGES_CONFIG
JSON array of centralised exchange accounts to monitor, grouped by **tenant**: the service provider that owns the accounts and operates on a given market or set of markets. A tenant can have any number of accounts on any exchanges, including several on the same exchange. If unset, exchange monitoring is skipped.
```json
[
  {
    "tenant": "acme",
    "service": "naka-gateway",
    "pool": "cex",
    "role": "treasury",
    "accounts": [
      {
        "exchange": "kraken",
        "account": "main",
        "label": "Acme Kraken",
        "api_key": "...",
        "api_secret": "...",
        "symbols": ["USDT", "BTC", "XAUT"]
      },
      {
        "exchange": "kraken",
        "account": "otc",
        "label": "Acme Kraken OTC",
        "role": "hot",
        "api_key": "...",
        "api_secret": "..."
      },
      {
        "exchange": "bitfinex",
        "account": "main",
        "label": "Acme Bitfinex",
        "api_key": "...",
        "api_secret": "...",
        "wallet_types": ["exchange", "funding"]
      }
    ]
  },
  {
    "tenant": "globex",
    "service": "naka-gateway",
    "pool": "cex",
    "accounts": [
      {"exchange": "bitfinex", "account": "main", "label": "Globex Bitfinex", "api_key": "...", "api_secret": "..."}
    ]
  }
]
```

Every field except `accounts` can be set on the tenant as a default for all its accounts, and overridden per account (above, the Kraken OTC account overrides `role`). An entry without `accounts` is a single account on its own, which keeps the earlier flat format working.

| Field | Required | Description |
|-------|----------|-------------|
| `tenant` | no | Tenant the account belongs to (default: empty) |
| `accounts` | no | Tenant only: list of the tenant's accounts |
| `exchange` | yes | `kraken` or `bitfinex` |
| `label` | yes | Human-readable display name, used as a metric label |
| `account` | no | Stable machine name of the account (default: the exchange name); `(tenant, exchange, account)` must be unique |
| `service`, `pool`, `role` | no | Same meaning as in `ADDRESSES_CONFIG` (default: empty) |
| `api_key`, `api_secret` | yes | API credentials of the account |
| `symbols` | no | Only export these symbols; configured symbols the exchange doesn't return are reported as 0 (default: all non-empty balances) |
| `wallet_types` | no | Only export these wallet types (default: all) |
| `symbol_map` | no | Extra exchange code → symbol mappings, e.g. `{"UST": "USDT"}` |
| `api_url` | no | Override the exchange API base URL |

Each account carries its own `api_key` and `api_secret`. The monitor exits with a configuration error if they are missing, or if two accounts on the same exchange use the same API key.

**Keeping secrets safe:** with inline credentials, `EXCHANGES_CONFIG` itself is a secret. Keep it out of git (e.g. in `.env`, which is ignored), and remember that anyone who can run `docker inspect` on the container can read its environment. Pass it with `-e EXCHANGES_CONFIG` (no value) so the secrets stay out of `ps` and shell history. The monitor never logs API keys or secrets, even for invalid JSON.

**API key permissions** — create a dedicated, read-only key per monitored account:
- **Kraken:** "Query Funds" only.
- **Bitfinex:** Wallets "Get wallet balances and addresses" only.

Use a key that no other application uses: both exchanges require a strictly increasing nonce per key, and sharing a key causes "invalid nonce" errors.

**Symbols and wallet types** are normalized so they match on-chain `token_symbol` values:

| Exchange | `wallet_type` values | Symbol normalization |
|----------|----------------------|----------------------|
| Kraken | `spot`, `staked` (`.S`, `.M`, `.B`, `.P` balances), `earn` (`.F`), `hold` | Kraken asset altnames, plus `XBT`→`BTC`, `XDG`→`DOGE` (e.g. `XXBT`→`BTC`, `ZUSD`→`USD`) |
| Bitfinex | `exchange`, `margin`, `funding` | `UST`→`USDT`, `UDC`→`USDC`, `EUT`→`EURT`, `TSD`→`TUSD` and a few others; the `F0` suffix of derivatives collateral is dropped |

## Usage

### Using Environment Variables Directly

```bash
export CHAINS_CONFIG='[{"name":"ethereum","rpc_url":"https://eth-mainnet.g.alchemy.com/v2/YOUR_API_KEY","native_token_symbol":"ETH","decimals":18}]'
export TOKENS_CONFIG='[{"chain":"ethereum","symbol":"USDC","contract_address":"0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48","decimals":6}]'
export ADDRESSES_CONFIG='[{"address":"0x742d35Cc6634C0532925a3b8D8A8E7E1aA9C0e5B","label":"wallet_1","chains":["ethereum"],"tokens":[{"chain":"ethereum","symbol":"USDC"}]}]'
python evm_balance_monitor.py
```

### Using .env File

1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` with your configuration:
   - Replace `YOUR_API_KEY` with your actual RPC API keys
   - Update addresses with the wallets you want to monitor
   - Adjust other settings as needed

3. Load environment variables and run:
   ```bash
   set -a && source .env && set +a
   python evm_balance_monitor.py
   ```

### Using Docker

#### Build and Run with Docker

```bash
# Build the image
docker build -t evm-balance-monitor .

# Run with environment variables
docker run -d \
  --name evm-balance-monitor \
  -p 8000:8000 \
  -e CHAINS_CONFIG='[{"name":"ethereum","rpc_url":"https://eth-mainnet.g.alchemy.com/v2/YOUR_API_KEY","native_token_symbol":"ETH","decimals":18}]' \
  -e TOKENS_CONFIG='[{"chain":"ethereum","symbol":"USDC","contract_address":"0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48","decimals":6}]' \
  -e ADDRESSES_CONFIG='[{"address":"0x742d35Cc6634C0532925a3b8D8A8E7E1aA9C0e5B","label":"wallet_1","chains":["ethereum"],"tokens":[{"chain":"ethereum","symbol":"USDC"}]}]' \
  evm-balance-monitor
```

#### Using Docker Compose (Recommended)

1. Copy and configure environment:
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

2. Run with Docker Compose:
   ```bash
   docker-compose up -d
   ```

3. View logs:
   ```bash
   docker-compose logs -f evm-balance-monitor
   ```

4. Stop the service:
   ```bash
   docker-compose down
   ```

#### Docker Environment Variables

You can override configurations using environment variables:

```bash
# API Keys
export ALCHEMY_ETH_API_KEY="your_ethereum_api_key"
export ALCHEMY_POLYGON_API_KEY="your_polygon_api_key"

# Wallet Addresses
export WALLET_1_ADDRESS="0xYourWalletAddress1"
export WALLET_2_ADDRESS="0xYourWalletAddress2"

# Optional Settings
export PROMETHEUS_PORT="8000"
export UPDATE_INTERVAL="60"

# Run with docker-compose
docker-compose up -d
```

## Metrics

The script exposes the following Prometheus metrics on `http://localhost:8000/metrics`:

| Metric | Labels | Description |
|--------|--------|-------------|
| `evm_balance_wei` | address labels | Token balance in wei (or the token's smallest unit) |
| `evm_balance_decimal` | address labels | Token balance in decimal form |
| `evm_transfer_amount_total` | address labels, `direction` | ERC-20 transfer amount (decimal) in or out of the address |
| `evm_transfer_count_total` | address labels, `direction` | Number of ERC-20 transfers in or out of the address |
| `evm_transfer_last_scanned_block` | `chain`, `chain_id` | Last block scanned for `Transfer` logs |
| `evm_balance_requests_total` | `chain`, `status` | Total number of balance requests |
| `evm_balance_errors_total` | `chain`, `error_type` | Total number of balance request errors |
| `evm_balance_last_update_timestamp` | `chain`, `address`, `label` | Timestamp of last successful balance update |
| `cex_balance_decimal` | exchange labels | Total exchange balance in decimal form |
| `cex_balance_available_decimal` | exchange labels | Balance not held in open orders; only when the exchange reports it |
| `cex_balance_requests_total` | `tenant`, `exchange`, `account`, `status` | Total number of exchange balance requests |
| `cex_balance_errors_total` | `tenant`, `exchange`, `account`, `error_type` | Total number of exchange balance request errors (`auth_error`, `api_error`, `request_failed`, ...) |
| `cex_balance_last_update_timestamp` | `tenant`, `exchange`, `account`, `label` | Timestamp of last successful exchange balance update |

**Address labels** are `service`, `pool`, `role`, `chain`, `chain_id`, `token_symbol`, `token_address`, `address` and `label`. Native balances have `token_address="native"`; ERC-20 balances carry the token's contract address. `direction` is `in` or `out`.

Example:
```
evm_balance_decimal{address="0x248E...",chain="avalanche",chain_id="43114",label="NAKA Avalanche Gateway",pool="avalanche-main",role="deposit",service="naka-gateway",token_address="0x9702230A...",token_symbol="USDT"} 10.0
```

#### Transfer (flow) counters

- Only ERC-20 transfers of tokens listed in an address's `tokens` are counted. Native (ETH, AVAX, ...) transfers emit no logs and are **not** counted; the `role` label (e.g. deposit-only vs payout-only wallets) gives an approximate native in/out picture.
- Counting starts from the chain head when the monitor starts; earlier history is not backfilled, and counters reset on restart. Use `increase()` / `rate()` in queries, which handle resets.
- A failed `eth_getLogs` range is retried on the next cycle without double counting.

#### Exchange balances

**Exchange labels** are `tenant`, `service`, `pool`, `role`, `exchange`, `account`, `wallet_type`, `token_symbol` and `label`. They share `service`, `pool`, `role`, `token_symbol` and `label` with the EVM metrics, so both can be combined in one query.

Example:
```
cex_balance_decimal{account="main",exchange="bitfinex",label="Acme Bitfinex",pool="cex",role="treasury",service="naka-gateway",tenant="acme",token_symbol="USDT",wallet_type="exchange"} 250.5
```

- If a request fails, the previous values stay exported; use `cex_balance_last_update_timestamp` to alert on stale data.
- Without `symbols`, series for assets that disappear from the account are removed.

### Example Prometheus Queries

```promql
# Current native balance for a specific wallet
evm_balance_decimal{chain="ethereum", label="wallet_1", token_address="native"}

# Current USDC balance for a specific wallet
evm_balance_decimal{chain="ethereum", label="wallet_1", token_symbol="USDC"}

# Total balance per token across all chains for a wallet
sum(evm_balance_decimal) by (label, token_symbol)

# Balance per token for each service and pool
sum(evm_balance_decimal) by (service, pool, token_symbol)

# Balance per token held by role (deposit, payout, treasury, ...)
sum(evm_balance_decimal{service="naka-gateway"}) by (role, token_symbol)

# USDT inflow and outflow per pool over the last 24h
sum(increase(evm_transfer_amount_total{token_symbol="USDT"}[24h])) by (pool, direction)

# Net flow per pool and token over the last 24h (in - out)
sum(increase(evm_transfer_amount_total{direction="in"}[24h])) by (pool, token_symbol)
  - sum(increase(evm_transfer_amount_total{direction="out"}[24h])) by (pool, token_symbol)

# Balance change rate over time
rate(evm_balance_decimal[5m])

# Error rate by chain
rate(evm_balance_errors_total[5m])

# USDT on each exchange, per wallet type
sum(cex_balance_decimal{token_symbol="USDT"}) by (exchange, wallet_type)

# Exchange balances per tenant and token, across all its accounts
sum(cex_balance_decimal) by (tenant, token_symbol)

# Failing exchange accounts over the last 15 minutes
sum(increase(cex_balance_errors_total[15m])) by (tenant, exchange, account, error_type) > 0

# Total USDT held by a service, on-chain and on exchanges combined
sum({__name__=~"evm_balance_decimal|cex_balance_decimal", service="naka-gateway", token_symbol="USDT"})

# On-chain vs exchange split per token
sum by (token_symbol) (evm_balance_decimal)
sum by (token_symbol) (cex_balance_decimal)

# Exchange accounts not updated in the last 10 minutes
time() - cex_balance_last_update_timestamp > 600
```

## Supported Chains

The script works with any EVM-compatible chain. Common examples:

- Ethereum
- Polygon
- Arbitrum
- Optimism
- Binance Smart Chain
- Avalanche
- Fantom

## Configuration Examples

### Multiple Chains Example

```json
[
  {
    "name": "ethereum",
    "rpc_url": "https://eth-mainnet.g.alchemy.com/v2/YOUR_ETH_API_KEY",
    "native_token_symbol": "ETH",
    "decimals": 18
  },
  {
    "name": "polygon",
    "rpc_url": "https://polygon-mainnet.g.alchemy.com/v2/YOUR_POLYGON_API_KEY",
    "native_token_symbol": "MATIC",
    "decimals": 18
  },
  {
    "name": "bsc",
    "rpc_url": "https://bsc-dataseed.binance.org",
    "native_token_symbol": "BNB",
    "decimals": 18
  }
]
```

### Multiple Tokens Example

```json
[
  {
    "chain": "ethereum",
    "symbol": "USDC",
    "contract_address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    "decimals": 6
  },
  {
    "chain": "ethereum",
    "symbol": "USDT",
    "contract_address": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
    "decimals": 6
  },
  {
    "chain": "polygon",
    "symbol": "USDC",
    "contract_address": "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
    "decimals": 6
  }
]
```

### Multiple Addresses Example

```json
[
  {
    "address": "0x742d35Cc6634C0532925a3b8D8A8E7E1aA9C0e5B",
    "label": "hot_wallet",
    "service": "naka-gateway",
    "pool": "main",
    "role": "hot",
    "chains": ["ethereum", "polygon", "arbitrum"],
    "tokens": [
      {"chain": "ethereum", "symbol": "USDC"},
      {"chain": "ethereum", "symbol": "USDT"},
      {"chain": "polygon", "symbol": "USDC"}
    ]
  },
  {
    "address": "0x1f9090aaE28b8a3dCeaDf281B0F12828e676c326",
    "label": "stablecoin_vault",
    "service": "naka-gateway",
    "pool": "main",
    "role": "treasury",
    "tokens": [
      {"chain": "ethereum", "symbol": "USDC"}
    ]
  },
  {
    "address": "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
    "label": "cold_wallet",
    "chains": ["ethereum"]
  },
  {
    "address": "0xA0b86991c31cC51b673C81C79B0b9B1B2A4e5a8b8",
    "label": "treasury",
    "chains": ["ethereum", "optimism"]
  }
]
```

## Docker Deployment

### Features
- **Multi-stage optimized** Dockerfile for smaller image size
- **Non-root user** for enhanced security
- **Health checks** for container monitoring
- **Resource limits** for production deployment
- **Log rotation** configured
- **Environment variable** support with defaults

### Production Deployment

For production environments, consider:

1. **Using a reverse proxy** (nginx/traefik) for SSL termination
2. **Monitoring** with Prometheus and Grafana
3. **Log aggregation** with ELK stack or similar
4. **Resource monitoring** with container metrics

Example production docker-compose with monitoring:

```yaml
version: '3.8'
services:
  evm-balance-monitor:
    image: evm-balance-monitor:latest
    restart: unless-stopped
    environment:
      - CHAINS_CONFIG=${CHAINS_CONFIG}
      - TOKENS_CONFIG=${TOKENS_CONFIG}
      - ADDRESSES_CONFIG=${ADDRESSES_CONFIG}
    deploy:
      resources:
        limits:
          memory: 256M
          cpus: '0.5'
    networks:
      - monitoring

  prometheus:
    image: prom/prometheus:latest
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
    networks:
      - monitoring

networks:
  monitoring:
    driver: bridge
```

### Chain-Specific Address Monitoring
- Each address configuration specifies which chains (native) and tokens (ERC-20) to monitor
- Eliminates unnecessary cross-chain scanning
- Reduces API calls and improves performance
- Allows different addresses to be monitored on different chains

## Logging

The script provides comprehensive logging. Log levels can be adjusted by modifying the `logging.basicConfig()` call in the script.

## Error Handling

The script includes robust error handling for:
- Network timeouts
- RPC errors
- JSON parsing errors
- Invalid hex values
- Missing configuration
- References to unknown chains or tokens in `ADDRESSES_CONFIG`
- Exchange API errors, rejected API keys and missing exchange credentials

## Adding an Exchange

Each exchange lives in its own module under `exchanges/` and subclasses `BaseExchange` (`exchanges/base.py`):

1. Create `exchanges/<name>.py` with a class that sets `name`, `default_api_url`, `default_wallet_type` and `default_symbol_map`, and implements `_fetch_raw_balances()`, yielding `(wallet_type, asset_code, total, available)` tuples. Override `_raise_for_api_error()` to classify the exchange's error responses.
2. Register the class in `EXCHANGES` in `exchanges/__init__.py`.

Symbol normalization, `symbols`/`wallet_types` filtering, metrics and error counting are shared, so a new exchange needs no changes to `evm_balance_monitor.py`.

All errors are logged and tracked in Prometheus metrics for monitoring.

## License

This project is open source and available under the MIT License.
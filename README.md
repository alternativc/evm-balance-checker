# EVM Balance Monitor

A Python script that monitors native token balances across EVM-compatible chains and exposes them via Prometheus metrics.

## Features

- Monitor native token balances across multiple EVM chains
- Convert hex balance responses to decimal format
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
    "decimals": 18
  }
]
```

#### ADDRESSES_CONFIG
JSON array of addresses to monitor with their target chains:
```json
[
  {
    "address": "0x742d35Cc6634C0532925a3b8D8A8E7E1aA9C0e5B",
    "label": "wallet_1",
    "chains": ["ethereum", "polygon"]
  }
]
```

**Note**: Each address specifies which chains to monitor it on, avoiding unnecessary cross-chain scanning for efficiency.

### Optional Environment Variables

- `PROMETHEUS_PORT`: Port for Prometheus metrics server (default: 8000)
- `UPDATE_INTERVAL`: Update interval in seconds (default: 60)

## Usage

### Using Environment Variables Directly

```bash
export CHAINS_CONFIG='[{"name":"ethereum","rpc_url":"https://eth-mainnet.g.alchemy.com/v2/YOUR_API_KEY","native_token_symbol":"ETH","decimals":18}]'
export ADDRESSES_CONFIG='[{"address":"0x742d35Cc6634C0532925a3b8D8A8E7E1aA9C0e5B","label":"wallet_1","chains":["ethereum"]}]'
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
  -e ADDRESSES_CONFIG='[{"address":"0x742d35Cc6634C0532925a3b8D8A8E7E1aA9C0e5B","label":"wallet_1","chains":["ethereum"]}]' \
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

- `evm_balance_wei`: Native token balance in wei
- `evm_balance_decimal`: Native token balance in decimal form
- `evm_balance_requests_total`: Total number of balance requests
- `evm_balance_errors_total`: Total number of balance request errors
- `evm_balance_last_update_timestamp`: Timestamp of last successful balance update

### Example Prometheus Queries

```promql
# Current ETH balance for a specific wallet
evm_balance_decimal{chain="ethereum", label="wallet_1"}

# Total balance across all chains for a wallet
sum(evm_balance_decimal) by (label)

# Balance change rate over time
rate(evm_balance_decimal[5m])

# Error rate by chain
rate(evm_balance_errors_total[5m])
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

### Multiple Addresses Example

```json
[
  {
    "address": "0x742d35Cc6634C0532925a3b8D8A8E7E1aA9C0e5B",
    "label": "hot_wallet",
    "chains": ["ethereum", "polygon", "arbitrum"]
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
- Each address configuration specifies which chains to monitor
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

All errors are logged and tracked in Prometheus metrics for monitoring.

## License

This project is open source and available under the MIT License.
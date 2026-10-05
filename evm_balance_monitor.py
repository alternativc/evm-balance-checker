#!/usr/bin/env python3
"""
EVM Balance Monitor with Prometheus Metrics
Monitors native and ERC-20 token balances and ERC-20 transfer flows
across EVM-compatible chains, and balances on centralised exchanges (see exchanges/)
"""

import json
import time
import logging
import os
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
import requests
from prometheus_client import start_http_server, Gauge, Counter
import threading

from exchanges import BaseExchange, ExchangeConfig, ExchangeError, create_exchange

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(os.getenv('LOGGER_NAME', 'evm_balance_monitor'))


@dataclass
class ChainConfig:
    """Configuration for a blockchain network"""
    name: str
    rpc_url: str
    native_token_symbol: str
    decimals: int = 18
    chain_id: Optional[str] = None  # Resolved via eth_chainId when not configured


@dataclass
class TokenConfig:
    """Configuration for an ERC-20 token on a specific chain"""
    chain: str
    symbol: str
    contract_address: str
    decimals: int = 18


@dataclass
class AddressConfig:
    """Configuration for an address to monitor"""
    address: str
    label: str
    chains: List[str] = field(default_factory=list)  # Chain names to monitor native balance on
    tokens: List[Dict[str, str]] = field(default_factory=list)  # [{"chain": ..., "symbol": ...}, ...]
    service: str = ''  # Stable machine name of the owning service, e.g. "naka-gateway"
    pool: str = ''  # Pool the address belongs to, e.g. "avalanche-main"
    role: str = ''  # deposit | payout | treasury | hot | cold ...


class EVMBalanceMonitor:
    """Monitor for EVM-compatible chain balances"""

    # Function selector for ERC-20 balanceOf(address), fixed across all standard tokens
    BALANCE_OF_SELECTOR = '0x70a08231'

    # keccak256("Transfer(address,address,uint256)"), the ERC-20 Transfer event topic
    TRANSFER_TOPIC = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'

    # token_address label value used for native balances
    NATIVE_TOKEN_ADDRESS = 'native'

    # Labels shared by all per-address balance and flow metrics
    ADDRESS_LABELS = ['service', 'pool', 'role', 'chain', 'chain_id',
                      'token_symbol', 'token_address', 'address', 'label']

    # Labels of centralised exchange balance metrics, mirroring ADDRESS_LABELS
    EXCHANGE_LABELS = ['tenant', 'service', 'pool', 'role', 'exchange', 'account',
                       'wallet_type', 'token_symbol', 'label']

    def __init__(self, chains: List[ChainConfig], addresses: List[AddressConfig],
                 tokens: Optional[List[TokenConfig]] = None,
                 enable_transfers: bool = True,
                 transfer_confirmations: int = 5,
                 transfer_max_block_range: int = 1000,
                 exchanges: Optional[List[BaseExchange]] = None):
        self.chains = {chain.name: chain for chain in chains}  # Convert to dict for efficient lookup
        self.addresses = addresses
        self.tokens = {(token.chain, token.symbol): token for token in (tokens or [])}

        self.enable_transfers = enable_transfers
        self.transfer_confirmations = transfer_confirmations
        self.transfer_max_block_range = transfer_max_block_range
        self.last_scanned_block: Dict[str, int] = {}  # chain name -> last block scanned for transfers

        self.exchanges = exchanges or []
        # (tenant, exchange, account) -> label values of the balance series exported last cycle
        self.exchange_series: Dict[tuple, set] = {}

        # Validate that all referenced chains and tokens exist
        self._validate_addresses()

        # Prometheus metrics
        self.balance_gauge = Gauge(
            'evm_balance_wei',
            'Token balance in wei (or smallest unit)',
            self.ADDRESS_LABELS
        )

        self.balance_decimal_gauge = Gauge(
            'evm_balance_decimal',
            'Token balance in decimal form',
            self.ADDRESS_LABELS
        )

        self.transfer_amount_counter = Counter(
            'evm_transfer_amount_total',
            'Total ERC-20 transfer amount in decimal form, from Transfer event logs',
            self.ADDRESS_LABELS + ['direction']
        )

        self.transfer_count_counter = Counter(
            'evm_transfer_count_total',
            'Total number of ERC-20 transfers, from Transfer event logs',
            self.ADDRESS_LABELS + ['direction']
        )

        self.last_scanned_block_gauge = Gauge(
            'evm_transfer_last_scanned_block',
            'Last block scanned for ERC-20 Transfer logs',
            ['chain', 'chain_id']
        )

        self.request_counter = Counter(
            'evm_balance_requests_total',
            'Total number of balance requests',
            ['chain', 'status']
        )

        self.error_counter = Counter(
            'evm_balance_errors_total',
            'Total number of balance request errors',
            ['chain', 'error_type']
        )

        self.last_update_timestamp = Gauge(
            'evm_balance_last_update_timestamp',
            'Timestamp of last successful balance update',
            ['chain', 'address', 'label']
        )

        self.exchange_balance_gauge = Gauge(
            'cex_balance_decimal',
            'Centralised exchange balance in decimal form',
            self.EXCHANGE_LABELS
        )

        self.exchange_available_gauge = Gauge(
            'cex_balance_available_decimal',
            'Centralised exchange balance available (not held in orders) in decimal form',
            self.EXCHANGE_LABELS
        )

        self.exchange_request_counter = Counter(
            'cex_balance_requests_total',
            'Total number of centralised exchange balance requests',
            ['tenant', 'exchange', 'account', 'status']
        )

        self.exchange_error_counter = Counter(
            'cex_balance_errors_total',
            'Total number of centralised exchange balance request errors',
            ['tenant', 'exchange', 'account', 'error_type']
        )

        self.exchange_last_update_timestamp = Gauge(
            'cex_balance_last_update_timestamp',
            'Timestamp of last successful centralised exchange balance update',
            ['tenant', 'exchange', 'account', 'label']
        )

        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json', 
            'User-Agent': 'EVMBalanceMonitor/1.0'
        })

    def _validate_addresses(self):
        """Validate that all chain and token references in addresses exist"""
        for address_config in self.addresses:
            for chain_name in address_config.chains:
                if chain_name not in self.chains:
                    raise ValueError(
                        f"Address '{address_config.label}' references unknown chain '{chain_name}'. "
                        f"Available chains: {list(self.chains.keys())}"
                    )
            for token_ref in address_config.tokens:
                chain_name = token_ref.get('chain')
                symbol = token_ref.get('symbol')
                if chain_name not in self.chains:
                    raise ValueError(
                        f"Address '{address_config.label}' references token on unknown chain '{chain_name}'. "
                        f"Available chains: {list(self.chains.keys())}"
                    )
                if (chain_name, symbol) not in self.tokens:
                    raise ValueError(
                        f"Address '{address_config.label}' references unknown token '{symbol}' on chain '{chain_name}'. "
                        f"Available tokens: {list(self.tokens.keys())}"
                    )

    def hex_to_decimal(self, hex_value: str) -> int:
        """Convert hex string to decimal integer"""
        try:
            # Remove '0x' prefix if present
            if hex_value.startswith('0x'):
                hex_value = hex_value[2:]
            return int(hex_value, 16)
        except ValueError as e:
            logger.error(f"Failed to convert hex to decimal: {hex_value}, error: {e}")
            return 0

    def wei_to_decimal(self, wei_amount: int, decimals: int = 18) -> float:
        """Convert wei amount to decimal token amount"""
        return wei_amount / (10 ** decimals)

    def _rpc_request(self, chain: ChainConfig, method: str, params: list, address: str) -> Optional[Any]:
        """Make a JSON-RPC request against a chain and return the raw result, or None on failure"""
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": 1
        }

        try:
            response = self.session.post(
                chain.rpc_url,
                json=payload,
                timeout=30
            )
            response.raise_for_status()

            data = response.json()

            if 'error' in data:
                error_msg = data['error'].get('message', 'Unknown RPC error')
                logger.error(f"RPC error for {chain.name} - {address}: {error_msg}")
                self.error_counter.labels(chain=chain.name, error_type='rpc_error').inc()
                return None

            if 'result' not in data:
                logger.error(f"No result in response for {chain.name} - {address}")
                self.error_counter.labels(chain=chain.name, error_type='no_result').inc()
                return None

            self.request_counter.labels(chain=chain.name, status='success').inc()
            return data['result']

        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed for {chain.name} - {address}: {e}")
            self.error_counter.labels(chain=chain.name, error_type='request_failed').inc()
            self.request_counter.labels(chain=chain.name, status='failed').inc()
            return None
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error for {chain.name} - {address}: {e}")
            self.error_counter.labels(chain=chain.name, error_type='json_decode').inc()
            self.request_counter.labels(chain=chain.name, status='failed').inc()
            return None
        except Exception as e:
            logger.error(f"Unexpected error for {chain.name} - {address}: {e}")
            self.error_counter.labels(chain=chain.name, error_type='unexpected').inc()
            self.request_counter.labels(chain=chain.name, status='failed').inc()
            return None

    def get_balance(self, chain: ChainConfig, address: str) -> Optional[int]:
        """Get native currency balance for an address on a specific chain"""
        result = self._rpc_request(chain, "eth_getBalance", [address, "latest"], address)
        if result is None:
            return None
        return self.hex_to_decimal(result)

    def get_token_balance(self, chain: ChainConfig, token: TokenConfig, address: str) -> Optional[int]:
        """Get ERC-20 token balance for an address on a specific chain via eth_call"""
        padded_address = address.lower().replace('0x', '').zfill(64)
        call_data = self.BALANCE_OF_SELECTOR + padded_address
        call_params = [{"to": token.contract_address, "data": call_data}, "latest"]

        result = self._rpc_request(chain, "eth_call", call_params, address)
        if result is None:
            return None
        return self.hex_to_decimal(result)

    def _pad_address_topic(self, address: str) -> str:
        """Left-pad an address to a 32-byte log topic"""
        return '0x' + address.lower().replace('0x', '').zfill(64)

    def _address_labels(self, address_config: AddressConfig, chain: ChainConfig,
                        token_symbol: str, token_address: str) -> Dict[str, str]:
        """Build the label set shared by per-address balance and flow metrics"""
        return {
            'service': address_config.service,
            'pool': address_config.pool,
            'role': address_config.role,
            'chain': chain.name,
            'chain_id': chain.chain_id,
            'token_symbol': token_symbol,
            'token_address': token_address,
            'address': address_config.address,
            'label': address_config.label,
        }

    def _resolve_chain_ids(self):
        """Fill in chain_id via eth_chainId for chains that don't configure it"""
        for chain in self.chains.values():
            if chain.chain_id is not None:
                continue
            result = self._rpc_request(chain, "eth_chainId", [], 'chain_id')
            if result is None:
                logger.warning(f"Could not resolve chain_id for {chain.name}, skipping it this cycle")
                continue
            chain.chain_id = str(self.hex_to_decimal(result))
            logger.info(f"Resolved chain_id for {chain.name}: {chain.chain_id}")

    def _record_balance(self, address_config: AddressConfig, chain: ChainConfig,
                        token_symbol: str, token_address: str,
                        balance_wei: int, decimals: int):
        """Update Prometheus metrics for a single balance reading"""
        balance_decimal = self.wei_to_decimal(balance_wei, decimals)
        labels = self._address_labels(address_config, chain, token_symbol, token_address)

        self.balance_gauge.labels(**labels).set(balance_wei)
        self.balance_decimal_gauge.labels(**labels).set(balance_decimal)

        self.last_update_timestamp.labels(
            chain=chain.name,
            address=address_config.address,
            label=address_config.label
        ).set(time.time())

        logger.info(
            f"Updated balance for {address_config.label} ({address_config.address}) on {chain.name}: "
            f"{balance_decimal:.6f} {token_symbol}"
        )

    def _chain_token_map(self) -> Dict[str, list]:
        """Map chain name -> (address config, token) pairs to monitor"""
        chain_token_map = {}
        for address_config in self.addresses:
            for token_ref in address_config.tokens:
                chain_name = token_ref['chain']
                token = self.tokens[(chain_name, token_ref['symbol'])]
                chain_token_map.setdefault(chain_name, []).append((address_config, token))
        return chain_token_map

    def _get_logs(self, chain: ChainConfig, from_block: int, to_block: int,
                  contracts: List[str], topics: list) -> Optional[list]:
        """Fetch logs for a block range via eth_getLogs"""
        log_filter = {
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
            "address": contracts,
            "topics": topics
        }
        return self._rpc_request(chain, "eth_getLogs", [log_filter], 'transfer-scan')

    def _scan_transfers(self, chain_token_map: Dict[str, list]):
        """Increment flow counters from ERC-20 Transfer logs since the last scanned block"""
        for chain_name, address_token_pairs in chain_token_map.items():
            chain = self.chains[chain_name]
            if chain.chain_id is None:
                continue

            result = self._rpc_request(chain, "eth_blockNumber", [], 'transfer-scan')
            if result is None:
                continue
            safe_block = self.hex_to_decimal(result) - self.transfer_confirmations

            # First cycle: start from the current block, history before startup is not backfilled
            if chain_name not in self.last_scanned_block:
                self.last_scanned_block[chain_name] = safe_block
                logger.info(f"Transfer scanning for {chain_name} starts after block {safe_block}")
                continue

            # (token contract, holder) -> (address config, token), both lowercased
            watched = {(token.contract_address.lower(), address_config.address.lower()): (address_config, token)
                       for address_config, token in address_token_pairs}
            contracts = sorted({contract for contract, _ in watched})
            holder_topics = sorted({self._pad_address_topic(holder) for _, holder in watched})

            from_block = self.last_scanned_block[chain_name] + 1
            while from_block <= safe_block:
                to_block = min(from_block + self.transfer_max_block_range - 1, safe_block)

                # Fetch both directions before counting, so a failed range is retried without double counting
                out_logs = self._get_logs(chain, from_block, to_block, contracts,
                                          [self.TRANSFER_TOPIC, holder_topics])
                in_logs = self._get_logs(chain, from_block, to_block, contracts,
                                         [self.TRANSFER_TOPIC, None, holder_topics])
                if out_logs is None or in_logs is None:
                    logger.warning(f"Transfer scan failed for {chain_name} at blocks {from_block}-{to_block}, will retry")
                    break

                for direction, logs, topic_index in (('out', out_logs, 1), ('in', in_logs, 2)):
                    for log in logs:
                        topics = log.get('topics', [])
                        if len(topics) != 3:  # ERC-721 Transfer has 4 topics
                            continue
                        holder = '0x' + topics[topic_index][-40:].lower()
                        match = watched.get((log.get('address', '').lower(), holder))
                        if match is None:
                            continue
                        address_config, token = match
                        amount = self.wei_to_decimal(self.hex_to_decimal(log.get('data') or '0x0'), token.decimals)
                        labels = self._address_labels(address_config, chain, token.symbol, token.contract_address)
                        self.transfer_amount_counter.labels(direction=direction, **labels).inc(amount)
                        self.transfer_count_counter.labels(direction=direction, **labels).inc()

                logger.info(
                    f"Scanned {chain_name} blocks {from_block}-{to_block}: "
                    f"{len(in_logs)} in, {len(out_logs)} out transfers"
                )
                self.last_scanned_block[chain_name] = to_block
                self.last_scanned_block_gauge.labels(chain=chain_name, chain_id=chain.chain_id).set(to_block)
                from_block = to_block + 1

                # Small delay between requests to avoid rate limiting
                time.sleep(0.1)

    def _update_exchange_balances(self):
        """Fetch balances from every configured exchange account and update cex_* metrics"""
        for client in self.exchanges:
            config = client.config
            account_key = (config.tenant, config.exchange, config.account)
            name = '/'.join(part for part in account_key if part)
            logger.info(f"Updating exchange balances for {config.label} ({name})")

            try:
                balances = client.get_balances()
            except ExchangeError as e:
                logger.error(f"Failed to get balances for {config.label} ({name}): {e}")
                self.exchange_error_counter.labels(tenant=config.tenant, exchange=config.exchange, account=config.account, error_type=e.error_type).inc()
                self.exchange_request_counter.labels(tenant=config.tenant, exchange=config.exchange, account=config.account, status='failed').inc()
                continue
            except Exception as e:
                logger.error(f"Unexpected error for {config.label} ({name}): {e}")
                self.exchange_error_counter.labels(tenant=config.tenant, exchange=config.exchange, account=config.account, error_type='unexpected').inc()
                self.exchange_request_counter.labels(tenant=config.tenant, exchange=config.exchange, account=config.account, status='failed').inc()
                continue

            self.exchange_request_counter.labels(tenant=config.tenant, exchange=config.exchange, account=config.account, status='success').inc()

            series = set()
            for balance in balances:
                labels = {
                    'tenant': config.tenant,
                    'service': config.service,
                    'pool': config.pool,
                    'role': config.role,
                    'exchange': config.exchange,
                    'account': config.account,
                    'wallet_type': balance.wallet_type,
                    'token_symbol': balance.token_symbol,
                    'label': config.label,
                }
                self.exchange_balance_gauge.labels(**labels).set(balance.total)
                if balance.available is not None:
                    self.exchange_available_gauge.labels(**labels).set(balance.available)
                else:
                    try:
                        self.exchange_available_gauge.remove(*(labels[name] for name in self.EXCHANGE_LABELS))
                    except KeyError:
                        pass
                series.add(tuple(labels[name] for name in self.EXCHANGE_LABELS))

            # Drop series for assets that disappeared from the account since the last cycle
            for label_values in self.exchange_series.get(account_key, set()) - series:
                for gauge in (self.exchange_balance_gauge, self.exchange_available_gauge):
                    try:
                        gauge.remove(*label_values)
                    except KeyError:
                        pass
            self.exchange_series[account_key] = series

            self.exchange_last_update_timestamp.labels(
                tenant=config.tenant, exchange=config.exchange, account=config.account, label=config.label
            ).set(time.time())
            logger.info(f"Updated {len(balances)} balances for {config.label} ({name})")

    def update_metrics(self):
        """Update all balance metrics"""
        logger.info("Starting balance update cycle")

        self._resolve_chain_ids()

        # Create a mapping of chain -> addresses to monitor natively, to minimize requests
        chain_address_map = {}
        for address_config in self.addresses:
            for chain_name in address_config.chains:
                chain_address_map.setdefault(chain_name, []).append(address_config)

        chain_token_map = self._chain_token_map()

        # Process native balances, one chain at a time
        for chain_name, address_configs in chain_address_map.items():
            chain = self.chains[chain_name]
            if chain.chain_id is None:
                continue
            logger.info(f"Updating native balances for chain: {chain_name} ({len(address_configs)} addresses)")

            for address_config in address_configs:
                address = address_config.address
                label = address_config.label

                balance_wei = self.get_balance(chain, address)

                if balance_wei is not None:
                    self._record_balance(
                        address_config=address_config,
                        chain=chain,
                        token_symbol=chain.native_token_symbol,
                        token_address=self.NATIVE_TOKEN_ADDRESS,
                        balance_wei=balance_wei,
                        decimals=chain.decimals
                    )
                else:
                    logger.warning(f"Failed to get balance for {label} ({address}) on {chain_name}")

                # Small delay between requests to avoid rate limiting
                time.sleep(0.1)

        # Process token balances, one chain at a time
        for chain_name, address_token_pairs in chain_token_map.items():
            chain = self.chains[chain_name]
            if chain.chain_id is None:
                continue
            logger.info(f"Updating token balances for chain: {chain_name} ({len(address_token_pairs)} lookups)")

            for address_config, token in address_token_pairs:
                address = address_config.address
                label = address_config.label

                balance_wei = self.get_token_balance(chain, token, address)

                if balance_wei is not None:
                    self._record_balance(
                        address_config=address_config,
                        chain=chain,
                        token_symbol=token.symbol,
                        token_address=token.contract_address,
                        balance_wei=balance_wei,
                        decimals=token.decimals
                    )
                else:
                    logger.warning(
                        f"Failed to get {token.symbol} balance for {label} ({address}) on {chain_name}"
                    )

                # Small delay between requests to avoid rate limiting
                time.sleep(0.1)

        if self.enable_transfers:
            self._scan_transfers(chain_token_map)

        self._update_exchange_balances()

        logger.info("Balance update cycle completed")

    def start_monitoring(self, update_interval: int = 60):
        """Start the monitoring loop"""
        logger.info(f"Starting monitoring with {update_interval}s interval")

        while True:
            try:
                self.update_metrics()
                time.sleep(update_interval)
            except KeyboardInterrupt:
                logger.info("Monitoring stopped by user")
                break
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                time.sleep(10)  # Wait before retrying


def load_chains_from_env() -> List[ChainConfig]:
    """Load chain configurations from environment variables"""
    chains = []

    # Get chains configuration from environment
    chains_config = os.getenv('CHAINS_CONFIG')
    if not chains_config:
        logger.error("CHAINS_CONFIG environment variable is required")
        raise ValueError("CHAINS_CONFIG environment variable is required")

    try:
        chains_data = json.loads(chains_config)
        for chain_data in chains_data:
            chain = ChainConfig(
                name=chain_data['name'],
                rpc_url=chain_data['rpc_url'],
                native_token_symbol=chain_data['native_token_symbol'],
                decimals=chain_data.get('decimals', 18),
                chain_id=str(chain_data['chain_id']) if chain_data.get('chain_id') is not None else None
            )
            chains.append(chain)
            logger.info(f"Loaded chain config: {chain.name}")
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in CHAINS_CONFIG: {e}")
        raise
    except KeyError as e:
        logger.error(f"Missing required field in CHAINS_CONFIG: {e}")
        raise

    return chains


def load_tokens_from_env() -> List[TokenConfig]:
    """Load ERC-20 token configurations from environment variables"""
    tokens = []

    # Get tokens configuration from environment (optional - not every deployment monitors tokens)
    tokens_config = os.getenv('TOKENS_CONFIG')
    if not tokens_config:
        logger.info("TOKENS_CONFIG not set, skipping token monitoring")
        return tokens

    try:
        tokens_data = json.loads(tokens_config)
        for token_data in tokens_data:
            token = TokenConfig(
                chain=token_data['chain'],
                symbol=token_data['symbol'],
                contract_address=token_data['contract_address'],
                decimals=token_data.get('decimals', 18)
            )
            tokens.append(token)
            logger.info(f"Loaded token config: {token.symbol} on {token.chain} ({token.contract_address})")
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in TOKENS_CONFIG: {e}")
        raise
    except KeyError as e:
        logger.error(f"Missing required field in TOKENS_CONFIG: {e}")
        raise

    return tokens


def _exchange_account_entries(exchanges_data: list) -> List[Dict[str, Any]]:
    """Flatten EXCHANGES_CONFIG into one dict per account.

    Entries with an "accounts" list are tenants: their other fields (tenant, service, pool, role,
    symbols, ...) are defaults that each account inherits and may override.
    Entries without "accounts" are single accounts.
    """
    if not isinstance(exchanges_data, list):
        raise ValueError("EXCHANGES_CONFIG must be a JSON array")

    entries = []
    for item in exchanges_data:
        if not isinstance(item, dict):
            raise ValueError("Each EXCHANGES_CONFIG entry must be a JSON object")
        if 'accounts' not in item:
            entries.append(item)
            continue

        defaults = {k: v for k, v in item.items() if k != 'accounts'}
        if not isinstance(item['accounts'], list) or not item['accounts']:
            raise ValueError(f"'accounts' must be a non-empty list for tenant '{item.get('tenant', '')}'")
        for account_data in item['accounts']:
            if not isinstance(account_data, dict):
                raise ValueError(f"Each account of tenant '{item.get('tenant', '')}' must be a JSON object")
            entries.append({**defaults, **account_data})
    return entries


def _exchange_credentials(exchange_data: Dict[str, Any], name: str) -> tuple:
    """Return the account's inline api_key and api_secret"""
    if 'api_key_env' in exchange_data or 'api_secret_env' in exchange_data:
        raise ValueError(
            f"Exchange account '{name}': 'api_key_env'/'api_secret_env' are no longer supported, "
            f"set 'api_key' and 'api_secret' in the account"
        )
    api_key = exchange_data.get('api_key')
    api_secret = exchange_data.get('api_secret')
    if not api_key or not api_secret:
        raise ValueError(f"Exchange account '{name}' needs 'api_key' and 'api_secret'")
    return api_key, api_secret


def load_exchanges_from_env() -> List[ExchangeConfig]:
    """Load centralised exchange account configurations from environment variables"""
    exchanges = []

    # Get exchanges configuration from environment (optional - not every deployment monitors exchanges)
    exchanges_config = os.getenv('EXCHANGES_CONFIG')
    if not exchanges_config:
        logger.info("EXCHANGES_CONFIG not set, skipping exchange monitoring")
        return exchanges

    try:
        seen_accounts = set()
        seen_keys = {}
        for exchange_data in _exchange_account_entries(json.loads(exchanges_config)):
            if 'exchange' not in exchange_data:
                raise KeyError("'exchange' field is required")
            if 'label' not in exchange_data:
                raise KeyError("'label' field is required")

            exchange = exchange_data['exchange'].lower()
            tenant = exchange_data.get('tenant', '')
            account = exchange_data.get('account', exchange)
            name = f"{tenant + '/' if tenant else ''}{exchange}/{account}"
            if (tenant, exchange, account) in seen_accounts:
                raise ValueError(f"Duplicate exchange account '{name}', set a unique 'account'")
            seen_accounts.add((tenant, exchange, account))

            api_key, api_secret = _exchange_credentials(exchange_data, name)
            # Sharing a key between accounts breaks the exchanges' per-key nonce, and is usually a copy-paste mistake
            if (exchange, api_key) in seen_keys:
                raise ValueError(f"Exchange accounts '{seen_keys[(exchange, api_key)]}' and '{name}' use the same API key")
            seen_keys[(exchange, api_key)] = name

            for list_field in ('symbols', 'wallet_types'):
                if not isinstance(exchange_data.get(list_field, []), list):
                    raise ValueError(f"'{list_field}' must be a list for exchange account '{name}'")

            config = ExchangeConfig(
                exchange=exchange,
                account=account,
                label=exchange_data['label'],
                api_key=api_key,
                api_secret=api_secret,
                tenant=tenant,
                service=exchange_data.get('service', ''),
                pool=exchange_data.get('pool', ''),
                role=exchange_data.get('role', ''),
                symbols=exchange_data.get('symbols', []),
                wallet_types=exchange_data.get('wallet_types', []),
                symbol_map=exchange_data.get('symbol_map', {}),
                api_url=exchange_data.get('api_url')
            )
            exchanges.append(config)
            logger.info(
                f"Loaded exchange config: {config.label} ({name}), "
                f"symbols: {', '.join(config.symbols) or 'all'}"
            )
    except json.JSONDecodeError as e:
        # Only the position is logged: the config may contain API secrets
        logger.error(f"Invalid JSON in EXCHANGES_CONFIG at line {e.lineno}, column {e.colno}: {e.msg}")
        raise ValueError(f"Invalid JSON in EXCHANGES_CONFIG at line {e.lineno}, column {e.colno}: {e.msg}")
    except (KeyError, ValueError) as e:
        logger.error(f"Invalid exchange configuration: {e}")
        raise

    return exchanges


def load_addresses_from_env() -> List[AddressConfig]:
    """Load address configurations from environment variables"""
    addresses = []

    # Get addresses configuration from environment
    addresses_config = os.getenv('ADDRESSES_CONFIG')
    if not addresses_config:
        logger.error("ADDRESSES_CONFIG environment variable is required")
        raise ValueError("ADDRESSES_CONFIG environment variable is required")

    try:
        addresses_data = json.loads(addresses_config)
        for address_data in addresses_data:
            # Validate required fields
            if 'address' not in address_data:
                raise KeyError("'address' field is required")
            if 'label' not in address_data:
                raise KeyError("'label' field is required")

            chains = address_data.get('chains', [])
            tokens = address_data.get('tokens', [])

            # Validate chains is a list
            if not isinstance(chains, list):
                raise ValueError(f"'chains' must be a list for address {address_data['address']}")

            # Validate tokens is a list of {"chain", "symbol"} entries
            if not isinstance(tokens, list):
                raise ValueError(f"'tokens' must be a list for address {address_data['address']}")
            for token_ref in tokens:
                if 'chain' not in token_ref or 'symbol' not in token_ref:
                    raise ValueError(
                        f"Each token entry for address {address_data['address']} requires 'chain' and 'symbol'"
                    )

            if not chains and not tokens:
                raise ValueError(
                    f"Address {address_data['address']} must specify at least one of 'chains' or 'tokens'"
                )

            address = AddressConfig(
                address=address_data['address'],
                label=address_data['label'],
                chains=chains,
                tokens=tokens,
                service=address_data.get('service', ''),
                pool=address_data.get('pool', ''),
                role=address_data.get('role', '')
            )
            addresses.append(address)
            logger.info(
                f"Loaded address config: {address.label} ({address.address}) "
                f"for chains: {', '.join(address.chains) or 'none'}, "
                f"tokens: {', '.join(t['symbol'] + '@' + t['chain'] for t in address.tokens) or 'none'}"
            )
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in ADDRESSES_CONFIG: {e}")
        raise
    except (KeyError, ValueError) as e:
        logger.error(f"Invalid address configuration: {e}")
        raise

    return addresses


def main():
    """Main function"""
    logger.info("Starting EVM Balance Monitor")

    # Load configuration from environment variables
    try:
        chains = load_chains_from_env()
        tokens = load_tokens_from_env()
        addresses = load_addresses_from_env()
        exchange_configs = load_exchanges_from_env()
        exchanges = [create_exchange(config) for config in exchange_configs]
    except (ValueError, json.JSONDecodeError, KeyError) as e:
        logger.error(f"Configuration error: {e}")
        logger.error("Please check your environment variables. See README for examples.")
        return

    # Get optional configuration from environment
    prometheus_port = int(os.getenv('PROMETHEUS_PORT', '8000'))
    update_interval = int(os.getenv('UPDATE_INTERVAL', '60'))
    enable_transfers = os.getenv('ENABLE_TRANSFER_METRICS', 'true').lower() in ('1', 'true', 'yes')
    transfer_confirmations = int(os.getenv('TRANSFER_CONFIRMATIONS', '5'))
    transfer_max_block_range = int(os.getenv('TRANSFER_MAX_BLOCK_RANGE', '1000'))

    logger.info(
        f"Loaded {len(chains)} chains, {len(tokens)} tokens, {len(addresses)} addresses "
        f"and {len(exchanges)} exchange accounts"
    )
    logger.info(f"Prometheus port: {prometheus_port}")
    logger.info(f"Update interval: {update_interval}s")
    logger.info(f"Transfer metrics: {'enabled' if enable_transfers else 'disabled'}")

    # Initialize monitor
    monitor = EVMBalanceMonitor(
        chains, addresses, tokens,
        enable_transfers=enable_transfers,
        transfer_confirmations=transfer_confirmations,
        transfer_max_block_range=transfer_max_block_range,
        exchanges=exchanges
    )

    # Start Prometheus HTTP server
    start_http_server(prometheus_port)
    logger.info(f"Prometheus metrics server started on port {prometheus_port}")
    logger.info(f"Metrics available at http://localhost:{prometheus_port}/metrics")

    # Start monitoring in a separate thread
    monitoring_thread = threading.Thread(
        target=monitor.start_monitoring,
        kwargs={'update_interval': update_interval}
    )
    monitoring_thread.daemon = True
    monitoring_thread.start()

    try:
        # Keep main thread alive
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")


if __name__ == "__main__":
    main()
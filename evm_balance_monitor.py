#!/usr/bin/env python3
"""
EVM Balance Monitor with Prometheus Metrics
Monitors native token balances across EVM-compatible chains
"""

import json
import time
import logging
import os
from typing import Dict, List, Optional
from dataclasses import dataclass
import requests
from prometheus_client import start_http_server, Gauge, Counter
import threading

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@dataclass
class ChainConfig:
    """Configuration for a blockchain network"""
    name: str
    rpc_url: str
    native_token_symbol: str
    decimals: int = 18

@dataclass
class AddressConfig:
    """Configuration for an address to monitor"""
    address: str
    label: str
    chains: List[str]  # List of chain names to monitor this address on

class EVMBalanceMonitor:
    """Monitor for EVM-compatible chain balances"""
    
    def __init__(self, chains: List[ChainConfig], addresses: List[AddressConfig]):
        self.chains = {chain.name: chain for chain in chains}  # Convert to dict for efficient lookup
        self.addresses = addresses
        
        # Validate that all referenced chains exist
        self._validate_address_chains()
        
        # Prometheus metrics
        self.balance_gauge = Gauge(
            'evm_balance_wei',
            'Native token balance in wei',
            ['chain', 'address', 'label', 'token_symbol']
        )
        
        self.balance_decimal_gauge = Gauge(
            'evm_balance_decimal',
            'Native token balance in decimal form',
            ['chain', 'address', 'label', 'token_symbol']
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
        
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'User-Agent': 'EVMBalanceMonitor/1.0'
        })
    
    def _validate_address_chains(self):
        """Validate that all chain references in addresses exist"""
        for address_config in self.addresses:
            for chain_name in address_config.chains:
                if chain_name not in self.chains:
                    raise ValueError(
                        f"Address '{address_config.label}' references unknown chain '{chain_name}'. "
                        f"Available chains: {list(self.chains.keys())}"
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
    
    def get_balance(self, chain: ChainConfig, address: str) -> Optional[int]:
        """Get balance for an address on a specific chain"""
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_getBalance",
            "params": [address, "latest"],
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
            
            hex_balance = data['result']
            balance_wei = self.hex_to_decimal(hex_balance)
            
            self.request_counter.labels(chain=chain.name, status='success').inc()
            return balance_wei
            
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
    
    def update_metrics(self):
        """Update all balance metrics"""
        logger.info("Starting balance update cycle")
        
        # Create a mapping of chain -> addresses to minimize requests
        chain_address_map = {}
        for address_config in self.addresses:
            for chain_name in address_config.chains:
                if chain_name not in chain_address_map:
                    chain_address_map[chain_name] = []
                chain_address_map[chain_name].append(address_config)
        
        # Process each chain only once with its relevant addresses
        for chain_name, address_configs in chain_address_map.items():
            chain = self.chains[chain_name]
            logger.info(f"Updating balances for chain: {chain_name} ({len(address_configs)} addresses)")
            
            for address_config in address_configs:
                address = address_config.address
                label = address_config.label
                
                balance_wei = self.get_balance(chain, address)
                
                if balance_wei is not None:
                    balance_decimal = self.wei_to_decimal(balance_wei, chain.decimals)
                    
                    # Update Prometheus metrics
                    self.balance_gauge.labels(
                        chain=chain_name,
                        address=address,
                        label=label,
                        token_symbol=chain.native_token_symbol
                    ).set(balance_wei)
                    
                    self.balance_decimal_gauge.labels(
                        chain=chain_name,
                        address=address,
                        label=label,
                        token_symbol=chain.native_token_symbol
                    ).set(balance_decimal)
                    
                    self.last_update_timestamp.labels(
                        chain=chain_name,
                        address=address,
                        label=label
                    ).set(time.time())
                    
                    logger.info(
                        f"Updated balance for {label} ({address}) on {chain_name}: "
                        f"{balance_decimal:.6f} {chain.native_token_symbol}"
                    )
                else:
                    logger.warning(f"Failed to get balance for {label} ({address}) on {chain_name}")
                
                # Small delay between requests to avoid rate limiting
                time.sleep(0.1)
        
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
                decimals=chain_data.get('decimals', 18)
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
            if 'chains' not in address_data:
                raise KeyError("'chains' field is required")
            
            # Validate chains is a list
            if not isinstance(address_data['chains'], list):
                raise ValueError(f"'chains' must be a list for address {address_data['address']}")
            
            address = AddressConfig(
                address=address_data['address'],
                label=address_data['label'],
                chains=address_data['chains']
            )
            addresses.append(address)
            logger.info(f"Loaded address config: {address.label} ({address.address}) for chains: {', '.join(address.chains)}")
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
        addresses = load_addresses_from_env()
    except (ValueError, json.JSONDecodeError, KeyError) as e:
        logger.error(f"Configuration error: {e}")
        logger.error("Please check your environment variables. See README for examples.")
        return
    
    # Get optional configuration from environment
    prometheus_port = int(os.getenv('PROMETHEUS_PORT', '8000'))
    update_interval = int(os.getenv('UPDATE_INTERVAL', '60'))
    
    logger.info(f"Loaded {len(chains)} chains and {len(addresses)} addresses")
    logger.info(f"Prometheus port: {prometheus_port}")
    logger.info(f"Update interval: {update_interval}s")
    
    # Initialize monitor
    monitor = EVMBalanceMonitor(chains, addresses)
    
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
"""
MEME HUNTER - Solana Trading Client
===================================
Low-level Solana interaction for trading operations.
"""

import asyncio
import base64
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
import struct

import aiohttp
from solana.rpc.api import Client as SolanaClient
from solana.rpc.commitment import Confirmed, Finalized
from solana.rpc.types import TxOpts
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import Transaction
from spl.token.constants import TOKEN_PROGRAM_ID
from spl.token.instructions import get_associated_token_address, transfer
import base58

from config import config

logger = logging.getLogger(__name__)


@dataclass
class TradeResult:
    """Result of a trade operation"""
    success: bool
    signature: Optional[str] = None
    error: Optional[str] = None
    tokens_received: float = 0.0
    tokens_sold: float = 0.0
    sol_received: float = 0.0
    slippage_sol: float = 0.0
    price: float = 0.0
    gas_used: float = 0.0

    # TradingEngine historically consumed dict-like results. Keeping this
    # adapter makes both live and test clients interoperable.
    def get(self, key: str, default=None):
        return getattr(self, key, default)

    def as_dict(self) -> Dict:
        return {
            "success": self.success, "signature": self.signature, "error": self.error,
            "tokens_received": self.tokens_received, "tokens_sold": self.tokens_sold,
            "sol_received": self.sol_received, "slippage_sol": self.slippage_sol,
            "price": self.price, "gas_used": self.gas_used,
        }


class SolanaTradingClient:
    """
    Solana trading client for executing swaps on DEXes
    """

    def __init__(self, private_key: str = None, rpc_endpoint: str = None):
        self.rpc_endpoint = rpc_endpoint or config.TRADING.rpc_endpoint
        self.client = SolanaClient(self.rpc_endpoint)

        # Load wallet
        if private_key:
            self.wallet = Keypair.from_bytes(base58.b58decode(private_key))
        else:
            self.wallet = None

        # DEX programs
        self.JUPITER_PROGRAM = Pubkey.from_string("JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4")
        # Jupiter is the only swap route used below. Keep the other program
        # identifiers optional so a stale third-party address cannot prevent
        # the bot from starting.
        self.RAYDIUM_PROGRAM = None
        self.ORCA_PROGRAM = Pubkey.from_string("whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc")

        # Token addresses
        self.WSOL_MINT = Pubkey.from_string("So11111111111111111111111111111111111111112")
        self.USDC_MINT = Pubkey.from_string("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")

        # Cache
        self._token_decimals: Dict[str, int] = {}
        self._price_cache: Dict[str, Tuple[float, float]] = {}

    def load_wallet_from_mnemonic(self, mnemonic: str):
        """Load wallet from mnemonic phrase"""
        # In production, use proper BIP39 library
        # For now, expect raw private key
        pass

    async def get_balance(self) -> float:
        """Get SOL balance"""
        if not self.wallet:
            return 0.0

        response = await asyncio.to_thread(self.client.get_balance, self.wallet.pubkey())
        lamports = response.value
        return lamports / 1e9  # Convert to SOL

    async def get_token_balance(self, mint: str) -> float:
        """Get token balance for a mint"""
        if not self.wallet:
            return 0.0

        try:
            ata = get_associated_token_address(self.wallet.pubkey(), Pubkey.from_string(mint))
            response = await asyncio.to_thread(self.client.get_token_account_balance, ata)
            ui_amount = response.value.ui_amount
            return ui_amount or 0.0
        except Exception as e:
            logger.warning(f"Failed to get token balance: {e}")
            return 0.0

    async def get_token_decimals(self, mint: str) -> int:
        """Get token decimals, cached"""
        if mint in self._token_decimals:
            return self._token_decimals[mint]

        try:
            response = await asyncio.to_thread(self.client.get_token_supply, Pubkey.from_string(mint))
            decimals = response.value.decimals
            self._token_decimals[mint] = decimals
            return decimals
        except Exception as e:
            logger.warning(f"Failed to get token decimals: {e}")
            return 9  # Default

    async def get_token_price(self, mint: str) -> float:
        """Get current token price in SOL, cached for 30 seconds."""
        import time
        cached = self._price_cache.get(mint)
        if cached and time.time() - cached[1] < 30:
            return cached[0]
        try:
            async with aiohttp.ClientSession() as session:
                # DexScreener exposes priceNative directly in SOL, avoiding a
                # second SOL/USD request and keeping the monitor lightweight.
                url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
                async with session.get(url, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        pairs = [
                            pair for pair in data.get("pairs", [])
                            if pair.get("chainId") == "solana"
                        ]
                        if pairs:
                            pair = max(
                                pairs,
                                key=lambda item: float(item.get("liquidity", {}).get("usd", 0) or 0),
                            )
                            price = float(pair.get("priceNative", 0) or 0)
                            self._price_cache[mint] = (price, time.time())
                            return price
        except Exception as e:
            logger.error(f"Failed to get price: {e}")

        return 0.0

    async def get_market_data(self, mint: str) -> Dict:
        """Get comprehensive market data for a token"""
        try:
            async with aiohttp.ClientSession() as session:
                # Use DexScreener
                url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
                async with session.get(url, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        pairs = data.get("pairs", [])

                        if pairs:
                            pair = pairs[0]  # Best pair by liquidity

                            return {
                                "price_usd": float(pair.get("priceUsd", 0)),
                                "price_sol": float(pair.get("priceNative", 0)),
                                "market_cap": float(pair.get("marketCap", 0)),
                                "liquidity": float(pair.get("liquidity", {}).get("usd", 0)),
                                "volume_24h": float(pair.get("volume", {}).get("h24", 0)),
                                "buys_24h": pair.get("txns", {}).get("buys", 0),
                                "sells_24h": pair.get("txns", {}).get("sells", 0),
                                "price_change_24h": float(pair.get("priceChange", {}).get("h24", 0)),
                                "created_at": pair.get("pairCreatedAt", 0)
                            }
        except Exception as e:
            logger.error(f"Failed to get market data: {e}")

        return {}

    async def execute_buy(
        self,
        mint: str,
        sol_amount: float,
        slippage_bps: int = 500
    ) -> TradeResult:
        """
        Execute a buy order via Jupiter aggregator
        """
        if not self.wallet:
            return TradeResult(success=False, error="No wallet loaded")

        try:
            # Get Jupiter quote
            async with aiohttp.ClientSession() as session:
                # Quote from SOL to token
                url = f"https://api.jup.ag/v6/quote"
                params = {
                    "inputMint": str(self.WSOL_MINT),
                    "outputMint": mint,
                    "amount": int(sol_amount * 1e9),  # Convert to lamports
                    "slippageBps": slippage_bps,
                    "computeUnitPriceMicroLamports": 1000000  # Priority fee
                }

                async with session.get(url, params=params, timeout=30) as resp:
                    if resp.status != 200:
                        error = await resp.text()
                        return TradeResult(success=False, error=f"Quote failed: {error}")

                    quote = await resp.json()

                # Get swap transaction
                url = "https://api.jup.ag/v6/swap"
                swap_request = {
                    "quoteResponse": quote,
                    "userPublicKey": str(self.wallet.pubkey()),
                    "wrapAndUnwrapSol": True
                }

                async with session.post(url, json=swap_request, timeout=30) as resp:
                    if resp.status != 200:
                        error = await resp.text()
                        return TradeResult(success=False, error=f"Swap request failed: {error}")

                    swap_data = await resp.json()

                # Decode and sign transaction
                tx_bytes = base64.b64decode(swap_data.get("swapTransaction"))
                tx = Transaction.from_bytes(tx_bytes)
                tx.sign(self.wallet, [self.wallet])

                # Send transaction
                signature = await asyncio.to_thread(
                    self.client.send_transaction, tx,
                    opts=TxOpts(skip_preflight=False, preflight_commitment=Confirmed),
                )

                # Wait for confirmation
                await self._wait_for_confirmation(signature.value)

                # Calculate results
                out_amount = int(quote.get("outAmount", 0))
                decimals = await self.get_token_decimals(mint)
                tokens_received = out_amount / (10 ** decimals)

                return TradeResult(
                    success=True,
                    signature=str(signature.value),
                    tokens_received=tokens_received,
                    price=sol_amount / tokens_received if tokens_received > 0 else 0,
                    gas_used=0.000005 * 5  # Estimated
                )

        except Exception as e:
            logger.error(f"Buy execution failed: {e}")
            return TradeResult(success=False, error=str(e))

    async def execute_sell(
        self,
        mint: str,
        token_amount: float,
        slippage_bps: int = 500
    ) -> TradeResult:
        """
        Execute a sell order via Jupiter aggregator
        """
        if not self.wallet:
            return TradeResult(success=False, error="No wallet loaded")

        try:
            # Get token account
            ata = get_associated_token_address(self.wallet.pubkey(), Pubkey.from_string(mint))

            # Check balance
            balance_resp = await asyncio.to_thread(self.client.get_token_account_balance, ata)
            available = balance_resp.value.ui_amount or 0

            if available < token_amount:
                token_amount = available

            if token_amount <= 0:
                return TradeResult(success=False, error="Insufficient balance")

            # Convert to base units
            decimals = await self.get_token_decimals(mint)
            amount_base = int(token_amount * (10 ** decimals))

            async with aiohttp.ClientSession() as session:
                # Quote from token to SOL
                url = f"https://api.jup.ag/v6/quote"
                params = {
                    "inputMint": mint,
                    "outputMint": str(self.WSOL_MINT),
                    "amount": amount_base,
                    "slippageBps": slippage_bps,
                    "computeUnitPriceMicroLamports": 1000000
                }

                async with session.get(url, params=params, timeout=30) as resp:
                    if resp.status != 200:
                        error = await resp.text()
                        return TradeResult(success=False, error=f"Quote failed: {error}")

                    quote = await resp.json()

                # Get swap transaction
                url = "https://api.jup.ag/v6/swap"
                swap_request = {
                    "quoteResponse": quote,
                    "userPublicKey": str(self.wallet.pubkey()),
                    "wrapAndUnwrapSol": True
                }

                async with session.post(url, json=swap_request, timeout=30) as resp:
                    if resp.status != 200:
                        error = await resp.text()
                        return TradeResult(success=False, error=f"Swap request failed: {error}")

                    swap_data = await resp.json()

                # Sign and send
                tx_bytes = base64.b64decode(swap_data.get("swapTransaction"))
                tx = Transaction.from_bytes(tx_bytes)
                tx.sign(self.wallet, [self.wallet])

                signature = await asyncio.to_thread(
                    self.client.send_transaction, tx,
                    opts=TxOpts(skip_preflight=False, preflight_commitment=Confirmed),
                )

                await self._wait_for_confirmation(signature.value)

                # Calculate results
                out_amount = int(quote.get("outAmount", 0))
                sol_received = out_amount / 1e9

                return TradeResult(
                    success=True,
                    signature=str(signature.value),
                    tokens_sold=token_amount,
                    sol_received=sol_received,
                    price=sol_received / token_amount if token_amount > 0 else 0
                )

        except Exception as e:
            logger.error(f"Sell execution failed: {e}")
            return TradeResult(success=False, error=str(e))

    async def _wait_for_confirmation(self, signature: str, timeout: int = 30):
        """Wait for transaction confirmation"""
        start = datetime.now()

        while (datetime.now() - start).seconds < timeout:
            try:
                response = await asyncio.to_thread(self.client.get_signature_statuses, [signature])
                status = response.value[0]

                if status:
                    if status.confirmation_status in [Confirmed, Finalized]:
                        if status.err is None:
                            return True
                        else:
                            raise Exception(f"Transaction failed: {status.err}")

            except Exception:
                pass

            await asyncio.sleep(1)

        raise Exception("Transaction confirmation timeout")

    async def get_recent_swaps(self, mint: str, limit: int = 20) -> List[Dict]:
        """Get recent swaps for a token"""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"https://api.jup.ag/v1/token/swaps/{mint}"
                params = {"limit": limit}

                async with session.get(url, params=params, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("swaps", [])
        except Exception as e:
            logger.error(f"Failed to get swaps: {e}")

        return []


class PaperTradingClient:
    """Drop-in trading client that never signs or broadcasts transactions."""

    def __init__(self, starting_balance_sol: float = 1.0, price_client=None, trade_store=None):
        self.starting_balance_sol = float(starting_balance_sol)
        self.balance_sol = self.starting_balance_sol
        self.holdings: Dict[str, float] = {}
        self.price_client = price_client
        if self.price_client is None:
            self.price_client = SolanaTradingClient(private_key=None)
        if trade_store is not None:
            self.restore_from_store(trade_store)

    def restore_from_store(self, trade_store) -> None:
        """Reconstruct simulated cash/token balances after a restart."""
        self.balance_sol = self.starting_balance_sol
        self.holdings.clear()
        for row in reversed(trade_store.get_trade_history(limit=1_000_000)):
            mint = row.get("position_mint", "")
            side = row.get("side")
            sol_amount = float(row.get("sol_amount", 0) or 0)
            token_amount = float(row.get("token_amount", 0) or 0)
            if side == "buy":
                self.balance_sol -= sol_amount
                self.holdings[mint] = self.holdings.get(mint, 0.0) + token_amount
            elif side == "sell":
                self.balance_sol += sol_amount
                self.holdings[mint] = max(0.0, self.holdings.get(mint, 0.0) - token_amount)
        self.balance_sol = max(0.0, self.balance_sol)

    async def get_balance(self) -> float:
        return self.balance_sol

    async def get_token_balance(self, mint: str) -> float:
        return self.holdings.get(mint, 0.0)

    async def get_token_price(self, mint: str) -> float:
        return await self.price_client.get_token_price(mint)

    async def execute_buy(self, mint: str, sol_amount: float, slippage_bps: int = 500) -> TradeResult:
        amount = float(sol_amount)
        if amount <= 0 or amount > self.balance_sol:
            return TradeResult(success=False, error="Insufficient simulated SOL balance")
        price = await self.get_token_price(mint)
        if price <= 0:
            return TradeResult(success=False, error="No simulated market price available")
        fee = amount * config.TRADING.paper_fee_bps / 10_000
        slippage_sol = amount * config.TRADING.paper_slippage_bps / 10_000
        execution_price = price * (1 + config.TRADING.paper_slippage_bps / 10_000)
        tokens = max(0.0, amount - fee) / execution_price
        self.balance_sol -= amount
        self.holdings[mint] = self.holdings.get(mint, 0.0) + tokens
        return TradeResult(
            success=True, tokens_received=tokens, price=execution_price, gas_used=fee,
            slippage_sol=slippage_sol,
            signature=f"paper-buy-{int(datetime.now().timestamp() * 1000000)}",
        )

    async def execute_sell(self, mint: str, token_amount: float, slippage_bps: int = 500) -> TradeResult:
        available = self.holdings.get(mint, 0.0)
        amount = min(float(token_amount), available)
        if amount <= 0:
            return TradeResult(success=False, error="Insufficient simulated token balance")
        price = await self.get_token_price(mint)
        if price <= 0:
            return TradeResult(success=False, error="No simulated market price available")
        execution_price = price * (1 - config.TRADING.paper_slippage_bps / 10_000)
        slippage_sol = amount * price * config.TRADING.paper_slippage_bps / 10_000
        gross_proceeds = amount * execution_price
        fee = gross_proceeds * config.TRADING.paper_fee_bps / 10_000
        proceeds = max(0.0, gross_proceeds - fee)
        self.holdings[mint] = max(0.0, available - amount)
        self.balance_sol += proceeds
        return TradeResult(
            success=True, tokens_sold=amount, sol_received=proceeds, price=execution_price,
            slippage_sol=slippage_sol, gas_used=fee,
            signature=f"paper-sell-{int(datetime.now().timestamp() * 1000000)}",
        )


class PumpFunTrader:
    """
    Direct trading on Pump.fun
    """

    def __init__(self, trading_client: SolanaTradingClient):
        self.client = trading_client
        self.api_base = "https://api.pump.fun"

    async def get_token_info(self, mint: str) -> Dict:
        """Get Pump.fun token info"""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.api_base}/tokens/{mint}"
                async with session.get(url, timeout=10) as resp:
                    if resp.status == 200:
                        return await resp.json()
        except Exception as e:
            logger.error(f"Failed to get token info: {e}")

        return {}

    async def buy(
        self,
        mint: str,
        sol_amount: float,
        slippage: int = 20
    ) -> TradeResult:
        """
        Buy on Pump.fun via their SDK/API
        """
        if not self.client.wallet:
            return TradeResult(success=False, error="No wallet")

        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.api_base}/trade-lightning"
                params = {"api-key": "demo"}  # Would need real API key

                payload = {
                    "action": "buy",
                    "mint": mint,
                    "amount": sol_amount,
                    "denominatedInSol": True,
                    "slippage": slippage,
                    "priorityFee": 0.001
                }

                async with session.post(url, params=params, json=payload, timeout=30) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return TradeResult(
                            success=True,
                            signature=data.get("signature"),
                            tokens_received=data.get("tokens_bought", 0)
                        )
                    else:
                        error = await resp.text()
                        return TradeResult(success=False, error=error)

        except Exception as e:
            return TradeResult(success=False, error=str(e))

    async def sell(
        self,
        mint: str,
        percentage: int = 100,
        slippage: int = 20
    ) -> TradeResult:
        """Sell on Pump.fun"""
        if not self.client.wallet:
            return TradeResult(success=False, error="No wallet")

        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.api_base}/trade-lightning"
                params = {"api-key": "demo"}

                payload = {
                    "action": "sell",
                    "mint": mint,
                    "percentage": percentage,
                    "slippage": slippage
                }

                async with session.post(url, params=params, json=payload, timeout=30) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return TradeResult(
                            success=True,
                            signature=data.get("signature"),
                            sol_received=data.get("sol_received", 0)
                        )
                    else:
                        error = await resp.text()
                        return TradeResult(success=False, error=error)

        except Exception as e:
            return TradeResult(success=False, error=str(e))

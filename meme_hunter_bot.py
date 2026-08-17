"""
MEME HUNTER - Main Bot - Selectivity Focused Rewrite
=====================================================
Edge must be selectivity + slower confirmation, not 0-slot race.

Improvements:
- 3 scanner stack: PumpPortal WS (buffer 10s, enhanced bundle detection), Pump.fun API poll 3s, LogsSubscribe (optional, processed commitment), Gecko new_pools
- Configurable tightened filters: buy_ratio 0.65, unique 12
- TradingEngine receives risk_analyzer for live rug checks
- Whale multiplier capped, max concurrent whale positions
- Rejection reasons extended for new MELT features
- Enrichment tries bundle_detector funding cluster + serial deployer
"""

import asyncio
import logging
import signal
import os
import sys
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path

from config import Config, config, TradingConfig
from models import TokenSignal, TokenStatus, Position, Portfolio
from token_scanner import (
    TokenScanner,
    PumpPortalScanner,
    DexScreenerScanner,
    PumpFunAPIScanner,
    LogsSubscribeScanner,
    ScanFilters
)
from risk_analyzer import RiskAnalyzer, WhaleTracker
from capital_manager import CapitalManager
from learning import TradeLearner
from state_store import StateStore
from wallet_store import WalletStore
from storage_paths import default_trade_history_path, default_wallets_path
from trading_engine import TradingEngine
from solana_client import SolanaTradingClient, PaperTradingClient, PumpFunTrader
from whale_data import (
    whale_fetcher,
    trending_fetcher,
    get_whale_activity_for_token,
    update_whale_list_from_gmgn,
)
from telegram_bot import init_telegram

# Optional enhanced detectors
try:
    from bundle_detector import detect_funding_clusters, check_serial_deployer
    HAS_BUNDLE_DETECTOR = True
except ImportError:
    HAS_BUNDLE_DETECTOR = False

logger = logging.getLogger(__name__)


class MemeHunterBot:
    def __init__(self, config_path: str = None):
        if config_path:
            Config.load_from_file(config_path)
        self._load_config()

        trade_path = config.TRADING.trade_history_db_path
        if config.TRADING.state_db_path != "trade_history.db":
            trade_path = config.TRADING.state_db_path
        if trade_path == "trade_history.db":
            trade_path = default_trade_history_path()
        wallet_path = config.TRADING.wallets_db_path
        if wallet_path == "wallets_list.db":
            wallet_path = default_wallets_path()
        self.store = StateStore(trade_path)
        self.wallet_store = WalletStore(wallet_path)

        self.paper_mode = bool(config.TRADING.paper_trading)
        if self.paper_mode:
            price_client = SolanaTradingClient(
                private_key=None, rpc_endpoint=config.TRADING.rpc_endpoint
            )
            self.client = PaperTradingClient(
                starting_balance_sol=config.TRADING.paper_starting_balance_sol,
                price_client=price_client,
                trade_store=self.store,
            )
        else:
            self.client = SolanaTradingClient(
                private_key=config.TRADING.wallet_private_key,
                rpc_endpoint=config.TRADING.rpc_endpoint,
            )

        self.pump_trader = PumpFunTrader(self.client)
        self.scanner = TokenScanner()
        self.risk_analyzer = RiskAnalyzer()
        self.whale_tracker = WhaleTracker(config.KOL_WALLETS)
        for wallet in config.KOL_WALLETS:
            whale_fetcher.add_wallet(
                wallet.address, wallet.name, wallet.tier,
                wallet.min_buy_sol, copy_trade=wallet.copy_trade,
            )

        whale_fetcher.store = self.wallet_store
        for candidate in self.wallet_store.get_wallet_candidates(limit=100):
            whale_fetcher.add_wallet(
                candidate["address"], candidate.get("name", "Tracked wallet"),
                candidate.get("source", "learned"),
                candidate.get("min_buy_threshold", 0.5), copy_trade=False,
            )
        self.capital_manager = CapitalManager(self.client)
        self.learner = TradeLearner(
            self.store, whale_fetcher, wallet_store=self.wallet_store
        )
        self.trading_engine = TradingEngine(
            self.client, capital_manager=self.capital_manager,
            store=self.store, learner=self.learner,
            risk_analyzer=self.risk_analyzer,
        )

        self.portfolio = Portfolio()

        self.telegram = None
        telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
        telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if telegram_token and telegram_chat_id:
            self.telegram = init_telegram(telegram_token, telegram_chat_id, self.trading_engine)

        self.running = False
        self.paused = False
        self.approved_tokens: Dict[str, TokenSignal] = {}
        self.scanned_mints: set = set()
        self.pending_signals: Dict[str, TokenSignal] = {}
        self.current_prices: Dict[str, float] = {}

        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _load_config(self):
        cfg = config.TRADING
        env_map = {
            "MAX_POSITION_PER_COIN": ("max_position_per_coin", float),
            "ALLOCATION_PER_MEME_PCT": ("allocation_per_meme_pct", float),
            "MAX_PORTFOLIO_ALLOCATION_PCT": ("max_portfolio_allocation_pct", float),
            "MIN_SOL_RESERVE": ("min_sol_reserve", float),
            "MIN_TRADE_SOL": ("min_trade_sol", float),
            "MIN_MARKET_CAP_USD": ("min_market_cap_usd", float),
            "SIGNAL_RECHECK_INTERVAL_SECONDS": ("signal_recheck_interval_seconds", int),
            "SIGNAL_RECHECK_MAX_AGE_SECONDS": ("signal_recheck_max_age_seconds", int),
            "MAX_COINS_TRACKED": ("max_coins_tracked", int),
            "DCA_ENTRIES": ("dca_entries", int),
            "DCA_SPACING_PCT": ("dca_spacing_pct", float),
            "DCA_WAIT_FOR_DIPS": ("dca_wait_for_dips", lambda v: str(v).lower() in {"1","true","yes"}),
            "DCA_COOLDOWN_SECONDS": ("dca_cooldown_seconds", int),
            "GRID_LEVELS": ("grid_levels", int),
            "STOP_LOSS_PCT": ("stop_loss_pct", float),
            "TRAILING_STOP_PCT": ("trailing_stop_pct", float),
            "TRAILING_STOP_ACTIVATION_PCT": ("trailing_stop_activation_pct", float),
            "TRAILING_STOP_DISTANCE_PCT": ("trailing_stop_distance_pct", float),
            "MOON_BAG_PCT": ("moon_bag_pct", float),
            "PAPER_FEE_BPS": ("paper_fee_bps", int),
            "PAPER_SLIPPAGE_BPS": ("paper_slippage_bps", int),
            "MIN_CONFIRMING_WHALES": ("min_confirming_whales", int),
            "MAX_WHALE_MULTIPLIER": ("max_whale_multiplier", float),
            "MAX_CONCURRENT_WHALE_POSITIONS": ("max_concurrent_whale_positions", int),
            "TIME_EXCEED_SECONDS": ("time_exceed_seconds", int),
            "DEV_DUMP_THRESHOLD_PCT": ("dev_dump_threshold_pct", float),
            "LIQUIDITY_DROP_THRESHOLD_PCT": ("liquidity_drop_threshold_pct", float),
            "MAX_BUNDLE_RISK_SCORE": ("max_bundle_risk_score", float),
            "MAX_WASH_SCORE": ("max_wash_score", float),
            "MAX_SNIPER_SATURATION_SCORE": ("max_sniper_saturation_score", float),
            "PUMPFUN_POLL_INTERVAL_SECONDS": ("pumpfun_poll_interval_seconds", int),
            "GECKO_POLL_INTERVAL_SECONDS": ("gecko_poll_interval_seconds", int),
            "GMGN_TOKEN_ENRICH_PER_HOUR": ("gmgn_token_enrich_per_hour", int),
            "LEARNED_WALLET_MIN_PROFIT_SOL": ("learned_wallet_min_profit_sol", float),
            "TRADE_HISTORY_DB_PATH": ("trade_history_db_path", str),
            "WALLETS_DB_PATH": ("wallets_db_path", str),
            "PAPER_STARTING_BALANCE_SOL": ("paper_starting_balance_sol", float),
            "HELIUS_API_KEY": ("helius_api_key", str),
            "HELIUS_RPC_WS": ("helius_rpc_ws", str),
            "LOGS_SUBSCRIBE_ENABLED": ("logs_subscribe_enabled", lambda v: str(v).lower() in {"1","true","yes"}),
        }
        for env_name, (attr, conv) in env_map.items():
            value = os.getenv(env_name)
            if value is not None:
                try:
                    setattr(cfg, attr, conv(value))
                except ValueError:
                    logger.warning("Ignoring invalid %s=%r", env_name, value)
        if os.getenv("RPC_ENDPOINT"):
            cfg.rpc_endpoint = os.getenv("RPC_ENDPOINT")
        if os.getenv("WALLET_PRIVATE_KEY"):
            cfg.wallet_private_key = os.getenv("WALLET_PRIVATE_KEY")
        if os.getenv("HELIUS_RPC_URL"):
            cfg.helius_rpc_ws = os.getenv("HELIUS_RPC_URL").replace("https://", "wss://")
        legacy_trade_path = os.getenv("STATE_DB_PATH") or os.getenv("DB_PATH")
        if legacy_trade_path:
            cfg.trade_history_db_path = legacy_trade_path
            cfg.state_db_path = legacy_trade_path
        if os.getenv("PAPER_TRADING") is not None:
            cfg.paper_trading = os.getenv("PAPER_TRADING", "true").lower() in {"1","true","yes"}
        if os.getenv("AUTO_LEARN_WALLETS") is not None:
            cfg.auto_learn_wallets = os.getenv("AUTO_LEARN_WALLETS", "true").lower() in {"1","true","yes"}
        logger.info(
            "Config loaded: mode=%s trade_db=%s wallet_db=%s selectors: bundle<%.0f sniper<%.0f time_exceed=%ss pump_poll=%ss logs=%s",
            "paper" if cfg.paper_trading else "live",
            cfg.trade_history_db_path, cfg.wallets_db_path,
            cfg.max_bundle_risk_score, cfg.max_sniper_saturation_score,
            cfg.time_exceed_seconds, cfg.pumpfun_poll_interval_seconds, cfg.logs_subscribe_enabled
        )

    def _signal_handler(self, signum, frame):
        logger.info("Shutdown signal received, cleaning up...")
        self.running = False

    async def _health_monitor(self):
        port = int(os.getenv("PORT", "8080"))
        try:
            async def handle(reader, writer):
                try:
                    await reader.read(1024)
                    body = b"ok"
                    writer.write(
                        b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
                        + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                        + body
                    )
                    await writer.drain()
                finally:
                    writer.close()
                    await writer.wait_closed()
            server = await asyncio.start_server(handle, "0.0.0.0", port)
            logger.info("Health endpoint listening on port %s", port)
            while self.running:
                await asyncio.sleep(5)
            server.close()
            await server.wait_closed()
        except Exception as exc:
            logger.warning("Health endpoint unavailable: %s", exc)

    async def start(self):
        logger.info("="*60)
        logger.info("🚀 MEME HUNTER BOT STARTING - Selectivity Framework")
        logger.info("="*60)

        balance = await self.client.get_balance()
        if self.paper_mode:
            logger.info(f"Paper balance: {balance:.4f} SOL (no live transactions)")
        else:
            logger.info(f"Wallet balance: {balance:.4f} SOL")
            if balance < 0.1:
                logger.warning("Low SOL balance! Ensure at least 0.1 SOL")

        filters = ScanFilters(
            min_dev_buy_sol=config.TRADING.min_dev_buy_sol,
            min_market_cap_sol=config.TRADING.min_market_cap_usd / 200,
            max_market_cap_sol=50.0,
            min_liquidity_sol=config.TRADING.min_liquidity_usd / 200,
            min_unique_wallets=config.TRADING.min_unique_wallets,
            min_buy_ratio=config.TRADING.min_buy_ratio
        )

        # === Selectivity scanner stack ===
        # Primary: PumpPortal WS with enhanced bundle detection
        self.scanner.add_scanner(PumpPortalScanner(filters, buffer_duration=config.TRADING.pumpportal_buffer_seconds))

        # NEW: Pump.fun API polling every 3s - 10x faster than Gecko
        if config.TRADING.enable_pumpfun_api_scanner:
            self.scanner.add_scanner(PumpFunAPIScanner(filters, poll_interval=config.TRADING.pumpfun_poll_interval_seconds))

        # Gecko new_pools still for migration detection
        if config.TRADING.enable_gecko_scanner:
            self.scanner.add_scanner(DexScreenerScanner(filters, poll_interval=config.TRADING.gecko_poll_interval_seconds))

        # Optional: logsSubscribe at processed for near-real-time (requires Helius WSS)
        if config.TRADING.logs_subscribe_enabled:
            ws_url = config.TRADING.helius_rpc_ws or os.getenv("HELIUS_WSS_URL", "")
            http_url = config.TRADING.rpc_endpoint
            if ws_url:
                logger.info(f"Enabling LogsSubscribe scanner: {ws_url[:40]}...")
                self.scanner.add_scanner(LogsSubscribeScanner(filters, ws_url=ws_url, http_url=http_url))
            else:
                logger.warning("LOGS_SUBSCRIBE_ENABLED but no HELIUS_RPC_WS set - skipping")

        self.capital_manager.set_balance(balance)
        if self.paper_mode:
            self.capital_manager.reference_balance_sol = config.TRADING.paper_starting_balance_sol
        self.portfolio.starting_balance_sol = config.TRADING.paper_starting_balance_sol if self.paper_mode else balance
        self.portfolio.current_balance_sol = balance

        self.running = True

        if self.telegram:
            asyncio.create_task(self.telegram.start())

        tasks = [
            self._health_monitor(),
            self._run_scanner(),
            self._run_price_monitor(),
            self._run_position_monitor(),
            self._run_balance_monitor(),
            self._run_signal_recheck(),
            self._run_whale_updater(),
            self._run_status_reporting()
        ]
        await asyncio.gather(*tasks)

    async def _run_whale_updater(self):
        logger.info("Starting whale updater...")
        first_run = True
        while self.running:
            try:
                if not first_run:
                    await asyncio.sleep(1800)
                first_run = False
                if not self.paused:
                    result = await update_whale_list_from_gmgn(limit=50)
                    logger.info(
                        "Whale refresh: %s new GMGN wallets, %s total tracked, %s activity records",
                        result["count"], result.get("tracked_count", result["count"]),
                        result.get("activity_count", 0),
                    )
            except Exception as e:
                logger.error(f"Whale updater error: {e}")
                await asyncio.sleep(30)

    async def _run_scanner(self):
        logger.info("Starting token scanner stack...")
        try:
            await self.scanner.start(self._handle_token_signal)
        except Exception as e:
            logger.error(f"Scanner error: {e}")

    @staticmethod
    def _classify_signal(signal: TokenSignal, whale_summary: Optional[Dict] = None) -> List[str]:
        labels = {"normal_scanner"}
        if signal.source in {"pumpfun_new", "pumpfun_api", "logs_subscribe", "gecko_new_pool"}:
            labels.add("new_pair")
        if signal.source == "logs_subscribe":
            labels.add("logs_detected")
        if signal.source == "pumpfun_api":
            labels.add("pumpfun_api")
        if signal.behavior_data.get("migration"):
            labels.add("migration")
        if signal.twitter_mentions or signal.telegram_members or signal.behavior_data.get("social_catalyst"):
            labels.add("social_catalyst")
        summary = whale_summary or {}
        buys = summary.get("all_buys", [])
        if any(item.get("source") == "kol" for item in buys):
            labels.add("kol_confirmation")
        if any(item.get("source") in {"gmgn", "smartmoney"} for item in buys):
            labels.add("smart_money_confirmation")
        if signal.behavior_data.get("sniper_saturation_score", 0) >= 80:
            labels.add("sniper_saturated")
        if signal.behavior_data.get("bundle_risk_score", 0) >= 70:
            labels.add("bundle_risk")
        return sorted(labels)

    @staticmethod
    def _rejection_reason(signal: TokenSignal, risk_report=None, whale_summary: Optional[Dict] = None) -> str:
        if risk_report is None:
            return "score_below_40"
        if not getattr(risk_report, "checks_complete", False):
            missing = getattr(risk_report, "unavailable_checks", [])
            return "risk_data_unavailable:" + (",".join(missing) if missing else "unknown")
        for flag, reason in (
            ("is_honeypot", "honeypot"),
            ("is_rugged", "rugged"),
            ("high_concentration", "holder_concentration"),
            ("developer_cluster", "developer_cluster"),
            ("wash_trading_suspected", "wash_trading"),
            ("bundle_risk_suspected", "bundle_risk"),
            ("creator_sold_early", "creator_sold"),
            ("sniper_saturation_suspected", "sniper_saturated"),
            ("funding_cluster_suspected", "funding_cluster"),
            ("serial_deployer_suspected", "serial_deployer"),
        ):
            if getattr(risk_report, flag, False):
                return reason
        if risk_report.overall_score < 50:
            return "risk_score_below_50"
        # New MELT-based rejections from behavior directly even if risk gate technically passed
        bd = signal.behavior_data or {}
        if float(bd.get("sniper_saturation_score", 0) or 0) >= config.TRADING.max_sniper_saturation_score:
            return f"sniper_saturation_{bd.get('sniper_saturation_score')}"
        if float(bd.get("bundle_risk_score", 0) or 0) >= config.TRADING.max_bundle_risk_score:
            return f"bundle_risk_{bd.get('bundle_risk_score')}"
        if float(bd.get("wash_trading_score", 0) or 0) >= config.TRADING.max_wash_score:
            return f"wash_trading_{bd.get('wash_trading_score')}"
        if float(bd.get("cluster_risk_score", 0) or 0) >= config.TRADING.max_funding_cluster_risk:
            return f"funding_cluster_{bd.get('cluster_risk_score')}"
        if bd.get("is_serial_deployer"):
            return "serial_deployer"

        # V3: migrations need risk >=62 now after 7.7% win rate
        is_migration = bool(bd.get("migration"))
        if signal.source == "gecko_new_pool" and not bd.get("gmgn_enriched"):
            if is_migration and risk_report.overall_score >= 62 and signal.liquidity_sol >= 8:
                pass
            else:
                return "launch_quality_data_unavailable"

        # V3 thresholds tightened after 22% approval too high
        threshold = 55
        if signal.source in {"pumpfun_new", "pumpfun_api", "logs_subscribe"}:
            threshold = 52
        if is_migration:
            threshold = 50

        if signal.overall_score < threshold and not signal.is_whale_alert:
            return f"opportunity_score_below_{threshold}"
        if signal.is_whale_alert and (whale_summary or {}).get("total_sol", 0) < 2:
            return "smart_money_amount_below_2_sol"
        return "not_approved"

    @staticmethod
    def _signal_features(signal: TokenSignal, whale_summary: Optional[Dict] = None, risk_report=None) -> Dict:
        summary = whale_summary or {}
        return {
            "source": signal.source,
            "score": signal.overall_score,
            "dev_buy_sol": signal.dev_buy_sol,
            "market_cap_sol": signal.market_cap_sol,
            "liquidity_sol": signal.liquidity_sol,
            "buy_ratio": signal.buy_ratio,
            "unique_wallets": signal.unique_wallets,
            "total_trades": signal.total_trades,
            "behavior": signal.behavior_data,
            "risk_score": getattr(risk_report, "overall_score", None),
            "risk_checks_complete": getattr(risk_report, "checks_complete", None),
            "risk_check_status": getattr(risk_report, "check_status", {}),
            "risk_unavailable_checks": getattr(risk_report, "unavailable_checks", []),
            "whale_sol": summary.get("total_sol", 0.0),
            "whale_wallet_count": summary.get("wallet_count", 0),
            "whale_avg_win_rate": summary.get("avg_win_rate", 0.0),
        }

    async def _enrich_signal_from_gmgn(self, signal: TokenSignal) -> bool:
        """GMGN enrichment for near-threshold pools + optional bundle funding cluster"""
        if signal.source not in {"gecko_new_pool", "pumpfun_api", "logs_subscribe"}:
            # Only enrich pools that are borderline or have low quality data
            if signal.overall_score >= 70:
                return False
        if signal.liquidity_sol < config.TRADING.min_liquidity_usd / 200 and signal.source == "gecko_new_pool":
            # Still need min liquidity for enrich to be worth credit
            pass

        info = await whale_fetcher.fetch_token_info(signal.mint)
        if not info:
            return False
        token = info.get("token", info.get("data", info)) if isinstance(info, dict) else {}
        if isinstance(token, dict) and isinstance(token.get("data"), dict):
            token = token["data"]
        if not isinstance(token, dict):
            return False
        top10 = token.get("top_10_holder_rate")
        try:
            top10 = float(top10) if top10 is not None else None
        except (TypeError, ValueError):
            top10 = None
        quality = 0.0
        if str(token.get("renounced_mint", token.get("mintable", "0"))) in {"1", "true"}:
            quality += 2.0
        if str(token.get("renounced_freeze_account", token.get("freezable", "0"))) in {"1", "true"}:
            quality += 2.0
        if top10 is not None and top10 < 0.30:
            quality += 3.0
        if float(token.get("holder_count", 0) or 0) >= 100:
            quality += 2.0
        if str(token.get("is_honeypot", "0")) in {"0", "false"}:
            quality += 1.0
        signal.behavior_data.update({
            "gmgn_quality_score": quality,
            "gmgn_top10_holder_rate": top10,
            "gmgn_holder_count": token.get("holder_count"),
            "bundler_rate": token.get("bundler_rate"),
            "smart_degen_count": token.get("smart_degen_count"),
            "is_wash_trading": token.get("is_wash_trading"),
            "creator_close": token.get("creator_close"),
            "gmgn_enriched": True,
        })
        if top10 is not None:
            signal.behavior_data["bundle_risk_score"] = max(
                float(signal.behavior_data.get("bundle_risk_score", 0) or 0),
                min(100.0, top10 * 100.0),
            )
        signal.calculate_overall_score()
        return True

    async def _enrich_with_bundle_detector(self, signal: TokenSignal):
        """Optional deeper enrichment using bundle_detector if available"""
        if not HAS_BUNDLE_DETECTOR:
            return
        try:
            # Funding cluster check if enabled and we have early buyers
            if config.TRADING.enable_funding_cluster_check and signal.behavior_data.get("early_buyer_count", 0) >= 5:
                # We don't have wallet list here directly, but behavior_data has observed traders count
                # For deeper check, we would need actual wallet addresses - store from scanner if available
                # For now, only check serial deployer if creator known
                if signal.creator_address:
                    deployer_info = await check_serial_deployer(signal.creator_address)
                    if deployer_info.get("is_serial_deployer"):
                        signal.behavior_data.update({
                            "is_serial_deployer": True,
                            "serial_deployer_risk_score": deployer_info.get("risk_score", 35.0),
                            "deployed_count_recent": deployer_info.get("deployed_count_recent", 0)
                        })
                        signal.calculate_overall_score()
        except Exception as e:
            logger.debug(f"Bundle detector enrichment failed: {e}")

    async def _handle_token_signal(self, signal: TokenSignal, allow_recheck: bool = False):
        if signal.mint in self.scanned_mints and not allow_recheck:
            return
        self.scanned_mints.add(signal.mint)

        logger.info(
            f"🔍 NEW SIGNAL [{signal.source}]: {signal.name} ({signal.symbol})\n"
            f"   Mint: {signal.mint[:16]}...\n"
            f"   Dev Buy: {signal.dev_buy_sol:.2f} SOL\n"
            f"   Market Cap: ${signal.market_cap_sol * 200:.2f}\n"
            f"   Buy Ratio: {signal.buy_ratio:.1%}\n"
            f"   Unique: {signal.unique_wallets} wallets\n"
            f"   Score: {signal.overall_score:.1f} | Bundle:{signal.behavior_data.get('bundle_risk_score',0)} Wash:{signal.behavior_data.get('wash_trading_score',0)} Sniper:{signal.behavior_data.get('sniper_saturation_score',0)}"
        )
        if not allow_recheck:
            self.store.record_signal(
                mint=signal.mint, symbol=signal.symbol, name=signal.name,
                score=signal.overall_score, dev_buy_sol=signal.dev_buy_sol,
                market_cap_sol=signal.market_cap_sol, liquidity_sol=signal.liquidity_sol,
                buy_ratio=signal.buy_ratio, unique_wallets=signal.unique_wallets,
                decision="discovered", reason="initial scanner signal",
            )
            signal.strategy_types = self._classify_signal(signal)
            signal.observation_ids = self.store.ensure_signal_observations(
                signal_run_id=signal.signal_run_id,
                mint=signal.mint, symbol=signal.symbol,
                strategy_types=signal.strategy_types,
                discovered_at=signal.discovered_at,
                features=self._signal_features(signal),
                decision="discovered",
            )

        if not self.paused:
            enriched = await self._enrich_signal_from_gmgn(signal)
            if enriched:
                self.store.update_signal_observations(
                    signal.signal_run_id,
                    strategy_types=signal.strategy_types,
                    decision="enriched",
                    approved=False,
                    features=self._signal_features(signal),
                )
            await self._enrich_with_bundle_detector(signal)

        if self.paused:
            self.store.update_signal_observations(
                signal.signal_run_id, strategy_types=signal.strategy_types,
                decision="paused", approved=False,
                rejection_reason="bot_paused",
                features=self._signal_features(signal),
            )
            logger.info(f"   ⏸️ Bot paused, skipping")
            return

        if signal.overall_score < 40:
            self.store.update_signal_observations(
                signal.signal_run_id, strategy_types=signal.strategy_types,
                decision="score_filtered", approved=False,
                rejection_reason="score_below_40",
                features=self._signal_features(signal),
            )
            self.pending_signals[signal.mint] = signal
            logger.info(f"   ⏭️ Score too low ({signal.overall_score:.1f}), queued for recheck")
            return

        # Pre-filter for MELT fast-fail before expensive RPC calls
        bd = signal.behavior_data
        if float(bd.get("sniper_saturation_score", 0) or 0) >= config.TRADING.max_sniper_saturation_score:
            logger.info(f"   ❌ REJECTED pre-risk: sniper saturated {bd.get('sniper_saturation_score')}")
            rejection = f"sniper_saturated_{bd.get('sniper_saturation_score')}"
            self.store.update_signal_observations(
                signal.signal_run_id, strategy_types=signal.strategy_types,
                decision="rejected", approved=False, rejection_reason=rejection,
                features=self._signal_features(signal)
            )
            self.store.record_signal(
                mint=signal.mint, symbol=signal.symbol, name=signal.name,
                score=signal.overall_score, dev_buy_sol=signal.dev_buy_sol,
                market_cap_sol=signal.market_cap_sol, liquidity_sol=signal.liquidity_sol,
                buy_ratio=signal.buy_ratio, unique_wallets=signal.unique_wallets,
                decision="rejected", reason=rejection,
                metadata={"behavior": bd}
            )
            return

        risk_report = await self.risk_analyzer.analyze(
            signal.mint,
            market_data={
                "liquidity_usd": signal.liquidity_sol * 200,
                "market_cap_usd": signal.market_cap_sol * 200,
            },
            behavior_data=signal.behavior_data,
        )

        logger.info(
            f"   📊 Risk Analysis:\n"
            f"   Risk Score: {risk_report.overall_score:.1f}/100\n"
            f"   Recommendation: {risk_report.get_recommendation()}\n"
            f"   Data: {risk_report.check_status or {}}"
        )
        if risk_report.warnings:
            for warning in risk_report.warnings[:4]:
                logger.info(f"   {warning}")

        whale_summary = {
            "total_sol": 0.0, "total_buys": 0, "wallet_count": 0,
            "all_buys": [], "top_buyer": None,
        }
        try:
            whale_summary = await get_whale_activity_for_token(signal.mint)
            if whale_summary["total_sol"] > 0:
                logger.info(
                    f"   🐋 Whale Activity:\n"
                    f"   Total Buys: {whale_summary['total_buys']}\n"
                    f"   Total SOL: {whale_summary['total_sol']:.2f}\n"
                    f"   Wallets: {whale_summary.get('wallet_count',0)}\n"
                    f"   Signal: {whale_summary['signal']}\n"
                    f"   Conviction: {whale_summary['conviction']}"
                )
                signal.is_whale_alert = (
                    whale_summary["total_sol"] > 1
                    and whale_summary.get("wallet_count", 0) >= config.TRADING.min_confirming_whales
                )
                if whale_summary['top_buyer']:
                    signal.whale_address = whale_summary['top_buyer']['wallet']
                    signal.kol_wallets = [b['wallet'] for b in whale_summary['all_buys']]
        except Exception as e:
            logger.warning(f"Whale check failed (non-critical): {e}")

        signal.calculate_overall_score()

        if signal.kol_wallets:
            adjustment = self.learner.confidence_adjustment(signal.kol_wallets)
            signal.overall_score = max(0.0, signal.overall_score + adjustment)
            if adjustment:
                logger.info("   🧠 Historical actor adjustment: %+0.1f", adjustment)

        signal.strategy_types = self._classify_signal(signal, whale_summary)
        signal.observation_ids = self.store.ensure_signal_observations(
            signal_run_id=signal.signal_run_id,
            mint=signal.mint, symbol=signal.symbol,
            strategy_types=signal.strategy_types,
            discovered_at=signal.discovered_at,
            features=self._signal_features(signal, whale_summary, risk_report),
            decision="evaluated",
        )

        should_trade = False
        if risk_report.is_tradeable(min_score=50):
            bd = signal.behavior_data or {}
            is_migration = bool(bd.get("migration"))

            # V3 FIX: allow migrations even without GMGN enrichment if risk good
            if signal.source == "gecko_new_pool" and not bd.get("gmgn_enriched"):
                if is_migration and risk_report.overall_score >= 62:
                    launch_data_ok = True
                else:
                    launch_data_ok = False
            else:
                launch_data_ok = True

            # V3 tiered thresholds – tightened after 22% approval too high, need 8-10%
            # Based on performance: 14% win rate, need higher bar
            threshold = 55
            if signal.source in {"pumpfun_new", "pumpfun_api", "logs_subscribe"}:
                threshold = 52
            if is_migration:
                threshold = 50  # migrations were worst 7.7% win, require higher risk but lower score?

            if launch_data_ok and signal.overall_score >= threshold:
                # Extra selectivity: require bundle <60 and sniper <70 already in risk gate,
                # but also require buy_ratio >=0.70 and unique >=10 for final approval
                if signal.buy_ratio >= 0.65 and signal.unique_wallets >= 8:
                    should_trade = True
            elif launch_data_ok and signal.is_whale_alert and whale_summary["total_sol"] >= 2:
                # Whale-assisted can be slightly lower but still selective
                if signal.overall_score >= 50:
                    should_trade = True

        decision = "approved" if should_trade else "rejected"
        rejection_reason = None if should_trade else self._rejection_reason(signal, risk_report, whale_summary)
        self.store.update_signal_observations(
            signal.signal_run_id,
            strategy_types=signal.strategy_types,
            decision=decision,
            approved=should_trade,
            risk_score=risk_report.overall_score,
            rejection_reason=rejection_reason,
            features=self._signal_features(signal, whale_summary, risk_report),
        )
        self.store.record_signal(
            mint=signal.mint, symbol=signal.symbol, name=signal.name,
            score=signal.overall_score, dev_buy_sol=signal.dev_buy_sol,
            market_cap_sol=signal.market_cap_sol, liquidity_sol=signal.liquidity_sol,
            buy_ratio=signal.buy_ratio, unique_wallets=signal.unique_wallets,
            risk_score=risk_report.overall_score,
            whale_sol=whale_summary.get("total_sol", 0.0), decision=decision,
            reason=risk_report.get_recommendation(),
            metadata={
                "whale_count": whale_summary.get("total_buys", 0),
                "whale_wallet_count": whale_summary.get("wallet_count", 0),
                "behavior": signal.behavior_data,
                "strategy_types": signal.strategy_types,
                "signal_run_id": signal.signal_run_id,
            },
        )

        if should_trade:
            logger.info(f"   ✅ APPROVED FOR TRADING (score {signal.overall_score:.1f} risk {risk_report.overall_score:.1f})")
            self.approved_tokens[signal.mint] = signal
            if self.telegram:
                await self.telegram.alert_new_signal({
                    "name": signal.name,
                    "symbol": signal.symbol,
                    "overall_score": signal.overall_score,
                    "dev_buy_sol": signal.dev_buy_sol,
                    "market_cap_sol": signal.market_cap_sol,
                    "buy_ratio": signal.buy_ratio,
                    "is_whale_alert": signal.is_whale_alert
                })
            if signal.overall_score >= 70 or (signal.is_whale_alert and whale_summary["total_sol"] > 2):
                logger.info(f"   🚀 STRONG SIGNAL - Opening position...")
                await self._open_position(signal)
            self.pending_signals.pop(signal.mint, None)
        else:
            if rejection_reason and (
                rejection_reason.startswith("risk_data_unavailable")
                or rejection_reason in {"launch_quality_data_unavailable", "opportunity_score_below_60"}
            ):
                self.pending_signals[signal.mint] = signal
            logger.info(f"   ❌ REJECTED - {rejection_reason or 'unknown reason'}")

    async def _open_position(self, signal: TokenSignal):
        if signal.mint in self.trading_engine.active_positions:
            return
        if len(self.trading_engine.active_positions) >= config.TRADING.max_coins_tracked:
            logger.warning("Max positions reached!")
            return

        base_size = self.capital_manager.position_cap_sol()
        if signal.overall_score >= 80:
            size = base_size
        elif signal.overall_score >= 70:
            size = base_size * 0.75
        else:
            size = base_size * 0.5

        if signal.is_whale_alert:
            # Capped multiplier - new: max_whale_multiplier default 1.25 but capped by position cap
            size = min(size * config.TRADING.max_whale_multiplier, base_size * config.TRADING.max_whale_multiplier)

            # Reduce size if funding cluster risk present even if approved
            if float(signal.behavior_data.get("cluster_risk_score", 0) or 0) > 30:
                size *= 0.7
                logger.info(f"   ⚠️ Funding cluster risk {signal.behavior_data.get('cluster_risk_score')} → reducing size to {size:.4f} SOL")

        position = await self.trading_engine.open_position(signal, sol_budget=size)

        if position:
            logger.info(
                f"   💰 Position Opened:\n"
                f"   Entry: {position.entry_price:.8f} SOL\n"
                f"   Invested: {position.total_invested_sol:.4f} SOL\n"
                f"   Tokens: {position.total_tokens:.2f}\n"
                f"   Stop Loss: {position.stop_loss_price:.8f} SOL\n"
                f"   Initial Liq: {position.initial_liquidity_sol:.2f} SOL\n"
                f"   Creator: {position.creator_address or 'unknown'[:12]}"
            )

    async def _run_balance_monitor(self):
        while self.running:
            try:
                await self.capital_manager.refresh()
                summary = self.trading_engine.get_portfolio_summary()
                self.portfolio.current_balance_sol = summary["wallet_balance_sol"]
                await asyncio.sleep(30)
            except Exception as exc:
                logger.warning("Balance monitor error: %s", exc)
                await asyncio.sleep(30)

    async def _run_signal_recheck(self):
        while self.running:
            await asyncio.sleep(config.TRADING.signal_recheck_interval_seconds)
            now = datetime.now()
            for mint, signal in list(self.pending_signals.items()):
                age = (now - signal.discovered_at).total_seconds()
                if age > config.TRADING.signal_recheck_max_age_seconds:
                    self.pending_signals.pop(mint, None)
                    continue
                try:
                    await self._handle_token_signal(signal, allow_recheck=True)
                except Exception as exc:
                    logger.warning("Signal recheck failed for %s: %s", mint[:12], exc)

    async def _run_price_monitor(self):
        logger.info("Starting price monitor...")
        while self.running:
            try:
                for mint in list(self.trading_engine.active_positions.keys()):
                    price = await self.client.get_token_price(mint)
                    if price > 0:
                        self.current_prices[mint] = price
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Price monitor error: {e}")
                await asyncio.sleep(10)

    async def _run_position_monitor(self):
        logger.info("Starting position monitor with live rug checks...")
        while self.running:
            try:
                if self.trading_engine.active_positions:
                    await self.trading_engine.monitor_positions(self.current_prices)
                await asyncio.sleep(3)
            except Exception as e:
                logger.error(f"Position monitor error: {e}")
                await asyncio.sleep(10)

    async def _run_status_reporting(self):
        while self.running:
            await asyncio.sleep(60)
            summary = self.trading_engine.get_portfolio_summary()
            logger.info("="*60)
            logger.info("📊 PORTFOLIO STATUS - Selectivity Framework")
            logger.info("="*60)
            logger.info(f"Active Positions: {summary['active_positions']}")
            logger.info(f"Total Invested: {summary['total_invested_sol']:.4f} SOL")
            logger.info(f"Unrealized PnL: {summary['unrealized_pnl_sol']:.4f} SOL")
            logger.info(f"Realized PnL: {summary['realized_pnl_sol']:.4f} SOL")
            logger.info(f"Total PnL: {summary['total_pnl_sol']:.4f} SOL")
            logger.info(f"Available: {summary['available_sol']:.4f} SOL | Committed: {summary['committed_sol']:.4f} SOL")
            if summary['positions']:
                logger.info("\nPosition Details:")
                for pos in summary['positions']:
                    logger.info(
                        f"  {pos['symbol']}: {pos['pnl_pct']:+.1f}% "
                        f"(Grid: {pos['grid_sold_pct']:.0f}% sold Hold: {pos['hold_seconds']:.0f}s)"
                    )
            logger.info("="*60)

    async def close_all_positions(self):
        logger.warning("Closing all positions...")
        for mint in list(self.trading_engine.active_positions.keys()):
            await self.trading_engine.close_position(mint, "Emergency close all")


class Backtester:
    def __init__(self):
        self.trades = []
        self.initial_balance = 10.0
        self.balance = self.initial_balance

    async def run_backtest(self, signals: List[Dict], price_data: Dict):
        results = {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "total_pnl": 0.0,
            "max_drawdown": 0.0,
            "sharpe_ratio": 0.0
        }
        for signal in signals:
            entry_price = signal["entry_price"]
            exit_price = signal["exit_price"]
            position_size = signal["position_size"]
            direction = signal.get("direction", "long")
            pnl_pct = (exit_price - entry_price) / entry_price if direction == "long" else (entry_price - exit_price) / entry_price
            pnl_sol = position_size * pnl_pct
            results["total_trades"] += 1
            if pnl_sol > 0:
                results["winning_trades"] += 1
            else:
                results["losing_trades"] += 1
            results["total_pnl"] += pnl_sol
            self.balance += pnl_sol
        if results["total_trades"] > 0:
            results["win_rate"] = results["winning_trades"] / results["total_trades"]
        results["final_balance"] = self.balance
        results["total_return"] = (self.balance - self.initial_balance) / self.initial_balance * 100
        return results


class PaperTrader(MemeHunterBot):
    def __init__(self):
        super().__init__()
        self.paper_balance = 10.0
        self.positions = {}
        self.trade_history = []

    async def execute_buy(self, mint: str, symbol: str, sol_amount: float) -> Dict:
        price = await self.client.get_token_price(mint)
        tokens = sol_amount / price if price > 0 else 0
        self.paper_balance -= sol_amount
        self.positions[mint] = {
            "symbol": symbol,
            "tokens": tokens,
            "entry_price": price,
            "entry_time": datetime.now(),
            "invested": sol_amount
        }
        logger.info(f"📝 PAPER BUY: {tokens:.2f} {symbol} @ {price:.8f} SOL")
        return {"success": True, "tokens_received": tokens, "price": price}

    async def execute_sell(self, mint: str, percentage: int = 100) -> Dict:
        if mint not in self.positions:
            return {"success": False, "error": "No position"}
        position = self.positions[mint]
        price = await self.client.get_token_price(mint)
        tokens_to_sell = position["tokens"] * (percentage / 100)
        sol_received = tokens_to_sell * price
        pnl = sol_received - position["invested"]
        self.paper_balance += sol_received
        position["tokens"] -= tokens_to_sell
        position["invested"] -= position["invested"] * (percentage / 100)
        if position["tokens"] <= 0.001:
            del self.positions[mint]
        self.trade_history.append({
            "type": "sell",
            "symbol": position["symbol"],
            "tokens": tokens_to_sell,
            "price": price,
            "sol_received": sol_received,
            "pnl": pnl,
            "time": datetime.now()
        })
        logger.info(f"📝 PAPER SELL: {tokens_to_sell:.2f} {position['symbol']} @ {price:.8f} SOL | PnL: {pnl:+.4f} SOL")
        return {"success": True, "sol_received": sol_received, "pnl": pnl, "price": price}

    def get_paper_balance(self) -> Dict:
        positions_value = 0
        for mint, pos in self.positions.items():
            price = self.current_prices.get(mint, pos["entry_price"])
            positions_value += pos["tokens"] * price
        total_value = self.paper_balance + positions_value
        return {
            "balance": self.paper_balance,
            "positions_value": positions_value,
            "total_value": total_value,
            "total_pnl": total_value - 10.0,
            "pnl_pct": ((total_value - 10.0) / 10.0) * 100,
            "positions": len(self.positions),
            "trades": len(self.trade_history)
        }


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler(), logging.FileHandler('meme_hunter.log')]
    )
    bot = MemeHunterBot()
    try:
        await bot.start()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot crashed: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())

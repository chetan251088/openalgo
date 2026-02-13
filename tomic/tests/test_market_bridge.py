from __future__ import annotations

import time
from datetime import datetime, timedelta

from tomic.agents.regime_agent import AtomicRegimeState, RegimeAgent
from tomic.agents.sniper_agent import SniperAgent
from tomic.agents.volatility_agent import VolatilityAgent
from tomic.config import TomicConfig
from tomic.freshness import FreshnessTracker
from tomic.market_bridge import OptionContract, SymbolSpec, TomicMarketBridge


class _NoopPublisher:
    def publish(self, _event) -> None:
        return None


class _FakeWSDataManager:
    def __init__(self) -> None:
        self.callback = None
        self.subscriptions = []
        self.mode = None
        self.added = []
        self.removed = []
        self.latest = {}

    def set_tick_callback(self, callback) -> None:
        self.callback = callback

    def subscribe(self, symbols, mode="QUOTE") -> None:
        self.subscriptions = symbols
        self.mode = mode

    def add_symbols(self, symbols) -> None:
        self.added.extend(symbols)
        existing = {(s["exchange"], s["symbol"]) for s in self.subscriptions}
        for item in symbols:
            key = (item.get("exchange"), item.get("symbol"))
            if key not in existing:
                self.subscriptions.append(item)
                existing.add(key)

    def remove_symbols(self, symbols) -> None:
        self.removed.extend(symbols)
        remove_keys = {(s.get("exchange"), s.get("symbol")) for s in symbols}
        self.subscriptions = [
            item
            for item in self.subscriptions
            if (item.get("exchange"), item.get("symbol")) not in remove_keys
        ]

    def emit(self, tick) -> None:
        symbol = str(tick.get("symbol", "")).upper()
        exchange = str(tick.get("exchange", "")).upper()
        payload = tick.get("data", {}) if isinstance(tick.get("data"), dict) else {}
        ltp = payload.get("ltp", payload.get("last_price", 0.0))
        key = (exchange, symbol)
        self.latest[key] = float(ltp or 0.0)
        if self.callback:
            self.callback(tick)

    def get_last_price(self, symbol: str, exchange: str = "", max_age_s: float = 15.0) -> float:
        key = (str(exchange or "").upper(), str(symbol or "").upper())
        if key in self.latest:
            return float(self.latest[key] or 0.0)
        # fallback by symbol-only
        symbol_u = str(symbol or "").upper()
        for (ex, sym), price in reversed(list(self.latest.items())):
            if sym == symbol_u:
                return float(price or 0.0)
        return 0.0


def test_market_bridge_feeds_regime_sniper_and_volatility(monkeypatch) -> None:
    monkeypatch.setenv(
        "TOMIC_FEED_OPTION_SYMBOLS",
        "NFO:NIFTY26FEB26000CE,NFO:NIFTY26FEB26000PE",
    )
    config = TomicConfig.load("sandbox")
    freshness = FreshnessTracker(config.freshness)
    regime_state = AtomicRegimeState()
    regime_agent = RegimeAgent(config=config, publisher=_NoopPublisher(), regime_state=regime_state)
    sniper_agent = SniperAgent(config=config, regime_state=regime_state)
    volatility_agent = VolatilityAgent(config=config, regime_state=regime_state)
    fake_ws = _FakeWSDataManager()

    bridge = TomicMarketBridge(
        config=config,
        ws_data_manager=fake_ws,
        freshness_tracker=freshness,
        regime_agent=regime_agent,
        sniper_agent=sniper_agent,
        volatility_agent=volatility_agent,
    )
    bridge.start()

    assert fake_ws.mode == "QUOTE"
    assert fake_ws.subscriptions

    base_ts = 1_700_000_000
    fake_ws.emit(
        {
            "type": "market_data",
            "symbol": "NIFTY",
            "exchange": "NSE_INDEX",
            "mode": "QUOTE",
            "data": {"ltp": 100.0, "volume": 1_000, "timestamp": base_ts},
            "_recv_wall": base_ts,
        }
    )
    fake_ws.emit(
        {
            "type": "market_data",
            "symbol": "NIFTY",
            "exchange": "NSE_INDEX",
            "mode": "QUOTE",
            "data": {"ltp": 102.0, "volume": 1_120, "timestamp": base_ts + 20},
            "_recv_wall": base_ts + 20,
        }
    )
    fake_ws.emit(
        {
            "type": "market_data",
            "symbol": "NIFTY",
            "exchange": "NSE_INDEX",
            "mode": "QUOTE",
            "data": {"ltp": 101.0, "volume": 1_300, "timestamp": base_ts + 65},
            "_recv_wall": base_ts + 65,
        }
    )

    # Candle is emitted only after minute rollover (third tick above).
    assert len(regime_agent._closes) >= 1
    assert len(sniper_agent._ohlcv_cache["NIFTY"]["C"]) >= 1
    assert len(volatility_agent._price_cache["NIFTY"]) >= 1

    fake_ws.emit(
        {
            "type": "market_data",
            "symbol": "NIFTY26FEB26000CE",
            "exchange": "NFO",
            "mode": "QUOTE",
            "data": {"ltp": 50.0, "iv": 18.0, "timestamp": base_ts + 70},
            "_recv_wall": base_ts + 70,
        }
    )
    fake_ws.emit(
        {
            "type": "market_data",
            "symbol": "NIFTY26FEB26000PE",
            "exchange": "NFO",
            "mode": "QUOTE",
            "data": {"ltp": 55.0, "iv": 20.0, "timestamp": base_ts + 72},
            "_recv_wall": base_ts + 72,
        }
    )

    assert "NIFTY" in volatility_agent._iv_cache
    assert volatility_agent._iv_cache["NIFTY"]["current_iv"] > 0
    assert bridge.get_status()["vol_iv_updates"] >= 2

    bridge.stop()


def test_market_bridge_computes_synthetic_iv_when_feed_iv_missing(monkeypatch) -> None:
    monkeypatch.setenv("TOMIC_FEED_OPTION_SYMBOLS", "NFO:NIFTY26FEB26000CE")
    monkeypatch.setenv("TOMIC_OPTION_IV_FALLBACK_ENABLED", "true")
    config = TomicConfig.load("sandbox")
    freshness = FreshnessTracker(config.freshness)
    regime_state = AtomicRegimeState()
    regime_agent = RegimeAgent(config=config, publisher=_NoopPublisher(), regime_state=regime_state)
    sniper_agent = SniperAgent(config=config, regime_state=regime_state)
    volatility_agent = VolatilityAgent(config=config, regime_state=regime_state)
    fake_ws = _FakeWSDataManager()

    bridge = TomicMarketBridge(
        config=config,
        ws_data_manager=fake_ws,
        freshness_tracker=freshness,
        regime_agent=regime_agent,
        sniper_agent=sniper_agent,
        volatility_agent=volatility_agent,
    )
    bridge.start()

    bridge._resolve_option_contract = lambda symbol, exchange, underlying_hint, option_type_hint: OptionContract(  # type: ignore[method-assign]
        underlying="NIFTY",
        option_type="CE",
        strike=26000.0,
        expiry=datetime.now() + timedelta(days=7),
    )

    class _Greeks:
        iv = 0.19

    bridge._iv_engine.compute = lambda **kwargs: _Greeks()  # type: ignore[method-assign]

    base_ts = 1_700_000_000
    fake_ws.emit(
        {
            "type": "market_data",
            "symbol": "NIFTY",
            "exchange": "NSE_INDEX",
            "mode": "QUOTE",
            "data": {"ltp": 25980.0, "volume": 1500, "timestamp": base_ts},
            "_recv_wall": base_ts,
        }
    )
    fake_ws.emit(
        {
            "type": "market_data",
            "symbol": "NIFTY26FEB26000CE",
            "exchange": "NFO",
            "mode": "QUOTE",
            "data": {"ltp": 120.0, "timestamp": base_ts + 1},
            "_recv_wall": base_ts + 1,
        }
    )
    fake_ws.emit(
        {
            "type": "market_data",
            "symbol": "NIFTY26FEB26000CE",
            "exchange": "NFO",
            "mode": "QUOTE",
            "data": {"ltp": 121.0, "timestamp": base_ts + 2},
            "_recv_wall": base_ts + 2,
        }
    )
    fake_ws.emit(
        {
            "type": "market_data",
            "symbol": "NIFTY26FEB26000PE",
            "exchange": "NFO",
            "mode": "QUOTE",
            "data": {"ltp": 130.0, "timestamp": base_ts + 3},
            "_recv_wall": base_ts + 3,
        }
    )

    status = bridge.get_status()
    assert status["option_iv_from_synthetic"] >= 1
    assert status["vol_iv_updates"] >= 1
    assert "NIFTY" in volatility_agent._iv_cache
    assert volatility_agent._iv_cache["NIFTY"]["current_iv"] > 0

    bridge.stop()


def test_market_bridge_auto_option_discovery_hot_subscribes(monkeypatch) -> None:
    monkeypatch.delenv("TOMIC_FEED_OPTION_SYMBOLS", raising=False)
    config = TomicConfig.load("sandbox")
    freshness = FreshnessTracker(config.freshness)
    regime_state = AtomicRegimeState()
    regime_agent = RegimeAgent(config=config, publisher=_NoopPublisher(), regime_state=regime_state)
    sniper_agent = SniperAgent(config=config, regime_state=regime_state)
    volatility_agent = VolatilityAgent(config=config, regime_state=regime_state)
    fake_ws = _FakeWSDataManager()

    bridge = TomicMarketBridge(
        config=config,
        ws_data_manager=fake_ws,
        freshness_tracker=freshness,
        regime_agent=regime_agent,
        sniper_agent=sniper_agent,
        volatility_agent=volatility_agent,
    )
    bridge.start()

    bridge._discover_option_symbols_for_underlying = lambda underlying, exchange, ltp: (
        [
            SymbolSpec(exchange="NFO", symbol="NIFTY26FEB26000CE"),
            SymbolSpec(exchange="NFO", symbol="NIFTY26FEB26000PE"),
        ],
        "26FEB26",
        50.0,
    )
    bridge._refresh_auto_option_symbols_worker("NIFTY", "NSE_INDEX", 23000.0, time.time())

    status = bridge.get_status()
    assert status["option_symbol_mode"] == "auto"
    assert any(spec["symbol"] == "NIFTY26FEB26000CE" for spec in fake_ws.added)
    assert any(spec["symbol"] == "NIFTY26FEB26000PE" for spec in fake_ws.added)
    assert "NFO:NIFTY26FEB26000CE" in status["option_symbols"]
    assert "NFO:NIFTY26FEB26000PE" in status["option_symbols"]

    bridge.stop()


def test_market_bridge_default_sniper_symbols_exclude_indiavix(monkeypatch) -> None:
    monkeypatch.delenv("TOMIC_SNIPER_SYMBOLS", raising=False)
    config = TomicConfig.load("sandbox")
    freshness = FreshnessTracker(config.freshness)
    regime_state = AtomicRegimeState()
    regime_agent = RegimeAgent(config=config, publisher=_NoopPublisher(), regime_state=regime_state)
    sniper_agent = SniperAgent(config=config, regime_state=regime_state)
    volatility_agent = VolatilityAgent(config=config, regime_state=regime_state)
    fake_ws = _FakeWSDataManager()

    bridge = TomicMarketBridge(
        config=config,
        ws_data_manager=fake_ws,
        freshness_tracker=freshness,
        regime_agent=regime_agent,
        sniper_agent=sniper_agent,
        volatility_agent=volatility_agent,
    )

    sniper_symbols = {spec.symbol for spec in bridge._sniper_symbols}
    assert "INDIAVIX" not in sniper_symbols


def test_market_bridge_selects_current_and_next_expiry() -> None:
    expiries = ["20-FEB-26", "27-FEB-26", "05-MAR-26"]
    selected = TomicMarketBridge._select_front_expiries(expiries, max_count=2)
    assert selected == ["20FEB26", "27FEB26"]


def test_market_bridge_interested_strike_offsets(monkeypatch) -> None:
    monkeypatch.setenv("TOMIC_OPTION_AUTO_STRIKE_OFFSETS", "-1,0,1")
    monkeypatch.delenv("TOMIC_FEED_OPTION_SYMBOLS", raising=False)
    config = TomicConfig.load("sandbox")
    freshness = FreshnessTracker(config.freshness)
    regime_state = AtomicRegimeState()
    regime_agent = RegimeAgent(config=config, publisher=_NoopPublisher(), regime_state=regime_state)
    sniper_agent = SniperAgent(config=config, regime_state=regime_state)
    volatility_agent = VolatilityAgent(config=config, regime_state=regime_state)
    fake_ws = _FakeWSDataManager()

    bridge = TomicMarketBridge(
        config=config,
        ws_data_manager=fake_ws,
        freshness_tracker=freshness,
        regime_agent=regime_agent,
        sniper_agent=sniper_agent,
        volatility_agent=volatility_agent,
    )

    strikes = [22500.0, 22550.0, 22600.0, 22650.0, 22700.0]
    selected = bridge._select_interested_strikes(strikes, atm_index=2)
    assert selected == [22550.0, 22600.0, 22650.0]


def test_market_bridge_regime_uses_benchmark_only(monkeypatch) -> None:
    monkeypatch.delenv("TOMIC_FEED_OPTION_SYMBOLS", raising=False)
    monkeypatch.setenv("TOMIC_FEED_UNDERLYINGS", "NSE_INDEX:NIFTY,NSE_INDEX:BANKNIFTY")
    config = TomicConfig.load("sandbox")
    freshness = FreshnessTracker(config.freshness)
    regime_state = AtomicRegimeState()
    regime_agent = RegimeAgent(config=config, publisher=_NoopPublisher(), regime_state=regime_state)
    sniper_agent = SniperAgent(config=config, regime_state=regime_state)
    volatility_agent = VolatilityAgent(config=config, regime_state=regime_state)
    fake_ws = _FakeWSDataManager()

    bridge = TomicMarketBridge(
        config=config,
        ws_data_manager=fake_ws,
        freshness_tracker=freshness,
        regime_agent=regime_agent,
        sniper_agent=sniper_agent,
        volatility_agent=volatility_agent,
    )
    bridge.start()

    base_ts = 1_700_100_000
    for idx, price in enumerate([60000.0, 60020.0, 60010.0]):
        fake_ws.emit(
            {
                "type": "market_data",
                "symbol": "BANKNIFTY",
                "exchange": "NSE_INDEX",
                "mode": "QUOTE",
                "data": {"ltp": price, "volume": 1000 + idx * 100, "timestamp": base_ts + idx * 65},
                "_recv_wall": base_ts + idx * 65,
            }
        )

    # BANKNIFTY still updates volatility input cache.
    assert len(volatility_agent._price_cache.get("BANKNIFTY", [])) >= 1
    # Regime must not ingest non-benchmark candles.
    assert len(regime_agent._closes) == 0

    bridge.stop()

"""Tests for MarketContextAgent."""
import pytest
from tomic.agents.market_context_agent import (
    MarketContextAgent, AtomicMarketContext, MarketContext,
    classify_vix_regime, classify_pcr_bias,
)
from tomic.config import TomicConfig


def test_classify_vix_regime():
    assert classify_vix_regime(10.0) == "TOO_LOW"
    assert classify_vix_regime(14.0) == "NORMAL"
    assert classify_vix_regime(20.0) == "ELEVATED"
    assert classify_vix_regime(28.0) == "HIGH"
    assert classify_vix_regime(40.0) == "EXTREME"


def test_classify_pcr_bias():
    assert classify_pcr_bias(1.5) == "BULLISH"
    assert classify_pcr_bias(0.6) == "BEARISH"
    assert classify_pcr_bias(1.0) == "NEUTRAL"


def test_atomic_market_context_thread_safe():
    ctx = AtomicMarketContext()
    mc = MarketContext(vix=16.0, vix_regime="NORMAL", pcr=1.1, pcr_bias="NEUTRAL")
    ctx.update(mc)
    snap = ctx.read()
    assert snap.vix == 16.0
    assert snap.vix_regime == "NORMAL"


def test_feed_vix_updates_context():
    config = TomicConfig()
    agent = MarketContextAgent(config=config)
    agent.feed_vix(17.5)
    ctx = agent.read_context()
    assert ctx.vix == 17.5
    assert ctx.vix_regime == "NORMAL"


def test_feed_pcr_updates_context():
    config = TomicConfig()
    agent = MarketContextAgent(config=config)
    agent.feed_pcr(1.4, instrument="NIFTY")
    ctx = agent.read_context()
    assert ctx.pcr == 1.4
    assert ctx.pcr_bias == "BULLISH"


def test_trend_ma_computes_correctly():
    config = TomicConfig()
    agent = MarketContextAgent(config=config)
    # Feed 25 candles with incrementally rising closes
    for i in range(25):
        agent.feed_candle(underlying="NIFTY", close=25000.0 + i * 10)
    ctx = agent.read_context()
    # Last close is above 20-period MA → ABOVE_20MA
    assert ctx.nifty_trend == "ABOVE_20MA"

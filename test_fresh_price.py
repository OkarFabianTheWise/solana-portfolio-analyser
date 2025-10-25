#!/usr/bin/env python3
"""
Test script to verify the agent fetches fresh price data from CoinGecko
"""

import asyncio
import json
from datetime import datetime, timezone
from uuid import uuid4
from uagents import Context, Agent
from models import PriceRequest, TradeSignal

# Test agent configuration
test_agent = Agent(
    name="test-price-client",
    port=8009,
    mailbox=False
)

# Your main agent address (update if needed)
MAIN_AGENT = "agent1qfkgrw7tayq4ng6tpx5azhvxmm3aeug3uf9sm78erm7zp4jk4p26jyms85a"

@test_agent.on_message(model=TradeSignal)
async def handle_trade_signal(ctx: Context, sender: str, msg: TradeSignal):
    ctx.logger.info(f"📊 Received TradeSignal from {sender}")
    ctx.logger.info(f"   Signal: {msg.signal}")
    ctx.logger.info(f"   Percent: {msg.percent}%")
    
    if msg.signal == "FETCHING_CURRENT_PRICE":
        ctx.logger.info("✅ Agent is fetching current price data - this is expected!")
    elif msg.signal in ["BUY", "SELL", "HOLD"]:
        ctx.logger.info("✅ Received final trading recommendation!")
    else:
        ctx.logger.warning(f"Unexpected signal: {msg.signal}")

@test_agent.on_event("startup")
async def test_price_request(ctx: Context):
    ctx.logger.info("🚀 Starting fresh price test...")
    
    # Wait a moment for the agent to be ready
    await asyncio.sleep(2)
    
    # Test case: SOL position with entry at $120, holdings of 2 SOL
    # Using outdated price to force fresh fetch
    test_request = PriceRequest(
        token="SOL",
        current_price=100.0,  # Intentionally outdated/incorrect price
        entry_price=120.0,
        historical_prices=[115.0, 118.0, 122.0, 119.0, 125.0],
        current_holdings=2.0
    )
    
    ctx.logger.info("📤 Sending test PriceRequest with outdated SOL price...")
    ctx.logger.info(f"   Token: {test_request.token}")
    ctx.logger.info(f"   Provided Price: ${test_request.current_price} (intentionally outdated)")
    ctx.logger.info(f"   Entry Price: ${test_request.entry_price}")
    ctx.logger.info(f"   Holdings: {test_request.current_holdings} SOL")
    
    try:
        await ctx.send(MAIN_AGENT, test_request)
        ctx.logger.info("✅ Test request sent successfully!")
        ctx.logger.info("⏳ Waiting for agent to fetch current price and respond...")
    except Exception as e:
        ctx.logger.error(f"❌ Error sending test request: {e}")

if __name__ == "__main__":
    print("🧪 Testing Fresh Price Data Integration")
    print("=" * 50)
    print("This test will:")
    print("1. Send a PriceRequest with outdated SOL price data")
    print("2. Verify the agent fetches current price from CoinGecko")
    print("3. Receive updated trading recommendation")
    print("=" * 50)
    
    test_agent.run()
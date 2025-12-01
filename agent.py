# agent.py

from datetime import datetime, timezone
from uuid import uuid4
from typing import Any, Dict
import json
import os
from dotenv import load_dotenv
from uagents import Context, Model, Protocol, Agent
from hyperon import MeTTa
import re

from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    EndSessionContent,
    StartSessionContent,
    TextContent,
    chat_protocol_spec,
)

from metta.investment_rag import SolanaPortfolioRAG
from metta.knowledge import initialize_solana_knowledge
from metta.utils import LLM, process_chat_query, process_trading_data

# Import shared message models
from models import PriceRequest, TradeSignal, PriceResponse

load_dotenv()

# Get the Heroku app URL from environment variable
# HEROKU_URL = os.getenv("HEROKU_URL", "https://c8f44c24cbc8.ngrok-free.app")
HEROKU_URL = "https://99440419bcf0.ngrok-free.app"

agent = Agent(
    name="fiatrouter-icm", 
    port=int(os.getenv("PORT", 5000)), 
    mailbox=False,  # Disable mailbox for Heroku deployment
    publish_agent_details=True,
    endpoint=[f"{HEROKU_URL}/submit"]
)

# CoinGecko agent address
COINGECKO_AGENT = "agent1qfkgrw7tayq4ng6tpx5azhvxmm3aeug3uf9sm78erm7zp4jk4p26jyms85a"

def create_text_chat(text: str, end_session: bool = False) -> ChatMessage:
    content = [TextContent(type="text", text=text)]
    if end_session:
        content.append(EndSessionContent(type="end-session"))
    return ChatMessage(
        timestamp=datetime.now(timezone.utc),
        msg_id=uuid4(),
        content=content,
    )

def extract_token_from_query(query: str) -> str:
    """Extract token name from price query."""
    # Words to ignore (common words that might match patterns)
    ignore_words = {"a", "an", "the", "is", "are", "was", "were", "be", "been", 
                    "buy", "sell", "and", "or", "i", "you", "me", "we", "they",
                    "what", "that", "this", "current", "good", "move", "price"}
    
    # Common patterns for price queries (ordered by specificity)
    patterns = [
        (r"buy\s+(\w+)\s+and\s+buy\s+(\w+)", 2),  # "buy X and buy Y" - get second token
        (r"buy\s+(\w+)\s+and\s+(\w+)", 2),         # "buy X and Y" - get second token  
        (r"sell\s+and\s+buy\s+(\w+)", 1),         # "sell and buy X"
        (r"price\s+of\s+(\w+)", 1),               # "price of X"
        (r"(\w+)\s+price", 1),                    # "X price"
        (r"what\s+is\s+(\w+)", 1),                # "what is X"
        (r"check\s+(\w+)", 1),                    # "check X"
        (r"(\w+)\s+token", 1),                    # "X token"
        (r"(\w+)\s+cost", 1),                     # "X cost"
        (r"how\s+much\s+is\s+(\w+)", 1),          # "how much is X"
        (r"(\w+)\s+worth", 1),                    # "X worth"
        (r"(\w+)\s+trading\s+at", 1),             # "X trading at"
        (r"(\w+)\s+value", 1),                    # "X value"
        (r"get\s+(\w+)\s+price", 1),              # "get X price"
    ]
    
    query_lower = query.lower()
    for pattern, group_idx in patterns:
        match = re.search(pattern, query_lower, re.IGNORECASE)
        if match:
            token = match.group(group_idx).upper()
            
            # Skip if it's a common ignored word
            if token.lower() in ignore_words:
                continue
            
            # Handle common token mappings
            token_mappings = {
                "SOLANA": "SOL",
                "BITCOIN": "BTC", 
                "ETHEREUM": "ETH",
                "PEPE": "PEPE",
                "CARDANO": "ADA",
                "POLYGON": "MATIC",
                "AVALANCHE": "AVAX",
                "CHAINLINK": "LINK",
                "UNISWAP": "UNI",
                "RAYDIUM": "RAY"
            }
            return token_mappings.get(token, token)
    
    return None

def extract_price_from_query(query: str) -> float:
    """Extract price value from query (e.g., '$20' from 'I bought 3 SOL at $20')."""
    # Look for price patterns: $20, $20.50, 20 dollars, etc.
    patterns = [
        r"\$\s*([\d.]+)",           # $20 or $ 20
        r"([\d.]+)\s*dollars",      # 20 dollars
        r"at\s*\$?\s*([\d.]+)",     # at $20 or at 20
        r"([\d.]+)\s*usd",          # 20 USD
    ]
    
    query_lower = query.lower()
    for pattern in patterns:
        match = re.search(pattern, query_lower, re.IGNORECASE)
        if match:
            try:
                return float(match.group(1))
            except (ValueError, IndexError):
                continue
    
    return 0.0

async def request_price_from_coingecko(ctx: Context, token: str) -> None:
    """Request price data from CoinGecko agent."""
    price_query = f"What is the price of {token}?"
    ctx.logger.info(f"🔍 Requesting current price for {token} from CoinGecko agent")
    
    try:
        await ctx.send(COINGECKO_AGENT, create_text_chat(price_query))
        ctx.logger.info(f"📤 Sent price request to CoinGecko for {token} - awaiting response...")
    except Exception as e:
        ctx.logger.error(f"❌ Error sending price request to CoinGecko: {e}")
        
        # Clean up any pending requests for this token if the send fails
        keys_to_remove = []
        for key in ctx.storage.keys():
            request_data = ctx.storage.get(key)
            if request_data and request_data.get("token", "").upper() == token.upper():
                keys_to_remove.append(key)
        
        for key in keys_to_remove:
            ctx.storage.delete(key)

metta = MeTTa()
initialize_solana_knowledge(metta)
rag = SolanaPortfolioRAG(metta)
llm = LLM(api_key=os.getenv("ASI_ONE_API_KEY"))

# Chat protocol for human interaction
chat_proto = Protocol(spec=chat_protocol_spec)

@chat_proto.on_message(ChatMessage)
async def handle_chat_message(ctx: Context, sender: str, msg: ChatMessage):
    # Handle CoinGecko responses
    if sender == COINGECKO_AGENT:
        ctx.logger.info(f"📥 Received response from CoinGecko: {sender}")
        
        for item in msg.content:
            if isinstance(item, TextContent):
                price_text = item.text.strip()
                ctx.logger.info(f"CoinGecko price data: {price_text}")
                
                # Parse price from CoinGecko response
                price_pattern = r"price of .* is \$?([\d.e\-+]+)"
                price_match = re.search(price_pattern, price_text, re.IGNORECASE)
                
                if price_match:
                    price_value = float(price_match.group(1))
                    
                    # Process both chat price requests and trading requests
                    processed_keys = []
                    
                    for key in list(ctx.storage.keys()):
                        request_data = ctx.storage.get(key)
                        if not request_data:
                            continue
                            
                        token = request_data.get("token", "").upper()
                        
                        # Check if this response matches the token requested
                        if token and token.lower() in price_text.lower():
                            
                            if key.startswith("price_request_"):
                                # Handle chat price requests
                                original_sender = request_data["sender"]
                                original_query = request_data["query"]
                                
                                # Generate enhanced response with portfolio analysis
                                enhanced_response = f"💰 **Current {token} Price: ${price_value:.8f} USD**\n\n"
                                enhanced_response += f"📊 {price_text}\n\n"
                                
                                # Add portfolio analysis context
                                try:
                                    analysis_query = f"Analyze {token} at ${price_value} for portfolio inclusion"
                                    portfolio_response = process_chat_query(analysis_query, rag, llm)
                                    if isinstance(portfolio_response, dict):
                                        enhanced_response += f"**Portfolio Analysis:**\n{portfolio_response.get('humanized_answer', '')}"
                                except Exception as e:
                                    ctx.logger.error(f"Error generating portfolio analysis: {e}")
                                    enhanced_response += "📈 Consider your risk tolerance and portfolio allocation when trading this token."
                                
                                # Send enhanced response to original requester
                                await ctx.send(original_sender, create_text_chat(enhanced_response))
                                ctx.logger.info(f"✅ Sent enhanced price response to {original_sender}")
                                
                            elif key.startswith("trading_request_"):
                                # Handle trading signal requests that needed price data
                                original_sender = request_data["sender"]
                                provided_price = request_data.get("provided_price", 0)
                                
                                # Log price comparison for debugging
                                ctx.logger.info(f"💹 Price comparison for {token}: Provided=${provided_price:.4f}, Current=${price_value:.4f}")
                                
                                # Create price data with fetched current price
                                price_data = {
                                    "token": token,
                                    "current_price": price_value,
                                    "entry_price": request_data.get("entry_price", 0),
                                    "historical_prices": request_data.get("historical_prices", []),
                                    "current_holdings": request_data.get("current_holdings", 0)
                                }
                                
                                # Generate trading signal with updated price
                                try:
                                    signal_result = process_trading_data(price_data, rag)
                                    
                                    trade_signal = TradeSignal(
                                        signal=signal_result["signal"],
                                        percent=signal_result["percent"]
                                    )
                                    
                                    await ctx.send(original_sender, trade_signal)
                                    ctx.logger.info(f"📤 Sent updated TradeSignal to {original_sender}: {trade_signal.signal} {trade_signal.percent}%")
                                    ctx.logger.info(f"🧠 Analysis used: Entry=${price_data['entry_price']}, Current=${price_data['current_price']}, Holdings={price_data['current_holdings']}")
                                    
                                except Exception as e:
                                    ctx.logger.error(f"Error generating trading signal with updated price: {e}")
                                    await ctx.send(original_sender, TradeSignal(signal="HOLD", percent=0.0))
                            
                            processed_keys.append(key)
                    
                    # Clean up processed requests
                    for key in processed_keys:
                        ctx.storage.delete(key)
        return
    
    # Handle regular chat messages
    ctx.storage.set(str(ctx.session), sender)
    await ctx.send(
        sender,
        ChatAcknowledgement(timestamp=datetime.now(timezone.utc), acknowledged_msg_id=msg.msg_id),
    )

    for item in msg.content:
        if isinstance(item, StartSessionContent):
            ctx.logger.info(f"Got a start session message from {sender}")
            continue
        elif isinstance(item, TextContent):
            user_query = item.text.strip()
            ctx.logger.info(f"Got a Solana portfolio query from {sender}: {user_query}")
            
            # Check if this is a price query
            price_keywords = ["price", "cost", "value", "worth", "trading at"]
            is_price_query = any(keyword in user_query.lower() for keyword in price_keywords)
            
            if is_price_query:
                token = extract_token_from_query(user_query)
                if token:
                    ctx.logger.info(f"🔍 Detected price query for token: {token}")
                    # Extract entry price if provided
                    entry_price = extract_price_from_query(user_query)
                    ctx.logger.info(f"💰 Extracted entry price: ${entry_price}")
                    
                    # Store the original sender and query for when we get the CoinGecko response
                    ctx.storage.set(f"price_request_{sender}", {
                        "token": token,
                        "query": user_query,
                        "sender": sender,
                        "entry_price": entry_price,
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    })
                    
                    # Request price from CoinGecko
                    await request_price_from_coingecko(ctx, token)
                    
                    # Send acknowledgment to user
                    await ctx.send(sender, create_text_chat(f"🔍 Fetching current price for {token}... Please wait a moment."))
                    return
            
            try:
                response = process_chat_query(user_query, rag, llm)
                
                if isinstance(response, dict):
                    answer_text = f"**{response.get('selected_question', user_query)}**\n\n{response.get('humanized_answer', 'I apologize, but I could not process your query.')}"
                else:
                    answer_text = str(response)
                
                await ctx.send(sender, create_text_chat(answer_text))
                
            except Exception as e:
                ctx.logger.error(f"Error processing Solana query: {e}")
                await ctx.send(
                    sender, 
                    create_text_chat("I apologize, but I encountered an error processing your Solana portfolio query. Please try again.")
                )
        else:
            ctx.logger.info(f"Got unexpected content from {sender}")

@chat_proto.on_message(ChatAcknowledgement)
async def handle_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    ctx.logger.info(f"Got an acknowledgement from {sender} for {msg.acknowledged_msg_id}")

# Agent-to-agent trading protocol
@agent.on_message(model=PriceRequest)
async def handle_price_request(ctx: Context, sender: str, msg: PriceRequest):
    ctx.logger.info(f"📊 Received PriceRequest from {sender} for {msg.token}")
    try:
        # Always fetch current price from CoinGecko for accurate analysis
        # This ensures we have the most up-to-date market data
        ctx.logger.info(f"🔍 Fetching current price for {msg.token} from CoinGecko for accurate analysis")
        
        # Store the trading request for when we get the fresh price data
        ctx.storage.set(f"trading_request_{sender}", {
            "sender": sender,
            "token": msg.token,
            "entry_price": msg.entry_price,
            "historical_prices": msg.historical_prices,
            "current_holdings": msg.current_holdings,
            "provided_price": msg.current_price,  # Keep the provided price for reference
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        
        # Request current price from CoinGecko
        await request_price_from_coingecko(ctx, msg.token)
        
        # Send acknowledgment that we're fetching current data
        await ctx.send(sender, TradeSignal(signal="FETCHING_CURRENT_PRICE", percent=0.0))
        return
        
        # Fallback: Use provided price if CoinGecko fetch fails or for backup analysis
        # This code should not normally be reached as we now always fetch from CoinGecko
        ctx.logger.warning(f"⚠️ Using fallback analysis with provided price ${msg.current_price} for {msg.token}")
        
        # Convert PriceRequest to dict for processing
        price_data = {
            "token": msg.token,
            "current_price": msg.current_price,
            "entry_price": msg.entry_price,
            "historical_prices": msg.historical_prices,
            "current_holdings": msg.current_holdings
        }
        
        # Generate trading signal
        signal_result = process_trading_data(price_data, rag)
        
        # Create and send TradeSignal response
        trade_signal = TradeSignal(
            signal=signal_result["signal"],
            percent=signal_result["percent"]
        )
        
        await ctx.send(sender, trade_signal)
        ctx.logger.info(f"📤 Sent fallback TradeSignal to {sender}: {trade_signal.signal} {trade_signal.percent}%")
        
        # Log analysis for debugging
        ctx.logger.info(f"Fallback Analysis: {signal_result['analysis']}")
        
    except Exception as e:
        ctx.logger.error(f"Error processing PriceRequest: {e}")
        # Send a default HOLD signal on error
        await ctx.send(sender, TradeSignal(signal="HOLD", percent=0.0))

agent.include(chat_proto, publish_manifest=True)

if __name__ == "__main__":
    agent.run()
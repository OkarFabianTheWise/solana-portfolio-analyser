from flask import Flask, render_template, request, jsonify
from datetime import datetime
from uuid import uuid4
import asyncio
import threading
import time
from uagents import Agent, Context
from uagents_core.contrib.protocols.chat import ChatMessage, ChatAcknowledgement, TextContent

app = Flask(__name__)
app.secret_key = 'your-secret-key-change-this'
from flask_cors import CORS

# Enable CORS for local frontend
CORS(
    app,
    resources={r"/*": {"origins": ["http://localhost:3001", "http://127.0.0.1:3001"]}},
    supports_credentials=True
)

# Your fiatrouter-icm agent address
AGENT_ADDRESS = "agent1qvsqzmw3x0nw2czxhf02zvprvdstylthlwt84uaawj9yr2zne2l4q2r3etl"

# Store responses in memory
responses = {}
pending_requests = {}

# Create the agent
client = Agent(
    name="flask-client",
    seed="flask-seed-456",
    port=8004,
    endpoint=["http://127.0.0.1:8004/submit"],
)

@client.on_message(ChatAcknowledgement)
async def handle_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    msg_id = str(msg.acknowledged_msg_id)
    ctx.logger.info(f"Got acknowledgement from {sender} for {msg_id}")
    if msg_id in pending_requests:
        pending_requests[msg_id]['status'] = 'acknowledged'

@client.on_message(ChatMessage)
async def handle_response(ctx: Context, sender: str, msg: ChatMessage):
    ctx.logger.info(f"Received message from {sender}")
    for item in msg.content:
        if isinstance(item, TextContent):
            # Store the response with the original msg_id
            # We need to match it with pending requests
            for msg_id, req_data in list(pending_requests.items()):
                if req_data['status'] in ['sending', 'acknowledged']:
                    responses[msg_id] = {
                        'text': item.text,
                        'timestamp': datetime.now().isoformat(),
                        'status': 'complete'
                    }
                    req_data['status'] = 'complete'
                    ctx.logger.info(f"Stored response for {msg_id}")
                    break

# Global variable to store queries to send
queries_to_send = []

@client.on_interval(period=1.0)
async def send_pending_queries(ctx: Context):
    """Check for pending queries and send them"""
    global queries_to_send
    if queries_to_send:
        query_data = queries_to_send.pop(0)
        ctx.logger.info(f"Sending message to {AGENT_ADDRESS}")
        await ctx.send(
            AGENT_ADDRESS,
            query_data['message']
        )
        pending_requests[query_data['msg_id']]['status'] = 'sent'

# Run agent in background thread
agent_ready = threading.Event()

def run_agent():
    try:
        client.run()
    except Exception as e:
        print(f"Agent error: {e}")

agent_thread = threading.Thread(target=run_agent, daemon=True)
agent_thread.start()

# Give the agent time to start up
time.sleep(3)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/send_query', methods=['POST'])
def send_query():
    data = request.json
    query = data.get('query', '')
    
    if not query:
        return jsonify({'error': 'Query cannot be empty'}), 400
    
    # Generate unique message ID
    msg_id = uuid4()
    msg_id_str = str(msg_id)
    
    # Create the message
    chat_message = ChatMessage(
        timestamp=datetime.now(),
        msg_id=msg_id,
        content=[TextContent(type="text", text=query)],
    )
    
    # Store in pending requests
    pending_requests[msg_id_str] = {
        'query': query,
        'status': 'sending',
        'timestamp': datetime.now().isoformat()
    }
    
    # Add to queue for sending
    queries_to_send.append({
        'msg_id': msg_id_str,
        'message': chat_message
    })
    
    print(f"Queued message {msg_id_str} for sending")
    
    return jsonify({
        'message_id': msg_id_str,
        'status': 'queued'
    })

@app.route('/get_response/<message_id>')
def get_response(message_id):
    if message_id in responses:
        return jsonify(responses[message_id])
    elif message_id in pending_requests:
        return jsonify({
            'status': pending_requests[message_id]['status'],
            'text': None
        })
    else:
        return jsonify({'error': 'Message ID not found'}), 404

@app.route('/agent_status')
def agent_status():
    return jsonify({
        'agent_address': client.address,
        'agent_name': client.name,
        'target_address': AGENT_ADDRESS,
        'pending_count': len([r for r in pending_requests.values() if r['status'] != 'complete']),
        'completed_count': len(responses)
    })

# ============================================
# PUBLIC API ENDPOINTS
# ============================================

@app.route('/api/query', methods=['POST'])
def api_query():
    """
    Public API endpoint for querying the agent
    
    Usage:
        POST /api/query
        Content-Type: application/json
        
        {
            "query": "Your question here",
            "wait_for_response": true  // optional, defaults to false
        }
    
    Response:
        {
            "success": true,
            "message_id": "uuid-here",
            "status": "queued",
            "response": "..." // only if wait_for_response=true
        }
    """
    try:
        data = request.json
        if not data or 'query' not in data:
            return jsonify({
                'success': False,
                'error': 'Missing "query" field in request body'
            }), 400
        
        query = data.get('query', '').strip()
        wait_for_response = data.get('wait_for_response', False)
        
        if not query:
            return jsonify({
                'success': False,
                'error': 'Query cannot be empty'
            }), 400
        
        # Generate unique message ID
        msg_id = uuid4()
        msg_id_str = str(msg_id)
        
        # Create the message
        chat_message = ChatMessage(
            timestamp=datetime.now(),
            msg_id=msg_id,
            content=[TextContent(type="text", text=query)],
        )
        
        # Store in pending requests
        pending_requests[msg_id_str] = {
            'query': query,
            'status': 'sending',
            'timestamp': datetime.now().isoformat()
        }
        
        # Add to queue for sending
        queries_to_send.append({
            'msg_id': msg_id_str,
            'message': chat_message
        })
        
        response_data = {
            'success': True,
            'message_id': msg_id_str,
            'status': 'queued',
            # 'query': query
        }
        
        # If wait_for_response is true, poll for the response
        if wait_for_response:
            max_wait = 60  # Maximum 60 seconds
            poll_interval = 0.5  # Check every 0.5 seconds
            
            for _ in range(int(max_wait / poll_interval)):
                time.sleep(poll_interval)
                
                if msg_id_str in responses:
                    response_data['status'] = 'complete'

                    raw = responses[msg_id_str]['text']
                    query = pending_requests[msg_id_str]['query']

                    cleaned = raw.replace(query, "").strip()

                    response_data['response'] = cleaned
                    response_data['completed_at'] = responses[msg_id_str]['timestamp']
                    break

                elif msg_id_str in pending_requests:
                    response_data['status'] = pending_requests[msg_id_str]['status']
            else:
                # Timeout
                response_data['status'] = 'timeout'
                response_data['error'] = 'Response not received within timeout period'
        
        return jsonify(response_data)
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/response/<message_id>', methods=['GET'])
def api_get_response(message_id):
    """
    Get the response for a specific message ID
    
    Usage:
        GET /api/response/<message_id>
    
    Response:
        {
            "success": true,
            "message_id": "uuid-here",
            "status": "complete",
            "response": "...",
            "completed_at": "timestamp"
        }
    """
    try:
        if message_id in responses:
            return jsonify({
                'success': True,
                'message_id': message_id,
                'status': 'complete',
                'response': responses[message_id]['text'],
                'completed_at': responses[message_id]['timestamp']
            })
        elif message_id in pending_requests:
            return jsonify({
                'success': True,
                'message_id': message_id,
                'status': pending_requests[message_id]['status'],
                'query': pending_requests[message_id]['query'],
                'queued_at': pending_requests[message_id]['timestamp']
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Message ID not found'
            }), 404
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/docs', methods=['GET'])
def api_docs():
    """API Documentation"""
    docs = {
        'name': 'ASI:One Agent API',
        'version': '1.0',
        'description': 'REST API for querying the ASI:One financial intelligence agent',
        'endpoints': [
            {
                'path': '/api/query',
                'method': 'POST',
                'description': 'Submit a query to the agent',
                'parameters': {
                    'query': 'string (required) - Your question',
                    'wait_for_response': 'boolean (optional) - Wait for agent response (default: false)'
                },
                'example': {
                    'request': {
                        'query': 'Should I sell my SOL and buy ETH?',
                        'wait_for_response': True
                    },
                    'response': {
                        'success': True,
                        'message_id': 'uuid-here',
                        'status': 'complete',
                        'response': 'Agent response here...'
                    }
                }
            },
            {
                'path': '/api/response/<message_id>',
                'method': 'GET',
                'description': 'Get the response for a specific message',
                'parameters': {
                    'message_id': 'string (required) - Message ID from /api/query'
                },
                'example': {
                    'response': {
                        'success': True,
                        'message_id': 'uuid-here',
                        'status': 'complete',
                        'response': 'Agent response here...'
                    }
                }
            },
            {
                'path': '/api/docs',
                'method': 'GET',
                'description': 'API documentation (this page)'
            }
        ],
        'curl_examples': [
            {
                'description': 'Send a query (async)',
                'command': 'curl -X POST http://your-server:5000/api/query -H "Content-Type: application/json" -d \'{"query": "Should I invest in SOL?"}\''
            },
            {
                'description': 'Send a query and wait for response (sync)',
                'command': 'curl -X POST http://your-server:5000/api/query -H "Content-Type: application/json" -d \'{"query": "Should I invest in SOL?", "wait_for_response": true}\''
            },
            {
                'description': 'Get response by message ID',
                'command': 'curl http://your-server:5000/api/response/<message_id>'
            }
        ]
    }
    return jsonify(docs)

if __name__ == '__main__':
    print(f"\n{'='*60}")
    print(f"ASI:One Agent API Server")
    print(f"{'='*60}")
    print(f"Flask client agent address: {client.address}")
    print(f"Target agent address: {AGENT_ADDRESS}")
    print(f"\nWeb Interface: http://localhost:8000")
    print(f"API Endpoint: http://localhost:8000/api/query")
    print(f"API Docs: http://localhost:8000/api/docs")
    print(f"{'='*60}\n")
    app.run(debug=True, port=8000, use_reloader=False, host='0.0.0.0')
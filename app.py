# app.py - Improved version
from flask import Flask, request, jsonify, render_template, Response, send_file, stream_template, redirect, url_for
from flask_cors import CORS
from webhooks.events import WebhookEventProcessor
from werkzeug.middleware.proxy_fix import ProxyFix
from webhooks.events import WebhookEventProcessor, EventType
from dotenv import load_dotenv
from crm_ui.routes import crm_bp
import jinja2
import os
import asyncio
import random
import logging
import time
import json
import sys
import traceback
from datetime import datetime, timedelta
from livekit import api as lkapi
from concurrent.futures import ThreadPoolExecutor
import threading
import queue
from database.connection import init_database, get_db, get_or_create_customer, create_call_log
from database.models import Customer, CallLog
from sqlalchemy.orm import Session
from database.migrations import initialize_database
initialize_database()

# Setup logging
logging.basicConfig(
    level=logging.INFO if os.getenv("NODE_ENV") == "production" else logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

app = Flask(__name__, 
    template_folder='templates',  # Main template folder
    static_folder='static'  # Main static folder
)

# Add additional template folder
app.jinja_loader = jinja2.ChoiceLoader([
    app.jinja_loader,
    jinja2.FileSystemLoader('crm_ui/templates')
])

CORS(app)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# Register CRM blueprint with its own static folder
app.register_blueprint(crm_bp, url_prefix='/crm', 
    static_folder='crm_ui/static',  # CRM UI static folder
    static_url_path='static'  # This will be prefixed with /crm to become /crm/static
)

# In-memory log storage for recent logs
from collections import deque
import threading

log_lock = threading.Lock()
recent_logs = deque(maxlen=100)

class InMemoryLogHandler(logging.Handler):
    def emit(self, record):
        log_entry = self.format(record)
        with log_lock:
            recent_logs.append(log_entry)

# Initialize webhook processor and event queue if not already present
webhook_processor = WebhookEventProcessor()
event_queue = queue.Queue()  # Make sure this is defined at module level

# Track application start time for uptime calculation
app._start_time = time.time()
app._total_events = 0
app._last_event_time = None

LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")
AGENT_NAME = os.getenv("AGENT_NAME")

executor = ThreadPoolExecutor(max_workers=4)

def run_async_in_thread(coro):
    def run_in_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()
    return executor.submit(run_in_thread).result()

@app.route('/')
def index():
    return render_template("index.html")

@app.route('/admin')
def admin_panel():
    return redirect(url_for("crm.dashboard"))


@app.route('/submit', methods=['POST'])
def submit():
    try:
        name = request.form.get('name', '').strip()
        phone = request.form.get('phone', '').strip()
        email = request.form.get('email', '').strip()
        query = request.form.get('query', '').strip()

        if not name or not phone:
            return jsonify({"success": False, "message": "Name and phone number are required."}), 400

        if not phone.replace('+', '').replace('-', '').replace(' ', '').replace('(', '').replace(')', '').isdigit():
            return jsonify({"success": False, "message": "Please provide a valid phone number."}), 400

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        random_suffix = ''.join(str(random.randint(0, 9)) for _ in range(6))
        room_name = f"outbound-{timestamp}-{random_suffix}"

        # Store customer data in database
        db = next(get_database())
        try:
            # Get or create customer
            customer = get_or_create_customer(db, name, phone, email)
            
            # Create call log entry
            call_data = {
                'name': name,
                'phone': phone,
                'email': email,
                'query': query,
                'room_name': room_name,
                'dispatch_id': None  # Will be updated after dispatch creation
            }
            call_log = create_call_log(db, call_data)
            
            metadata_dict = {
                "phone_number": phone,
                "name": name,
                "email": email if email else None,
                "query": query if query else None,
                "timestamp": timestamp,
                "call_id": call_log.call_id
            }
            metadata = json.dumps(metadata_dict, separators=(',', ':'))

            logger.info(f"Processing call request: {name} -> {phone}")
            logger.debug(f"Metadata: {metadata}")

            result = run_async_in_thread(create_dispatch(room_name, metadata))
            
            # Update call log with dispatch ID
            call_log.dispatch_id = result.get("dispatch_id")
            db.commit()

            logger.info(f"Dispatch created successfully: {result.get('dispatch_id')}")

            return jsonify({
                "success": True,
                "message": "Your call has been scheduled successfully! Our AI assistant will call you within the next few moments.",
                "details": {
                    "room_name": room_name,
                    "dispatch_id": result.get("dispatch_id"),
                    "call_id": call_log.call_id,
                    "estimated_wait_time": "1-2 minutes"
                }
            })

        except Exception as db_error:
            db.rollback()
            logger.error(f"Database error: {str(db_error)}")
            raise
        finally:
            db.close()

    except Exception as e:
        logger.error(f"Request processing error: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({
            "success": False,
            "message": "An unexpected error occurred. Please try again.",
            "error_details": str(e) if app.debug else None
        }), 500


@app.route('/health')
def health_check():
    return jsonify({
        "status": "ok",
        "livekit_configured": bool(LIVEKIT_URL and LIVEKIT_API_KEY and LIVEKIT_API_SECRET),
        "agent_name": AGENT_NAME
    })

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        raw_data = request.get_data(as_text=True)
        logger.info(f"Raw webhook data received: {raw_data}")
        
        data = request.json
        if not data:
            logger.error("No JSON data received in webhook")
            return jsonify({"error": "No JSON data"}), 400
            
        # Process with WebhookEventProcessor first
        success = webhook_processor.process_webhook(data, source="livekit")
        if not success:
            logger.error("Failed to process webhook with WebhookEventProcessor")
            return jsonify({"error": "Webhook processing failed"}), 400

        # Format data for UI updates
        formatted_data = format_webhook_data(data)
        
        # Add to event queue for real-time UI updates
        event_queue.put(formatted_data)
        
        logger.info(f"Webhook processed successfully: {formatted_data['status']}")
        return jsonify({"status": "success", "event": formatted_data['status']}), 200
        
    except Exception as e:
        logger.error(f"Webhook processing error: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({"error": "Failed to process webhook"}), 500

def format_webhook_data(data):
    """Format webhook data for UI consumption"""
    event_type = data.get('event_type') or data.get('type') or data.get('event')
    event_data = data.get('data', {})
    
    formatted_data = {
        'name': event_data.get('name', 'Unknown'),
        'phone_number': event_data.get('phone_number', ''),
        'email': event_data.get('email', ''),
        'query': event_data.get('query', ''),
        'timestamp': data.get('timestamp', datetime.utcnow().isoformat()),
        'status': event_type,
        'session_id': event_data.get('session_id', ''),
        'call_duration': event_data.get('call_duration_seconds', 0),
        'call_outcome': event_data.get('call_outcome', ''),
    }

    # Add event-specific data
    if event_type == "call_completed":
        formatted_data.update({
            'status': 'call_completed',
            'call_duration': event_data.get('duration_seconds', 0),
            'customer_satisfaction': event_data.get('customer_satisfaction', ''),
            'transcript': event_data.get('conversation_transcript', []),
            'detected_intents': event_data.get('detected_intents', [])
        })
    elif event_type == "call_failed":
        formatted_data.update({
            'status': 'call_failed',
            'error_type': event_data.get('error_type', ''),
            'error_message': event_data.get('error_message', ''),
            'sip_status_code': event_data.get('sip_status_code', '')
        })
    
    return formatted_data

@app.route('/events')
def events():
    """SSE endpoint for real-time updates"""
    def generate():
        try:
            while True:
                try:
                    # Get data from queue with timeout
                    data = event_queue.get(timeout=30)  # 30 second timeout
                    yield f"data: {json.dumps(data)}\n\n"
                except queue.Empty:
                    # Send keepalive
                    yield f"data: {json.dumps({'keepalive': True})}\n\n"
                except Exception as e:
                    logger.error(f"SSE error: {e}")
                    break
        except GeneratorExit:
            # Client disconnected
            pass
        except Exception as e:
            logger.error(f"SSE generation error: {e}")
            
    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'Access-Control-Allow-Origin': '*'
        }
    )

def process_webhook_event(formatted_data):
    """Process webhook events for additional actions"""
    try:
        event_type = formatted_data.get('status', '')
        
        # Update application stats
        app._total_events = getattr(app, '_total_events', 0) + 1
        app._last_event_time = datetime.now().isoformat()
        
        # Event-specific processing
        if event_type == 'call_failed':
            logger.warning(f"Call failed for {formatted_data.get('phone_number')}: {formatted_data.get('error_message')}")
            # Here you could trigger automated retry logic
            
        elif event_type == 'priority_interaction':
            logger.info(f"Priority interaction detected for {formatted_data.get('phone_number')}")
            # Here you could trigger real-time alerts to human agents
            
        elif event_type == 'call_completed':
            duration = formatted_data.get('call_duration', 0)
            outcome = formatted_data.get('call_outcome', 'unknown')
            satisfaction = formatted_data.get('customer_satisfaction', 'unknown')
            logger.info(f"Call completed: {duration}s, outcome: {outcome}, satisfaction: {satisfaction}")
            
        # Add to queue for real-time display
        event_queue.put(formatted_data)
        
    except Exception as e:
        logger.error(f"Event processing error: {e}")

@app.route('/api/call-analytics/<phone_number>')
def get_call_analytics(phone_number):
    """Get call analytics for a specific phone number"""
    try:
        # This would typically query your database
        # For now, returning mock data structure
        analytics = {
            "phone_number": phone_number,
            "total_calls": 0,
            "successful_calls": 0,
            "failed_calls": 0,
            "average_duration": 0,
            "last_call_date": None,
            "customer_satisfaction_trend": [],
            "common_intents": [],
            "call_outcomes": {}
        }
        
        return jsonify(analytics)
    except Exception as e:
        logger.error(f"Analytics error: {e}")
        return jsonify({"error": "Failed to fetch analytics"}), 500

@app.route('/api/retry-failed-call', methods=['POST'])
def retry_failed_call():
    """Retry a failed call"""
    try:
        data = request.json
        phone_number = data.get('phone_number')
        original_data = data.get('original_data', {})
        
        if not phone_number:
            return jsonify({"error": "Phone number required"}), 400
        
        # Use the original submit logic but mark as retry
        retry_data = {
            **original_data,
            "retry_attempt": True,
            "original_failure_reason": data.get('failure_reason', '')
        }
        
        logger.info(f"Retrying call for {phone_number}")
        
        # Process retry similar to original submit
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        random_suffix = ''.join(str(random.randint(0, 9)) for _ in range(6))
        room_name = f"retry-{timestamp}-{random_suffix}"
        
        metadata = json.dumps(retry_data, separators=(',', ':'))
        
        result = run_async_in_thread(create_dispatch(room_name, metadata))
        
        return jsonify({
            "success": True,
            "message": "Call retry initiated successfully",
            "dispatch_id": result.get("dispatch_id")
        })
        
    except Exception as e:
        logger.error(f"Retry call error: {e}")
        return jsonify({"error": "Failed to retry call"}), 500

@app.route('/api/webhook-stats')
def webhook_stats():
    """Get webhook statistics and health"""
    try:
        stats = {
            "queue_size": event_queue.qsize(),
            "webhook_url_configured": bool(os.getenv("WEBHOOK_URL")),
            "total_events_processed": getattr(app, '_total_events', 0),
            "last_event_time": getattr(app, '_last_event_time', None),
            "uptime": time.time() - getattr(app, '_start_time', time.time())
        }
        return jsonify(stats)
    except Exception as e:
        logger.error(f"Stats error: {e}")
        return jsonify({"error": "Failed to get stats"}), 500

@app.route('/test-webhook')
def test_webhook():
    test_data = {
        "name": "Test User",
        "phone_number": "+911234567890",
        "email": "test@example.com",
        "query": "Test Call",
        "timestamp": datetime.utcnow().isoformat(),
        "status": "call_started"
    }
    event_queue.put(test_data)
    return jsonify({"status": "Test event sent!"})


async def create_dispatch(room_name: str, metadata: str) -> dict:
    try:
        logger.debug("Initializing LiveKit API client")

        if not all([LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET, AGENT_NAME]):
            raise ValueError("Missing required LiveKit configuration")

        lk_client = lkapi.LiveKitAPI(
            url=LIVEKIT_URL,
            api_key=LIVEKIT_API_KEY,
            api_secret=LIVEKIT_API_SECRET,
        )

        dispatch_request = lkapi.CreateAgentDispatchRequest(
            agent_name=AGENT_NAME,
            room=room_name,
            metadata=metadata
        )

        logger.debug(f"Sending dispatch request for room: {room_name}")
        response = await lk_client.agent_dispatch.create_dispatch(dispatch_request)
        dispatch_id = getattr(response, 'dispatch_id', None) or getattr(response, 'id', 'unknown')

        logger.info(f"Dispatch created successfully: {dispatch_id}")

        return {
            "dispatch_id": dispatch_id,
            "room_name": room_name,
            "status": "created"
        }

    except Exception as e:
        logger.error(f"LiveKit dispatch creation failed: {str(e)}")
        raise Exception(f"Failed to create dispatch: {str(e)}")

@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Endpoint not found"}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({"error": "Internal server error"}), 500

@app.route('/debug/queue-status')
def queue_status():
    return jsonify({
        "queue_size": event_queue.qsize(),
        "timestamp": datetime.now().isoformat()
    })

@app.route('/debug/test-event')
def test_event():
    test_data = {
        "name": "Test Customer",
        "phone_number": "+1234567890",
        "email": "test@example.com",
        "query": "Test Query",
        "timestamp": datetime.now().isoformat(),
        "status": "call_started"
    }
    event_queue.put(test_data)
    return jsonify({"status": "Test event added to queue", "data": test_data})

@app.route('/debug/direct-queue-test')
def direct_queue_test():
    """Directly add data to queue - bypasses webhook entirely"""
    test_data = {
        'name': 'Direct Queue Test',
        'phone_number': '+919876543210',
        'email': 'direct@test.com',
        'query': 'Direct queue test',
        'timestamp': datetime.utcnow().isoformat(),
        'status': 'call_started'
    }
    
    logger.info(f"Direct queue test - adding: {json.dumps(test_data, indent=2)}")
    event_queue.put(test_data)
    
    return jsonify({
        "status": "success",
        "message": "Data added directly to queue",
        "data": test_data,
        "queue_size": event_queue.qsize()
    })

@app.route('/debug/webhook-test', methods=['POST'])
def webhook_test():
    """Manual webhook test that mimics the real webhook structure"""
    
    # Test with the exact structure that main.py sends
    test_payload = {
        "event": "call_started",
        "timestamp": datetime.now().isoformat(),
        "data": {
            "name": "Debug Test User",
            "phone_number": "+911234567890",
            "email": "debug@test.com",
            "query": "Debug test call",
            "timestamp": datetime.now().strftime("%Y%m%d_%H%M%S")
        }
    }
    
    logger.info(f"Manual webhook test - sending payload: {json.dumps(test_payload, indent=2)}")
    
    # Send it to the actual webhook endpoint
    try:
        import requests
        response = requests.post('http://localhost:8080/webhook', json=test_payload)
        
        return jsonify({
            "status": "success",
            "message": "Test webhook sent to /webhook endpoint",
            "payload_sent": test_payload,
            "webhook_response_status": response.status_code,
            "webhook_response": response.json() if response.headers.get('content-type') == 'application/json' else response.text
        })
        
    except Exception as e:
        logger.error(f"Failed to send test webhook: {e}")
        return jsonify({
            "status": "error",
            "message": f"Failed to send test webhook: {str(e)}"
        }), 500
@app.route('/debug/logs')
def show_logs():
    """Show recent log entries"""
    return jsonify({
        "webhook_url_configured": bool(os.getenv("WEBHOOK_URL")),
        "webhook_url": os.getenv("WEBHOOK_URL"),
        "queue_size": event_queue.qsize(),
        "flask_debug": app.debug,
        "timestamp": datetime.utcnow().isoformat()
    })

def send_ui_update(event_type: str, data: dict):
    """Send UI updates through SSE"""
    try:
        formatted_data = {
            'timestamp': datetime.utcnow().isoformat(),
            'event_type': event_type,
            **data
        }
        event_queue.put(formatted_data)
        logger.debug(f"UI update sent: {formatted_data}")
    except Exception as e:
        logger.error(f"Failed to send UI update: {e}")

# Add after app initialization but before routes
# Initialize database
# Initialize database
from database.connection import DatabaseManager
db_manager = DatabaseManager()
db_manager.initialize()

# Add this function to get database session
def get_database():
    """Get database session"""
    with db_manager.get_db_session() as db:
        yield db

# Admin routes moved to crm_ui blueprint

@app.route('/api/calls')
def api_calls():
    with db_manager.get_db_session() as db:
        calls = db.query(CallLog).order_by(CallLog.created_at.desc()).limit(100).all()
        return jsonify([{
            'id': call.id,
            'call_id': call.call_id,
            'room_name': call.room_name,
            'customer_name': call.customer_name,
            'customer_phone': call.customer_phone,
            'status': call.status,
            'created_at': call.created_at.isoformat(),
            'duration': call.duration_seconds,
            'dispatch_id': call.dispatch_id,
            'query': call.query,
            'email': call.email
        } for call in calls])

@app.route('/api/dashboard-stats')
def dashboard_stats():
    """Get dashboard statistics"""
    try:
        with db_manager.get_db_session() as db:
            stats = {
                'total_calls': db.query(CallLog).count(),
                'total_customers': db.query(Customer).count(),
                'active_calls': db.query(CallLog).filter(
                    CallLog.status.in_(['initiated', 'connected'])
                ).count(),
                'recent_calls_24h': db.query(CallLog).filter(
                    CallLog.created_at >= datetime.utcnow() - timedelta(hours=24)
                ).count()
            }
            return jsonify(stats)
    except Exception as e:
        logger.error(f"Error getting dashboard stats: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/metrics/summary')
def get_metrics_summary():
    """Get comprehensive metrics summary"""
    try:
        days = request.args.get('days', default=30, type=int)
        from monitoring.metrics import get_metrics_summary
        summary = get_metrics_summary(days)
        return jsonify(summary)
    except Exception as e:
        logger.error(f"Error getting metrics summary: {e}")
        return jsonify({
            "error": "Failed to get metrics summary",
            "details": str(e) if app.debug else None
        }), 500

if __name__ == '__main__':
    missing_vars = []
    required_vars = ["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "AGENT_NAME"]

    for var in required_vars:
        if not os.getenv(var):
            missing_vars.append(var)

    if missing_vars:
        logger.error(f"Missing required environment variables: {', '.join(missing_vars)}")
        sys.exit(1)

    port = int(os.environ.get("PORT", 8080))

    logger.info(f"Starting AI Voice Assistant API")
    logger.info(f"LiveKit URL: {LIVEKIT_URL}")
    logger.info(f"Agent Name: {AGENT_NAME}")
    logger.info(f"Port: {port}")

    app.run(host='0.0.0.0', port=port, debug=os.getenv("FLASK_DEBUG", "false").lower() == "true")

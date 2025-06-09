# main.py - Fixed version with webhook debugging
from __future__ import annotations
from dotenv import load_dotenv
from livekit import agents
from livekit.agents import AgentSession, Agent, RoomInputOptions, RunContext
from livekit.plugins import openai, elevenlabs, deepgram, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from prompts import INSTRUCTIONS, get_time_based_greeting
from livekit.agents import llm
from livekit import api, rtc
import json
import os
import asyncio
import aiohttp
from datetime import datetime
import logging
from typing import Any, Optional
import traceback

# Load environment variables
load_dotenv()

# Setup logging with more detailed configuration
logging.basicConfig(
    level=logging.DEBUG,  # Changed to DEBUG for more verbose logging
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),  # Ensure console output
        logging.FileHandler('assistant.log')  # Also log to file
    ]
)
logger = logging.getLogger("call_assistant")

# Force logger level to ensure it's not overridden
logger.setLevel(logging.DEBUG)

async def send_webhook(event_type: str, data: dict, additional_data: dict = None):
    """Send webhook with enhanced debugging and error handling"""
    logger.info(f"🔥 WEBHOOK FUNCTION CALLED - Event: {event_type}")
    
    webhook_url = os.getenv("WEBHOOK_URL")
    if not webhook_url:
        logger.error("====> CRITICAL: No WEBHOOK_URL found in environment variables!")
        logger.error(f"Available env vars: {list(os.environ.keys())}")
        return False
    
    logger.info(f"==>  Webhook URL found: {webhook_url}")
    
    # Merge additional data if provided
    if additional_data:
        data = {**data, **additional_data}
        logger.info(f" Additional data merged")
    
    payload = {
        "event": event_type,
        "timestamp": datetime.now().isoformat(),
        "data": data,
        "room_id": data.get("room_id", ""),
        "session_id": data.get("session_id", "")
    }
    
    logger.info(f"=== WEBHOOK DEBUG START ===")
    logger.info(f"Event type: {event_type}")
    logger.info(f"Webhook URL: {webhook_url}")
    logger.info(f"Full payload: {json.dumps(payload, indent=2)}")
    logger.info(f"Payload size: {len(json.dumps(payload))} bytes")
    logger.info(f"=== WEBHOOK DEBUG END ===")
    
    max_retries = 3
    for attempt in range(max_retries):
        logger.info(f"===>>>> Webhook attempt {attempt + 1}/{max_retries}")
        
        try:
            # Test network connectivity first
            logger.info("===>.../// Testing network connectivity...")
            
            timeout = aiohttp.ClientTimeout(total=30)  # Increased timeout
            async with aiohttp.ClientSession(timeout=timeout) as session:
                logger.info("📡 Sending webhook request...")
                
                async with session.post(
                    webhook_url, 
                    json=payload,
                    headers={
                        'Content-Type': 'application/json',
                        'User-Agent': 'LiveKit-Assistant/1.0'
                    }
                ) as response:
                    logger.info(f"📨 Response received - Status: {response.status}")
                    
                    response_text = await response.text()
                    logger.info(f"📄 Response body: {response_text[:500]}...")  # Log first 500 chars
                    
                    if response.status == 200:
                        logger.info(f"✅ Webhook sent successfully: {event_type}")
                        return True
                    else:
                        logger.warning(f"❌ Webhook failed: {response.status} - {response_text}")
                        
        except aiohttp.ClientError as e:
            logger.error(f"🌐 Network error on attempt {attempt + 1}: {str(e)}")
            logger.error(f"Error type: {type(e).__name__}")
        except asyncio.TimeoutError as e:
            logger.error(f"⏰ Timeout error on attempt {attempt + 1}: {str(e)}")
        except Exception as e:
            logger.error(f"❌ Unexpected error on attempt {attempt + 1}: {str(e)}")
            logger.error(f"Error type: {type(e).__name__}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            
        if attempt < max_retries - 1:
            wait_time = 2 ** attempt
            logger.info(f"⏳ Waiting {wait_time} seconds before retry...")
            await asyncio.sleep(wait_time)
        else:
            logger.error(f"💀 All webhook attempts failed for {event_type}")
    
    return False


class Assistant(Agent):
    def __init__(self, *, name: str, dial_info: dict[str, Any] = None):
        super().__init__(instructions=INSTRUCTIONS)
        self.name = name
        self.dial_info = dial_info or {}
        self.participant: Optional[rtc.RemoteParticipant] = None
        self.session_started = False
        self.conversation_transcript = []
        self.call_start_time = None
        self.user_intents = []
        logger.info(f"🤖 Assistant '{name}' initialized")

    def create_greeting(self) -> str:
        try:
            customer_name = self.dial_info.get("name", "there")
            customer_query = self.dial_info.get("query", "")
            time_greeting = get_time_based_greeting()
            if customer_query:
                greeting = (
                    f"Hey {customer_name}, {time_greeting.lower()}! "
                    f"You're speaking with FRAN-TIGER — your friendly AI assistant. "
                    f"I understand you have a question about {customer_query}. "
                    f"I'm here to help! How can I assist you with that?"
                )
            else:
                greeting = (
                    f"Hey {customer_name}, {time_greeting}! "
                    f"I'm FRAN-TIGER, your AI assistant. "
                    f"What can I help you with today?"
                )
            logger.debug(f"Generated greeting: {greeting}")
            return greeting
        except Exception as e:
            logger.error(f"Error creating greeting: {e}")
            return "Hello! I'm FRAN-TIGER, your AI assistant. How can I help you today?"

    async def on_session_started(self, ctx: RunContext) -> None:
        logger.info("🚀 Session started - preparing welcome message")
        self.session_started = True
        self.call_start_time = datetime.now()
        
        # Enhanced webhook data with room info
        session_data = {
            **self.dial_info,
            "session_id": ctx.room.name,
            "room_id": ctx.room.name,  # Ensure room_id is set
            "agent_name": self.name,
            "call_start_time": self.call_start_time.isoformat()
        }
        
        logger.info(f"📞 Sending call_started webhook with data: {session_data}")
        
        try:
            # Use await to ensure the webhook is sent before continuing
            result = await send_webhook("call_started", session_data)
            if result:
                logger.info("✅ call_started webhook sent successfully")
            else:
                logger.error("❌ call_started webhook failed")
        except Exception as e:
            logger.error(f"💥 Exception sending call_started webhook: {e}")
            logger.error(traceback.format_exc())

    async def on_session_ended(self, ctx: RunContext):
        logger.info("🔚 Session ended - preparing completion data")
        call_end_time = datetime.now()
        call_duration = (call_end_time - self.call_start_time).total_seconds() if self.call_start_time else 0
        
        # Comprehensive end-of-call data
        completion_data = {
            **self.dial_info,
            "session_id": ctx.room.name,
            "room_id": ctx.room.name,  # Ensure room_id is set
            "call_end_time": call_end_time.isoformat(),
            "call_duration_seconds": call_duration,
            "conversation_transcript": self.conversation_transcript,
            "detected_intents": self.user_intents,
            "call_outcome": self._analyze_call_outcome(),
            "customer_satisfaction": self._estimate_satisfaction()
        }

        logger.info(f"📊 Sending call_completed webhook with data size: {len(str(completion_data))} chars")

        try:
            result = await send_webhook("call_completed", completion_data)
            if result:
                logger.info("✅ call_completed webhook sent successfully")
            else:
                logger.error("❌ call_completed webhook failed")
        except Exception as e:
            logger.error(f"💥 Exception sending call_completed webhook: {e}")
            logger.error(traceback.format_exc())

    async def capture_user_interaction(self, interaction_type: str, content: str, intent: str = None):
        """Capture and send user interaction events"""
        logger.info(f"👤 Capturing user interaction: {interaction_type} - {intent}")
        
        interaction_data = {
            **self.dial_info,
            "interaction_type": interaction_type,
            "content": content,
            "intent": intent,
            "timestamp": datetime.now().isoformat(),
            "call_duration": (datetime.now() - self.call_start_time).total_seconds() if self.call_start_time else 0
        }
        
        self.user_intents.append(interaction_data)
        
        try:
            await send_webhook("user_interaction_detected", interaction_data)
            
            # Real-time intent analysis
            if intent and intent.lower() in ['urgent', 'complaint', 'technical_support']:
                priority_data = {
                    **interaction_data,
                    "priority_level": "high",
                    "suggested_action": "route_to_human"
                }
                await send_webhook("priority_interaction", priority_data)
        except Exception as e:
            logger.error(f"Error capturing user interaction: {e}")

    def _analyze_call_outcome(self) -> str:
        """Analyze call outcome based on conversation"""
        if not self.conversation_transcript:
            return "no_conversation"
        
        # Simple outcome analysis - you can enhance this with NLP
        total_messages = len(self.conversation_transcript)
        if total_messages < 3:
            return "early_disconnect"
        elif any("thank" in msg.get("content", "").lower() for msg in self.conversation_transcript):
            return "successful_resolution"
        elif any(word in " ".join([msg.get("content", "") for msg in self.conversation_transcript]).lower() 
                for word in ["problem", "issue", "help", "support"]):
            return "support_needed"
        else:
            return "information_provided"

    def _estimate_satisfaction(self) -> str:
        """Estimate customer satisfaction based on conversation patterns"""
        if not self.conversation_transcript:
            return "unknown"
        
        # Simple satisfaction estimation
        positive_words = ["thank", "great", "good", "helpful", "perfect"]
        negative_words = ["bad", "terrible", "awful", "useless", "frustrated"]
        
        conversation_text = " ".join([msg.get("content", "") for msg in self.conversation_transcript]).lower()
        
        positive_count = sum(1 for word in positive_words if word in conversation_text)
        negative_count = sum(1 for word in negative_words if word in conversation_text)
        
        if positive_count > negative_count and positive_count > 0:
            return "satisfied"
        elif negative_count > positive_count and negative_count > 0:
            return "dissatisfied"
        else:
            return "neutral"

    async def on_user_speech_committed(self, ctx: RunContext, message: llm.ChatMessage) -> None:
        """Track user messages and analyze intents"""
        logger.info(f"🗣️ User speech committed: {message.content[:100]}...")
        
        user_message = {
            "role": "user",
            "content": message.content,
            "timestamp": datetime.now().isoformat()
        }
        
        self.conversation_transcript.append(user_message)
        
        # Simple intent detection - enhance with proper NLP
        content_lower = message.content.lower()
        detected_intent = None
        
        if any(word in content_lower for word in ['urgent', 'emergency', 'asap']):
            detected_intent = 'urgent'
        elif any(word in content_lower for word in ['complaint', 'problem', 'issue']):
            detected_intent = 'complaint'
        elif any(word in content_lower for word in ['technical', 'support', 'help']):
            detected_intent = 'technical_support'
        elif any(word in content_lower for word in ['price', 'cost', 'billing']):
            detected_intent = 'billing_inquiry'
        
        if detected_intent:
            logger.info(f"🎯 Intent detected: {detected_intent}")
            await self.capture_user_interaction("speech", message.content, detected_intent)

    async def on_agent_speech_committed(self, ctx: RunContext, message: llm.ChatMessage) -> None:
        """Track agent responses"""
        logger.info(f"🤖 Agent speech committed: {message.content[:100]}...")
        
        agent_message = {
            "role": "assistant",
            "content": message.content,
            "timestamp": datetime.now().isoformat()
        }
        
        self.conversation_transcript.append(agent_message)

def validate_environment() -> bool:
    logger.info("🔍 Validating environment variables...")
    
    required_vars = [
        "DEEPGRAM_API_KEY",
        "MODEL_NAME",
        "SIP_OUTBOUND_TRUNK_ID",
        "MY_PHONE_NUMBER"
    ]
    
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    # Check webhook URL separately
    webhook_url = os.getenv("WEBHOOK_URL")
    if not webhook_url:
        logger.warning("⚠️ WEBHOOK_URL not set - webhooks will not be sent")
    else:
        logger.info(f"✅ WEBHOOK_URL configured: {webhook_url}")
    
    if missing_vars:
        logger.error(f"❌ Missing required environment variables: {', '.join(missing_vars)}")
        return False
    
    logger.info("✅ All required environment variables present")
    return True

async def create_agent_session() -> AgentSession:
    try:
        logger.info("🔧 Creating agent session...")
        session = AgentSession(
            stt=deepgram.STT(
                api_key=os.getenv("DEEPGRAM_API_KEY"), 
                model="nova-2"
            ),
            llm=openai.LLM(
                model=os.getenv("MODEL_NAME"),
                temperature=0.7,
            ),
            tts=openai.TTS(
                model="tts-1", 
                voice="nova"
            ),
            vad=silero.VAD.load(),
            turn_detection=MultilingualModel()
        )
        logger.info("✅ Agent session created successfully")
        return session
    except Exception as e:
        logger.error(f"❌ Failed to create agent session: {e}")
        logger.error(traceback.format_exc())
        raise

async def handle_sip_call(ctx: agents.JobContext, dial_info: dict) -> rtc.RemoteParticipant:
    try:
        phone_number = dial_info["phone_number"]
        participant_identity = dial_info.get("name", "customer").replace(" ", "_").lower()
        logger.info(f"📞 Initiating call to {phone_number} with identity {participant_identity}")
        
        await ctx.api.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                room_name=ctx.room.name,
                sip_trunk_id=os.getenv("SIP_OUTBOUND_TRUNK_ID"),
                sip_call_to=phone_number,
                participant_identity=participant_identity,
                krisp_enabled=True
            )
        )
        
        logger.info("⏳ Waiting for participant to join...")
        participant = await ctx.wait_for_participant(identity=participant_identity)
        logger.info(f"✅ Participant {participant.identity} joined successfully")
        return participant
        
    except api.TwirpError as e:
        error_data = {
            **dial_info,
            "error_type": "sip_error",
            "error_message": e.message,
            "sip_status_code": e.metadata.get('sip_status_code'),
            "sip_status": e.metadata.get('sip_status'),
            "room_id": ctx.room.name
        }
        
        logger.error(f"📞 SIP call failed: {e.message}")
        try:
            await send_webhook("call_failed", error_data)
        except Exception as webhook_error:
            logger.error(f"Failed to send call_failed webhook: {webhook_error}")
        
        raise Exception(f"Call failed: {e.message}")
        
    except Exception as e:
        error_data = {
            **dial_info,
            "error_type": "unexpected_error",
            "error_message": str(e),
            "room_id": ctx.room.name,
            "traceback": traceback.format_exc()
        }
        
        logger.error(f"💥 Unexpected error in SIP call: {e}")
        logger.error(traceback.format_exc())
        
        try:
            await send_webhook("call_failed", error_data)
        except Exception as webhook_error:
            logger.error(f"Failed to send call_failed webhook: {webhook_error}")
        
        raise

async def entrypoint(ctx: agents.JobContext):
    logger.info(f"🚀 Agent starting for job: {ctx.job.id}")
    
    try:
        if not validate_environment():
            logger.error("❌ Environment validation failed")
            ctx.shutdown()
            return

        await ctx.connect()
        logger.info(f"🔗 Connected to room: {ctx.room.name}")

        dial_info = {}
        try:
            if ctx.job.metadata:
                dial_info = json.loads(ctx.job.metadata)
                logger.info(f"📋 Parsed dial info: {dial_info}")
            else:
                dial_info = {"phone_number": os.getenv("MY_PHONE_NUMBER")}
                logger.warning("⚠️ No metadata provided, using default phone number")
        except json.JSONDecodeError as e:
            logger.error(f"❌ Failed to parse metadata: {e}")
            dial_info = {"phone_number": os.getenv("MY_PHONE_NUMBER")}

        agent = Assistant(
            name="SheraAI_Assistant",
            dial_info=dial_info
        )
        
        session = await create_agent_session()
        participant = await handle_sip_call(ctx, dial_info)
        
        # Set participant reference
        agent.participant = participant
        
        logger.info("🤖 Starting agent session...")
        await session.start(
            room=ctx.room,
            agent=agent,
            room_input_options=RoomInputOptions(),
        )
        
        await asyncio.sleep(1)
        
        try:
            greeting = agent.create_greeting()
            logger.info("👋 Sending initial greeting...")
            await session.say(greeting, allow_interruptions=True)
            logger.info("✅ Initial greeting sent successfully")
        except Exception as e:
            logger.error(f"❌ Failed to send greeting: {e}")
            logger.error(traceback.format_exc())
            
        logger.info("🏃 Agent session running successfully")
        
    except Exception as e:
        logger.error(f"💀 Fatal error in entrypoint: {e}")
        logger.error(traceback.format_exc())
        ctx.shutdown()
        return

if __name__ == "__main__":
    # Set log level based on environment
    log_level = logging.DEBUG if os.getenv("NODE_ENV") != "production" else logging.INFO
    logging.basicConfig(level=log_level)
    
    logger.info("Starting SheraAI Assistant Agent")
    
    # Test webhook URL at startup
    webhook_url = os.getenv("WEBHOOK_URL")
    if webhook_url:
        logger.info(f" Webhook URL configured: {webhook_url}")
    else:
        logger.warning("No webhook URL configured - set WEBHOOK_URL environment variable")
    
    agents.cli.run_app(
        agents.WorkerOptions(
            entrypoint_fnc=entrypoint, 
            agent_name="SheraAI_Assistant"
        )
    )
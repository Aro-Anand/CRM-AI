# webhooks/handlers.py
import asyncio
import aiohttp
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from database.connection import db_manager
from database.models import WebhookLog, CallLog
import os

logger = logging.getLogger(__name__)

class WebhookManager:
    def __init__(self):
        self.webhook_urls = self._load_webhook_urls()
        self.max_retries = int(os.getenv("WEBHOOK_MAX_RETRIES", "3"))
        self.retry_delay = int(os.getenv("WEBHOOK_RETRY_DELAY", "60"))  # seconds
        
    def _load_webhook_urls(self) -> List[str]:
        """Load webhook URLs from environment variables"""
        urls = []
        
        # Primary webhook URL
        primary_url = os.getenv("WEBHOOK_URL")
        if primary_url:
            urls.append(primary_url)
        
        # Additional webhook URLs (comma-separated)
        additional_urls = os.getenv("WEBHOOK_URLS", "").strip()
        if additional_urls:
            urls.extend([url.strip() for url in additional_urls.split(",") if url.strip()])
        
        logger.info(f"Loaded {len(urls)} webhook URLs")
        return urls
    
    async def send_webhook(self, event_type: str, call_id: str, payload: Dict):
        """Send webhook to all configured URLs"""
        if not self.webhook_urls:
            logger.info("No webhook URLs configured")
            return
        
        tasks = []
        for url in self.webhook_urls:
            task = asyncio.create_task(
                self._send_webhook_to_url(url, event_type, call_id, payload)
            )
            tasks.append(task)
        
        # Wait for all webhooks to complete
        await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _send_webhook_to_url(self, url: str, event_type: str, call_id: str, payload: Dict):
        """Send webhook to a specific URL"""
        webhook_payload = {
            "event_type": event_type,
            "call_id": call_id,
            "timestamp": datetime.utcnow().isoformat(),
            "data": payload
        }
        
        # Store webhook attempt in database
        with db_manager.get_db_session() as db:
            webhook_log = WebhookLog(
                call_id=call_id,
                event_type=event_type,
                payload=webhook_payload,
                max_retries=self.max_retries
            )
            db.add(webhook_log)
            db.commit()
            webhook_id = webhook_log.webhook_id
        
        # Send the webhook
        success = await self._attempt_webhook_delivery(url, webhook_payload, webhook_id)
        
        if not success:
            # Schedule retry
            await self._schedule_webhook_retry(webhook_id)
    
    async def _attempt_webhook_delivery(self, url: str, payload: Dict, webhook_id: str) -> bool:
        """Attempt to deliver webhook"""
        try:
            timeout = aiohttp.ClientTimeout(total=30)  # 30 second timeout
            
            async with aiohttp.ClientSession(timeout=timeout) as session:
                headers = {
                    'Content-Type': 'application/json',
                    'User-Agent': 'AI-Assistant-Webhook/1.0',
                    'X-Webhook-ID': webhook_id
                }
                
                # Add webhook signature if secret is configured
                webhook_secret = os.getenv("WEBHOOK_SECRET")
                if webhook_secret:
                    import hmac
                    import hashlib
                    
                    signature = hmac.new(
                        webhook_secret.encode('utf-8'),
                        json.dumps(payload, separators=(',', ':')).encode('utf-8'),
                        hashlib.sha256
                    ).hexdigest()
                    headers['X-Webhook-Signature'] = f"sha256={signature}"
                
                async with session.post(url, json=payload, headers=headers) as response:
                    response_text = await response.text()
                    
                    # Update webhook log
                    with db_manager.get_db_session() as db:
                        webhook_log = db.query(WebhookLog).filter(
                            WebhookLog.webhook_id == webhook_id
                        ).first()
                        
                        if webhook_log:
                            webhook_log.status_code = response.status
                            webhook_log.response_body = response_text[:1000]  # Limit response size
                            
                            if 200 <= response.status < 300:
                                webhook_log.delivered = True
                                logger.info(f"Webhook delivered successfully: {webhook_id} -> {url}")
                                return True
                            else:
                                logger.warning(f"Webhook failed with status {response.status}: {webhook_id} -> {url}")
                        
                        db.commit()
        
        except asyncio.TimeoutError:
            logger.error(f"Webhook timeout: {webhook_id} -> {url}")
        except Exception as e:
            logger.error(f"Webhook delivery error: {webhook_id} -> {url} - {e}")
            
            # Update webhook log with error
            try:
                with db_manager.get_db_session() as db:
                    webhook_log = db.query(WebhookLog).filter(
                        WebhookLog.webhook_id == webhook_id
                    ).first()
                    
                    if webhook_log:
                        webhook_log.response_body = str(e)[:1000]
                        db.commit()
            except:
                pass
        
        return False
    
    async def _schedule_webhook_retry(self, webhook_id: str):
        """Schedule webhook for retry"""
        try:
            with db_manager.get_db_session() as db:
                webhook_log = db.query(WebhookLog).filter(
                    WebhookLog.webhook_id == webhook_id
                ).first()
                
                if webhook_log and webhook_log.retry_count < webhook_log.max_retries:
                    webhook_log.retry_count += 1
                    webhook_log.next_retry_at = datetime.utcnow() + timedelta(
                        seconds=self.retry_delay * (2 ** webhook_log.retry_count)  # Exponential backoff
                    )
                    db.commit()
                    
                    logger.info(f"Scheduled webhook retry {webhook_log.retry_count}/{webhook_log.max_retries}: {webhook_id}")
                
        except Exception as e:
            logger.error(f"Error scheduling webhook retry: {webhook_id} - {e}")
    
    async def process_webhook_retries(self):
        """Process pending webhook retries (call this periodically)"""
        try:
            with db_manager.get_db_session() as db:
                # Get webhooks that need retry
                pending_webhooks = db.query(WebhookLog).filter(
                    WebhookLog.delivered == False,
                    WebhookLog.retry_count < WebhookLog.max_retries,
                    WebhookLog.next_retry_at <= datetime.utcnow()
                ).all()
                
                logger.info(f"Processing {len(pending_webhooks)} webhook retries")
                
                for webhook_log in pending_webhooks:
                    # Reset next_retry_at to prevent multiple retries
                    webhook_log.next_retry_at = None
                    db.commit()
                    
                    # Attempt delivery again
                    for url in self.webhook_urls:
                        success = await self._attempt_webhook_delivery(
                            url, webhook_log.payload, webhook_log.webhook_id
                        )
                        if success:
                            break
                    
                    if not success:
                        await self._schedule_webhook_retry(webhook_log.webhook_id)
        
        except Exception as e:
            logger.error(f"Error processing webhook retries: {e}")

# Global webhook manager instance
webhook_manager = WebhookManager()

# Event-specific webhook functions
async def send_call_initiated_webhook(call_id: str, call_data: Dict):
    """Send webhook when call is initiated"""
    payload = {
        "customer_name": call_data.get("name"),
        "customer_phone": call_data.get("phone"),
        "customer_email": call_data.get("email"),
        "customer_query": call_data.get("query"),
        "room_name": call_data.get("room_name"),
        "dispatch_id": call_data.get("dispatch_id")
    }
    
    await webhook_manager.send_webhook("call_initiated", call_id, payload)

async def send_call_connected_webhook(call_id: str, participant_data: Dict):
    """Send webhook when call is connected"""
    payload = {
        "participant_identity": participant_data.get("identity"),
        "connected_at": datetime.utcnow().isoformat(),
        "sip_status": participant_data.get("sip_status")
    }
    
    await webhook_manager.send_webhook("call_connected", call_id, payload)

async def send_call_ended_webhook(call_id: str, call_summary: Dict):
    """Send webhook when call ends"""
    payload = {
        "ended_at": datetime.utcnow().isoformat(),
        "duration_seconds": call_summary.get("duration_seconds"),
        "transcript": call_summary.get("transcript"),
        "recording_url": call_summary.get("recording_url"),
        "end_reason": call_summary.get("end_reason", "completed")
    }
    
    await webhook_manager.send_webhook("call_ended", call_id, payload)

async def send_call_failed_webhook(call_id: str, error_data: Dict):
    """Send webhook when call fails"""
    payload = {
        "failed_at": datetime.utcnow().isoformat(),
        "error_message": error_data.get("error_message"),
        "sip_status_code": error_data.get("sip_status_code"),
        "sip_status_message": error_data.get("sip_status_message")
    }
    
    await webhook_manager.send_webhook("call_failed", call_id, payload)

async def send_transcript_ready_webhook(call_id: str, transcript_data: Dict):
    """Send webhook when transcript is ready"""
    payload = {
        "transcript": transcript_data.get("transcript"),
        "transcript_url": transcript_data.get("transcript_url"),
        "language": transcript_data.get("language", "en"),
        "confidence_score": transcript_data.get("confidence_score")
    }
    
    await webhook_manager.send_webhook("transcript_ready", call_id, payload)
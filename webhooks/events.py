# webhooks/events.py
import logging
import json
from datetime import datetime
from typing import Dict, Any, Optional, List
from enum import Enum
from dataclasses import dataclass
from database.connection import db_manager
from database.models import CallLog, CallEvent, WebhookLog, Customer
from monitoring.metrics import metrics_collector
from validation.phone_validator import validate_phone_number

logger = logging.getLogger(__name__)

class EventType(Enum):
    """Supported webhook event types"""
    CALL_INITIATED = "call.initiated"
    CALL_CONNECTED = "call.connected" 
    CALL_ANSWERED = "call.answered"
    CALL_COMPLETED = "call.completed"
    CALL_FAILED = "call.failed"
    CALL_CANCELLED = "call.cancelled"
    CALL_NO_ANSWER = "call.no_answer"
    CALL_BUSY = "call.busy"
    RECORDING_READY = "recording.ready"
    TRANSCRIPT_READY = "transcript.ready"
    PARTICIPANT_JOINED = "participant.joined"
    PARTICIPANT_LEFT = "participant.left"
    ERROR_OCCURRED = "error.occurred"

@dataclass
class WebhookEvent:
    """Structured webhook event data"""
    event_type: str
    call_id: str
    timestamp: datetime
    data: Dict[str, Any]
    source: str = "unknown"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'event_type': self.event_type,
            'call_id': self.call_id,
            'timestamp': self.timestamp.isoformat(),
            'data': self.data,
            'source': self.source
        }

class WebhookEventProcessor:
    """Processes incoming webhook events and updates database"""
    
    def __init__(self):
        self.event_handlers = {
            EventType.CALL_INITIATED.value: self._handle_call_initiated,
            EventType.CALL_CONNECTED.value: self._handle_call_connected,
            EventType.CALL_ANSWERED.value: self._handle_call_answered,
            EventType.CALL_COMPLETED.value: self._handle_call_completed,
            EventType.CALL_FAILED.value: self._handle_call_failed,
            EventType.CALL_CANCELLED.value: self._handle_call_cancelled,
            EventType.CALL_NO_ANSWER.value: self._handle_call_no_answer,
            EventType.CALL_BUSY.value: self._handle_call_busy,
            EventType.RECORDING_READY.value: self._handle_recording_ready,
            EventType.TRANSCRIPT_READY.value: self._handle_transcript_ready,
            EventType.PARTICIPANT_JOINED.value: self._handle_participant_joined,
            EventType.PARTICIPANT_LEFT.value: self._handle_participant_left,
            EventType.ERROR_OCCURRED.value: self._handle_error_occurred,
        }
    
    def process_webhook(self, payload: Dict[str, Any], source: str = "webhook") -> bool:
        """Process incoming webhook payload"""
        try:
            # Log the webhook
            webhook_log_id = self._log_webhook(payload, source)
            
            # Parse the event
            event = self._parse_webhook_event(payload, source)
            if not event:
                logger.error(f"Failed to parse webhook event: {payload}")
                return False
            
            # Process the event
            success = self._process_event(event)
            
            # Update webhook log with result
            self._update_webhook_log(webhook_log_id, success)
            
            return success
            
        except Exception as e:
            logger.error(f"Error processing webhook: {e}")
            return False
    
    def _parse_webhook_event(self, payload: Dict[str, Any], source: str) -> Optional[WebhookEvent]:
        """Parse webhook payload into structured event"""
        try:
            # Handle different webhook formats
            if 'event_type' in payload:
                # Direct format
                event_type = payload['event_type']
                call_id = payload.get('call_id')
                data = payload.get('data', {})
                timestamp_str = payload.get('timestamp')
            elif 'type' in payload:
                # Alternative format
                event_type = payload['type']
                call_id = payload.get('callId') or payload.get('call_id')
                data = payload.get('payload', payload)
                timestamp_str = payload.get('createdAt') or payload.get('timestamp')
            else:
                logger.error(f"Unknown webhook format: {payload}")
                return None
            
            if not call_id:
                logger.error(f"Missing call_id in webhook: {payload}")
                return None
            
            # Parse timestamp
            if timestamp_str:
                try:
                    if isinstance(timestamp_str, str):
                        # Try different timestamp formats
                        for fmt in ['%Y-%m-%dT%H:%M:%S.%fZ', '%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%d %H:%M:%S']:
                            try:
                                timestamp = datetime.strptime(timestamp_str, fmt)
                                break
                            except ValueError:
                                continue
                        else:
                            timestamp = datetime.utcnow()
                    else:
                        timestamp = datetime.utcnow()
                except:
                    timestamp = datetime.utcnow()
            else:
                timestamp = datetime.utcnow()
            
            return WebhookEvent(
                event_type=event_type,
                call_id=call_id,
                timestamp=timestamp,
                data=data,
                source=source
            )
            
        except Exception as e:
            logger.error(f"Error parsing webhook event: {e}")
            return None
    
    def _process_event(self, event: WebhookEvent) -> bool:
        """Process a structured webhook event"""
        try:
            handler = self.event_handlers.get(event.event_type)
            if not handler:
                logger.warning(f"No handler for event type: {event.event_type}")
                return self._handle_unknown_event(event)
            
            return handler(event)
            
        except Exception as e:
            logger.error(f"Error processing event {event.event_type}: {e}")
            return False
    
    def _handle_call_initiated(self, event: WebhookEvent) -> bool:
        """Handle call initiated event"""
        try:
            with db_manager.get_db_session() as db:
                # Extract call data
                data = event.data
                customer_phone = data.get('customer_phone') or data.get('to')
                customer_name = data.get('customer_name')
                customer_email = data.get('customer_email')
                customer_query = data.get('customer_query') or data.get('query')
                
                # Validate phone number
                if customer_phone:
                    validated_phone = validate_phone_number(customer_phone)
                    if validated_phone:
                        customer_phone = validated_phone
                
                # Create or update call log
                call_log = db.query(CallLog).filter(CallLog.call_id == event.call_id).first()
                if not call_log:
                    call_log = CallLog(
                        call_id=event.call_id,
                        room_name=data.get('room_name'),
                        dispatch_id=data.get('dispatch_id'),
                        customer_name=customer_name,
                        customer_phone=customer_phone,
                        customer_email=customer_email,
                        customer_query=customer_query,
                        status='initiated',
                        call_started_at=event.timestamp
                    )
                    db.add(call_log)
                else:
                    call_log.status = 'initiated'
                    call_log.call_started_at = event.timestamp
                    call_log.updated_at = datetime.utcnow()
                
                # Create call event
                call_event = CallEvent(
                    call_log_id=call_log.id,
                    event_type=event.event_type,
                    event_data=event.data
                )
                db.add(call_event)
                
                # Create or update customer record
                if customer_phone:
                    self._upsert_customer(db, customer_name, customer_phone, customer_email)
                
                db.commit()
                logger.info(f"Call initiated: {event.call_id}")
                return True
                
        except Exception as e:
            logger.error(f"Error handling call initiated: {e}")
            return False
    
    def _handle_call_connected(self, event: WebhookEvent) -> bool:
        """Handle call connected event"""
        return self._update_call_status(event, 'connected')
    
    def _handle_call_answered(self, event: WebhookEvent) -> bool:
        """Handle call answered event"""
        return self._update_call_status(event, 'answered')

def _handle_call_completed(self, event: WebhookEvent) -> bool:
    """Handle call completed event"""
    try:
        with db_manager.get_db_session() as db:
            call_log = db.query(CallLog).filter(CallLog.call_id == event.call_id).first()
            if not call_log:
                logger.warning(f"Call log not found for completed event: {event.call_id}")
                return False

            call_log.status = 'completed'
            call_log.call_ended_at = event.timestamp
            call_log.updated_at = datetime.utcnow()
            call_log.duration = (
                (event.timestamp - call_log.call_started_at).total_seconds()
                if call_log.call_started_at else None
            )

            call_event = CallEvent(
                call_log_id=call_log.id,
                event_type=event.event_type,
                event_data=event.data
            )
            db.add(call_event)
            db.commit()

            metrics_collector.record_call_duration(call_log.duration)
            return True
    except Exception as e:
        logger.error(f"Error handling call completed: {e}")
        return False

def _handle_call_failed(self, event: WebhookEvent) -> bool:
    """Handle call failed event"""
    return self._update_call_status(event, 'failed', error_details=event.data.get('error'))

def _handle_call_cancelled(self, event: WebhookEvent) -> bool:
    """Handle call cancelled event"""
    return self._update_call_status(event, 'cancelled')

def _handle_call_no_answer(self, event: WebhookEvent) -> bool:
    """Handle call no answer event"""
    return self._update_call_status(event, 'no_answer')

def _handle_call_busy(self, event: WebhookEvent) -> bool:
    """Handle call busy event"""
    return self._update_call_status(event, 'busy')

def _handle_recording_ready(self, event: WebhookEvent) -> bool:
    """Handle recording ready event"""
    try:
        with db_manager.get_db_session() as db:
            call_log = db.query(CallLog).filter(CallLog.call_id == event.call_id).first()
            if call_log:
                call_log.recording_url = event.data.get('recording_url')
                call_log.recording_duration = event.data.get('duration')
                call_log.updated_at = datetime.utcnow()

                call_event = CallEvent(
                    call_log_id=call_log.id,
                    event_type=event.event_type,
                    event_data=event.data
                )
                db.add(call_event)
                db.commit()
                return True
        return False
    except Exception as e:
        logger.error(f"Error handling recording ready: {e}")
        return False

def _handle_transcript_ready(self, event: WebhookEvent) -> bool:
    """Handle transcript ready event"""
    try:
        with db_manager.get_db_session() as db:
            call_log = db.query(CallLog).filter(CallLog.call_id == event.call_id).first()
            if call_log:
                call_log.transcript_url = event.data.get('transcript_url')
                call_log.updated_at = datetime.utcnow()

                call_event = CallEvent(
                    call_log_id=call_log.id,
                    event_type=event.event_type,
                    event_data=event.data
                )
                db.add(call_event)
                db.commit()
                return True
        return False
    except Exception as e:
        logger.error(f"Error handling transcript ready: {e}")
        return False

def _handle_participant_joined(self, event: WebhookEvent) -> bool:
    """Handle participant joined event"""
    return self._log_participant_event(event)

def _handle_participant_left(self, event: WebhookEvent) -> bool:
    """Handle participant left event"""
    return self._log_participant_event(event)

def _handle_error_occurred(self, event: WebhookEvent) -> bool:
    """Handle error event"""
    try:
        with db_manager.get_db_session() as db:
            call_log = db.query(CallLog).filter(CallLog.call_id == event.call_id).first()
            if call_log:
                call_log.error_details = event.data.get('error')
                call_log.updated_at = datetime.utcnow()

                call_event = CallEvent(
                    call_log_id=call_log.id,
                    event_type=event.event_type,
                    event_data=event.data
                )
                db.add(call_event)
                db.commit()

                metrics_collector.record_error(event.data.get('error'))
                return True
        return False
    except Exception as e:
        logger.error(f"Error handling error event: {e}")
        return False

def _handle_unknown_event(self, event: WebhookEvent) -> bool:
    """Handle unknown event types"""
    try:
        with db_manager.get_db_session() as db:
            call_event = CallEvent(
                call_id=event.call_id,
                event_type=event.event_type,
                event_data=event.data
            )
            db.add(call_event)
            db.commit()
            return True
    except Exception as e:
        logger.error(f"Error handling unknown event: {e}")
        return False

def _update_call_status(self, event: WebhookEvent, status: str, error_details: str = None) -> bool:
    """Update call status and log event"""
    try:
        with db_manager.get_db_session() as db:
            call_log = db.query(CallLog).filter(CallLog.call_id == event.call_id).first()
            if call_log:
                call_log.status = status
                call_log.updated_at = datetime.utcnow()
                if error_details:
                    call_log.error_details = error_details

                call_event = CallEvent(
                    call_log_id=call_log.id,
                    event_type=event.event_type,
                    event_data=event.data
                )
                db.add(call_event)
                db.commit()
                return True
        return False
    except Exception as e:
        logger.error(f"Error updating call status: {e}")
        return False

def _log_participant_event(self, event: WebhookEvent) -> bool:
    """Log participant related events"""
    try:
        with db_manager.get_db_session() as db:
            call_event = CallEvent(
                call_id=event.call_id,
                event_type=event.event_type,
                event_data=event.data
            )
            db.add(call_event)
            db.commit()
            return True
    except Exception as e:
        logger.error(f"Error logging participant event: {e}")
        return False
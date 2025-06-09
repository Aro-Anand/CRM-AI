# database/models.py
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, Boolean, Float, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy.sql import func
from datetime import datetime
import uuid

Base = declarative_base()

class Customer(Base):
    __tablename__ = 'customers'
    
    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    phone = Column(String(20), nullable=False, unique=True)
    email = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    
    # Relationship to calls
    calls = relationship("CallLog", back_populates="customer")

class CallLog(Base):
    __tablename__ = 'call_logs'
    
    id = Column(Integer, primary_key=True)
    call_id = Column(String(255), unique=True, nullable=False)
    room_name = Column(String(255), nullable=False)
    dispatch_id = Column(String(255), nullable=True)
    
    # Customer information
    customer_id = Column(Integer, nullable=True)  # Foreign key to customers
    customer_name = Column(String(255), nullable=False)
    customer_phone = Column(String(20), nullable=False)
    customer_email = Column(String(255), nullable=True)
    customer_query = Column(Text, nullable=True)
    
    # Call status and timing
    status = Column(String(50), default='initiated')  # initiated, connected, ended, failed
    call_started_at = Column(DateTime, default=func.now())
    call_connected_at = Column(DateTime, nullable=True)
    call_ended_at = Column(DateTime, nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    
    # Call details
    participant_identity = Column(String(255), nullable=True)
    call_metadata = Column(JSON, nullable=True)  # Renamed from metadata to call_metadata
    
    # Relationships
    customer = relationship("Customer", back_populates="calls", foreign_keys=[customer_id])
    
    # Relationship to events
    events = relationship("CallEvent", back_populates="call")

class CallEvent(Base):
    __tablename__ = 'call_events'
    
    id = Column(Integer, primary_key=True)
    call_id = Column(String(255), nullable=False)  # Reference to CallLog.call_id
    event_type = Column(String(100), nullable=False)  # initiated, connected, participant_joined, ended, failed, etc.
    event_data = Column(JSON, nullable=True)
    timestamp = Column(DateTime, default=func.now())
    
    # Relationship to call
    call = relationship("CallLog", back_populates="events")

class CallMetrics(Base):
    __tablename__ = 'call_metrics'
    
    id = Column(Integer, primary_key=True)
    date = Column(DateTime, nullable=False)
    
    # Daily aggregated metrics
    total_calls = Column(Integer, default=0)
    successful_calls = Column(Integer, default=0)
    failed_calls = Column(Integer, default=0)
    average_duration = Column(Float, nullable=True)
    total_duration = Column(Integer, default=0)
    
    # Success rates
    connection_rate = Column(Float, nullable=True)  # Percentage of calls that connected
    completion_rate = Column(Float, nullable=True)  # Percentage of connected calls that completed normally
    
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

class WebhookLog(Base):
    __tablename__ = 'webhook_logs'
    
    id = Column(Integer, primary_key=True)
    webhook_id = Column(String(255), default=lambda: str(uuid.uuid4()))
    call_id = Column(String(255), nullable=False)
    event_type = Column(String(100), nullable=False)
    payload = Column(JSON, nullable=False)
    status_code = Column(Integer, nullable=True)
    response_body = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)
    next_retry_at = Column(DateTime, nullable=True)
    delivered = Column(Boolean, default=False)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

# Utility functions for models
def generate_call_id():
    """Generate a unique call ID"""
    return f"call_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{str(uuid.uuid4())[:8]}"
# database/connection.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.pool import StaticPool
from contextlib import contextmanager
import os
import logging
from .models import Base

logger = logging.getLogger(__name__)

class DatabaseManager:
    def __init__(self):
        self.engine = None
        self.SessionLocal = None
        self.session = None
        
    def initialize(self, database_url: str = None):
        """Initialize database connection"""
        if not database_url:
            database_url = os.getenv("DATABASE_URL")
            
        if not database_url:
            # Default to SQLite for development
            database_url = "sqlite:///./ai_assistant.db"
            logger.info("Using SQLite database")
        
        # Configure engine based on database type
        if database_url.startswith('sqlite'):
            self.engine = create_engine(
                database_url,
                poolclass=StaticPool,
                connect_args={"check_same_thread": False},
                echo=os.getenv("SQL_DEBUG", "false").lower() == "true"
            )
        else:
            self.engine = create_engine(
                database_url,
                pool_pre_ping=True,
                pool_recycle=300,
                echo=os.getenv("SQL_DEBUG", "false").lower() == "true"
            )
        
        self.SessionLocal = scoped_session(sessionmaker(bind=self.engine))
        logger.info(f"Database initialized: {database_url}")
        
    def create_tables(self):
        """Create all tables"""
        Base.metadata.create_all(bind=self.engine)
        logger.info("Database tables created/verified")
        
    def get_session(self):
        """Get database session"""
        return self.SessionLocal()
        
    def close_session(self):
        """Close current session"""
        if self.SessionLocal:
            self.SessionLocal.remove()
    
    @contextmanager
    def get_db_session(self):
        """Context manager for database sessions"""
        db = self.get_session()
        try:
            yield db
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"Database session error: {e}")
            raise
        finally:
            db.close()

# Global database manager instance
db_manager = DatabaseManager()

def init_database(database_url: str = None):
    """Initialize database with tables"""
    db_manager.initialize(database_url)
    db_manager.create_tables()
    return db_manager

def get_db():
    """Dependency for getting database session"""
    return db_manager.get_session()

# Database utility functions
def get_or_create_customer(db, name: str, phone: str, email: str = None):
    """Get existing customer or create new one"""
    from .models import Customer
    
    # Try to find existing customer by phone
    customer = db.query(Customer).filter(Customer.phone == phone).first()
    
    if not customer:
        customer = Customer(
            name=name,
            phone=phone,
            email=email
        )
        db.add(customer)
        db.flush()  # Get the ID without committing
        logger.info(f"Created new customer: {name} ({phone})")
    else:
        # Update customer info if needed
        if customer.name != name:
            customer.name = name
        if email and customer.email != email:
            customer.email = email
        logger.info(f"Found existing customer: {name} ({phone})")
    
    return customer

def create_call_log(db, call_data: dict):
    """Create a new call log entry"""
    from .models import CallLog, generate_call_id
    
    # Get or create customer
    customer = get_or_create_customer(
        db,
        call_data.get('name'),
        call_data.get('phone'),
        call_data.get('email')
    )
    
    # Create call log
    call_log = CallLog(
        call_id=generate_call_id(),
        room_name=call_data.get('room_name'),
        dispatch_id=call_data.get('dispatch_id'),
        customer_id=customer.id,
        customer_name=call_data.get('name'),        customer_phone=call_data.get('phone'),
        customer_email=call_data.get('email'),
        customer_query=call_data.get('query'),
        call_metadata=call_data.get('metadata', {})
    )
    
    db.add(call_log)
    db.flush()
    
    logger.info(f"Created call log: {call_log.call_id}")
    return call_log

def update_call_status(db, call_id: str, status: str, **kwargs):
    """Update call status and related fields"""
    from .models import CallLog
    
    call = db.query(CallLog).filter(CallLog.call_id == call_id).first()
    if call:
        call.status = status
        
        # Update specific fields based on status
        if status == 'connected' and 'connected_at' in kwargs:
            call.call_connected_at = kwargs['connected_at']
        elif status == 'ended' and 'ended_at' in kwargs:
            call.call_ended_at = kwargs['ended_at']
            if 'duration' in kwargs:
                call.duration_seconds = kwargs['duration']
        
        # Update any additional fields
        for key, value in kwargs.items():
            if hasattr(call, key):
                setattr(call, key, value)
        
        db.flush()
        logger.info(f"Updated call {call_id} status to {status}")
        return call
    
    logger.warning(f"Call not found for update: {call_id}")
    return None

def add_call_event(db, call_id: str, event_type: str, event_data: dict = None):
    """Add a call event"""
    from .models import CallEvent
    
    event = CallEvent(
        call_id=call_id,
        event_type=event_type,
        event_data=event_data or {}
    )
    
    db.add(event)
    db.flush()
    
    logger.info(f"Added event {event_type} for call {call_id}")
    return event
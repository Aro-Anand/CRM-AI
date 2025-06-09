# database/migrations.py
import logging
from datetime import datetime, timedelta
from sqlalchemy import text, inspect
from sqlalchemy.exc import SQLAlchemyError
from .connection import db_manager
from .models import Base, CallLog, Customer, CallEvent, WebhookLog

logger = logging.getLogger(__name__)

class MigrationManager:
    """Handles database migrations and schema updates"""
    
    def __init__(self):
        self.migrations = [
            self.migration_001_initial_schema,
            self.migration_002_add_indexes,
            self.migration_003_add_customer_notes,
            self.migration_004_add_call_events_table,
            self.migration_005_add_webhook_logs,
            self.migration_006_add_call_recording_fields,
        ]
    
    def run_migrations(self):
        """Run all pending migrations"""
        try:
            with db_manager.get_db_session() as db:
                # Create migrations table if it doesn't exist
                self._ensure_migrations_table(db)
                
                # Get completed migrations
                completed = self._get_completed_migrations(db)
                
                # Run pending migrations
                for i, migration in enumerate(self.migrations, 1):
                    migration_name = f"migration_{i:03d}"
                    
                    if migration_name not in completed:
                        logger.info(f"Running {migration_name}")
                        try:
                            migration(db)
                            self._mark_migration_completed(db, migration_name)
                            logger.info(f"Completed {migration_name}")
                        except Exception as e:
                            logger.error(f"Failed to run {migration_name}: {e}")
                            raise
                
                logger.info("All migrations completed successfully")
                return True
                
        except Exception as e:
            logger.error(f"Migration failed: {e}")
            return False
    
    def _ensure_migrations_table(self, db):
        """Create migrations tracking table"""
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                id SERIAL PRIMARY KEY,
                migration_name VARCHAR(255) UNIQUE NOT NULL,
                executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        db.commit()
    
    def _get_completed_migrations(self, db):
        """Get list of completed migrations"""
        result = db.execute(text("SELECT migration_name FROM schema_migrations"))
        return [row[0] for row in result.fetchall()]
    
    def _mark_migration_completed(self, db, migration_name):
        """Mark a migration as completed"""
        db.execute(text("""
            INSERT INTO schema_migrations (migration_name) 
            VALUES (:migration_name)
        """), {"migration_name": migration_name})
        db.commit()
    
    def migration_001_initial_schema(self, db):
        """Create initial database schema"""
        # Create all tables
        Base.metadata.create_all(db.bind)
        
        # Add some initial data
        db.execute(text("""
            INSERT INTO customers (name, phone, email, notes, created_at, updated_at)
            VALUES 
            ('System Test Customer', '+1234567890', 'test@example.com', 'Test customer for system validation', NOW(), NOW())
            ON CONFLICT DO NOTHING
        """))
        db.commit()
    
    def migration_002_add_indexes(self, db):
        """Add database indexes for better performance"""
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_call_logs_status ON call_logs(status)",
            "CREATE INDEX IF NOT EXISTS idx_call_logs_customer_phone ON call_logs(customer_phone)",
            "CREATE INDEX IF NOT EXISTS idx_call_logs_call_started_at ON call_logs(call_started_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_call_logs_created_at ON call_logs(created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_customers_phone ON customers(phone)",
            "CREATE INDEX IF NOT EXISTS idx_customers_email ON customers(email)",
            "CREATE INDEX IF NOT EXISTS idx_customers_created_at ON customers(created_at DESC)",
        ]
        
        for index_sql in indexes:
            try:
                db.execute(text(index_sql))
            except SQLAlchemyError as e:
                logger.warning(f"Index creation warning: {e}")
        
        db.commit()
    
    def migration_003_add_customer_notes(self, db):
        """Add notes field to customers if not exists"""
        # Check if column exists
        inspector = inspect(db.bind)
        columns = [col['name'] for col in inspector.get_columns('customers')]
        
        if 'notes' not in columns:
            db.execute(text("ALTER TABLE customers ADD COLUMN notes TEXT"))
            db.commit()
    
    def migration_004_add_call_events_table(self, db):
        """Ensure call_events table exists with proper structure"""
        # This is handled by Base.metadata.create_all in migration_001
        # But we can add additional constraints or modifications here
        
        # Add index for call events
        db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_call_events_call_log_id 
            ON call_events(call_log_id)
        """))
        
        db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_call_events_created_at 
            ON call_events(created_at DESC)
        """))
        
        db.commit()
    
    def migration_005_add_webhook_logs(self, db):
        """Ensure webhook_logs table exists"""
        # Add indexes for webhook logs
        db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_webhook_logs_event_type 
            ON webhook_logs(event_type)
        """))
        
        db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_webhook_logs_created_at 
            ON webhook_logs(created_at DESC)
        """))
        
        db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_webhook_logs_call_id 
            ON webhook_logs(call_id)
        """))
        
        db.commit()
    
    def migration_006_add_call_recording_fields(self, db):
        """Add recording and transcript fields if they don't exist"""
        inspector = inspect(db.bind)
        columns = [col['name'] for col in inspector.get_columns('call_logs')]
        
        alterations = []
        
        if 'recording_url' not in columns:
            alterations.append("ADD COLUMN recording_url VARCHAR(500)")
        
        if 'transcript' not in columns:
            alterations.append("ADD COLUMN transcript TEXT")
        
        if 'summary' not in columns:
            alterations.append("ADD COLUMN summary TEXT")
        
        if alterations:
            alter_sql = f"ALTER TABLE call_logs {', '.join(alterations)}"
            db.execute(text(alter_sql))
            db.commit()
    
    def check_database_health(self):
        """Check database connection and table integrity"""
        try:
            with db_manager.get_db_session() as db:
                # Test basic connectivity
                db.execute(text("SELECT 1"))
                
                # Check if all required tables exist
                inspector = inspect(db.bind)
                existing_tables = inspector.get_table_names()
                
                required_tables = ['call_logs', 'customers', 'call_events', 'webhook_logs']
                missing_tables = [table for table in required_tables if table not in existing_tables]
                
                if missing_tables:
                    logger.error(f"Missing required tables: {missing_tables}")
                    return False
                
                # Check table row counts
                health_info = {}
                for table in required_tables:
                    result = db.execute(text(f"SELECT COUNT(*) FROM {table}"))
                    count = result.scalar()
                    health_info[table] = count
                
                logger.info(f"Database health check passed. Table counts: {health_info}")
                return True
                
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            return False
    
    def create_sample_data(self):
        """Create sample data for testing"""
        try:
            with db_manager.get_db_session() as db:
                # Create sample customers
                sample_customers = [
                    {
                        'name': 'John Doe',
                        'phone': '+1234567890',
                        'email': 'john.doe@example.com',
                        'notes': 'VIP customer, prefers morning calls'
                    },
                    {
                        'name': 'Jane Smith',
                        'phone': '+0987654321',
                        'email': 'jane.smith@example.com',
                        'notes': 'Interested in premium services'
                    },
                    {
                        'name': 'Bob Johnson',
                        'phone': '+1122334455',
                        'email': 'bob.johnson@example.com',
                        'notes': 'Technical support customer'
                    }
                ]
                
                for customer_data in sample_customers:
                    db.execute(text("""
                        INSERT INTO customers (name, phone, email, notes, created_at, updated_at)
                        VALUES (:name, :phone, :email, :notes, :created_at, :updated_at)
                        ON CONFLICT (phone) DO UPDATE SET
                        name = EXCLUDED.name,
                        email = EXCLUDED.email,
                        notes = EXCLUDED.notes,
                        updated_at = EXCLUDED.updated_at
                    """), {
                        **customer_data,
                        'created_at': datetime.utcnow(),
                        'updated_at': datetime.utcnow()
                    })
                
                # Create sample call logs
                sample_calls = [
                    {
                        'call_id': 'call_sample_001',
                        'room_name': 'room_001',
                        'dispatch_id': 'dispatch_001',
                        'customer_name': 'John Doe',
                        'customer_phone': '+1234567890',
                        'customer_email': 'john.doe@example.com',
                        'customer_query': 'Inquiry about premium services',
                        'status': 'completed',
                        'call_started_at': datetime.utcnow() - timedelta(hours=2),
                        'call_ended_at': datetime.utcnow() - timedelta(hours=2, minutes=-15),
                        'duration': 900,  # 15 minutes
                        'summary': 'Customer inquired about premium services. Provided detailed information and scheduled follow-up.'
                    },
                    {
                        'call_id': 'call_sample_002',
                        'room_name': 'room_002',
                        'dispatch_id': 'dispatch_002',
                        'customer_name': 'Jane Smith',
                        'customer_phone': '+0987654321',
                        'customer_email': 'jane.smith@example.com',
                        'customer_query': 'Technical support request',
                        'status': 'completed',
                        'call_started_at': datetime.utcnow() - timedelta(hours=1),
                        'call_ended_at': datetime.utcnow() - timedelta(hours=1, minutes=-10),
                        'duration': 600,  # 10 minutes
                        'summary': 'Resolved technical issue with account access.'
                    }
                ]
                
                for call_data in sample_calls:
                    db.execute(text("""
                        INSERT INTO call_logs (
                            call_id, room_name, dispatch_id, customer_name, customer_phone,
                            customer_email, customer_query, status, call_started_at,
                            call_ended_at, duration, summary, created_at, updated_at
                        ) VALUES (
                            :call_id, :room_name, :dispatch_id, :customer_name, :customer_phone,
                            :customer_email, :customer_query, :status, :call_started_at,
                            :call_ended_at, :duration, :summary, :created_at, :updated_at
                        ) ON CONFLICT (call_id) DO NOTHING
                    """), {
                        **call_data,
                        'created_at': datetime.utcnow(),
                        'updated_at': datetime.utcnow()
                    })
                
                db.commit()
                logger.info("Sample data created successfully")
                return True
                
        except Exception as e:
            logger.error(f"Failed to create sample data: {e}")
            return False

# Global migration manager instance
migration_manager = MigrationManager()

def initialize_database():
    """Initialize database with all migrations"""
    return migration_manager.run_migrations()

def check_database():
    """Check database health"""
    return migration_manager.check_database_health()

def create_sample_data():
    """Create sample data for testing"""
    return migration_manager.create_sample_data()
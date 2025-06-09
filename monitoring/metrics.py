# monitoring/metrics.py
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from database.connection import db_manager
from database.models import CallLog, CallMetrics, CallEvent
from sqlalchemy import func, and_

logger = logging.getLogger(__name__)

class MetricsCollector:
    """Collect and analyze call metrics"""
    
    def __init__(self):
        self.metrics_cache = {}
        self.cache_duration = 300  # 5 minutes cache
    
    def collect_daily_metrics(self, date: datetime = None) -> Dict:
        """Collect daily call metrics"""
        if not date:
            date = datetime.utcnow().date()
        
        cache_key = f"daily_metrics_{date}"
        
        # Check cache
        if cache_key in self.metrics_cache:
            cached_data, cache_time = self.metrics_cache[cache_key]
            if (datetime.utcnow() - cache_time).seconds < self.cache_duration:
                return cached_data
        
        try:
            with db_manager.get_db_session() as db:
                # Query calls for the day
                start_date = datetime.combine(date, datetime.min.time())
                end_date = start_date + timedelta(days=1)
                
                calls_query = db.query(CallLog).filter(
                    and_(
                        CallLog.call_started_at >= start_date,
                        CallLog.call_started_at < end_date
                    )
                )
                
                all_calls = calls_query.all()
                total_calls = len(all_calls)
                
                # Calculate success/failure rates
                successful_calls = len([c for c in all_calls if c.status == 'ended'])
                failed_calls = len([c for c in all_calls if c.status == 'failed'])
                connected_calls = len([c for c in all_calls if c.call_connected_at is not None])
                
                # Calculate durations
                completed_calls = [c for c in all_calls if c.duration_seconds is not None]
                total_duration = sum(c.duration_seconds for c in completed_calls)
                average_duration = total_duration / len(completed_calls) if completed_calls else 0
                
                # Calculate rates
                connection_rate = (connected_calls / total_calls * 100) if total_calls > 0 else 0
                completion_rate = (successful_calls / connected_calls * 100) if connected_calls > 0 else 0
                
                metrics = {
                    'date': date.isoformat(),
                    'total_calls': total_calls,
                    'successful_calls': successful_calls,
                    'failed_calls': failed_calls,
                    'connected_calls': connected_calls,
                    'connection_rate': round(connection_rate, 2),
                    'completion_rate': round(completion_rate, 2),
                    'average_duration': round(average_duration, 2),
                    'total_duration': total_duration
                }
                
                # Cache the result
                self.metrics_cache[cache_key] = (metrics, datetime.utcnow())
                
                return metrics
                
        except Exception as e:
            logger.error(f"Error collecting daily metrics: {e}")
            return {}
    
    def collect_hourly_metrics(self, date: datetime = None) -> List[Dict]:
        """Collect hourly metrics for a day"""
        if not date:
            date = datetime.utcnow().date()
        
        try:
            with db_manager.get_db_session() as db:
                hourly_metrics = []
                
                for hour in range(24):
                    start_time = datetime.combine(date, datetime.min.time()) + timedelta(hours=hour)
                    end_time = start_time + timedelta(hours=1)
                    
                    calls_in_hour = db.query(CallLog).filter(
                        and_(
                            CallLog.call_started_at >= start_time,
                            CallLog.call_started_at < end_time
                        )
                    ).all()
                    
                    total_calls = len(calls_in_hour)
                    successful_calls = len([c for c in calls_in_hour if c.status == 'ended'])
                    failed_calls = len([c for c in calls_in_hour if c.status == 'failed'])
                    
                    hourly_metrics.append({
                        'hour': hour,
                        'timestamp': start_time.isoformat(),
                        'total_calls': total_calls,
                        'successful_calls': successful_calls,
                        'failed_calls': failed_calls
                    })
                
                return hourly_metrics
                
        except Exception as e:
            logger.error(f"Error collecting hourly metrics: {e}")
            return []
    
    def get_call_duration_distribution(self, days: int = 7) -> Dict:
        """Get call duration distribution"""
        try:
            with db_manager.get_db_session() as db:
                start_date = datetime.utcnow() - timedelta(days=days)
                
                calls = db.query(CallLog).filter(
                    and_(
                        CallLog.call_started_at >= start_date,
                        CallLog.duration_seconds.isnot(None)
                    )
                ).all()
                
                # Create duration buckets
                buckets = {
                    '0-30s': 0,
                    '30s-1m': 0,
                    '1-2m': 0,
                    '2-5m': 0,
                    '5-10m': 0,
                    '10m+': 0
                }
                
                for call in calls:
                    duration = call.duration_seconds
                    
                    if duration <= 30:
                        buckets['0-30s'] += 1
                    elif duration <= 60:
                        buckets['30s-1m'] += 1
                    elif duration <= 120:
                        buckets['1-2m'] += 1
                    elif duration <= 300:
                        buckets['2-5m'] += 1
                    elif duration <= 600:
                        buckets['5-10m'] += 1
                    else:
                        buckets['10m+'] += 1
                
                return buckets
                
        except Exception as e:
            logger.error(f"Error getting duration distribution: {e}")
            return {}
    
    def get_top_failure_reasons(self, days: int = 7) -> List[Dict]:
        """Get top call failure reasons"""
        try:
            with db_manager.get_db_session() as db:
                start_date = datetime.utcnow() - timedelta(days=days)
                
                # Query failed calls with SIP status
                failed_calls = db.query(
                    CallLog.sip_status_code,
                    CallLog.sip_status_message,
                    func.count(CallLog.id).label('count')
                ).filter(
                    and_(
                        CallLog.call_started_at >= start_date,
                        CallLog.status == 'failed',
                        CallLog.sip_status_code.isnot(None)
                    )
                ).group_by(
                    CallLog.sip_status_code,
                    CallLog.sip_status_message
                ).order_by(
                    func.count(CallLog.id).desc()
                ).limit(10).all()
                
                return [
                    {
                        'sip_code': result.sip_status_code,
                        'sip_message': result.sip_status_message,
                        'count': result.count
                    }
                    for result in failed_calls
                ]
                
        except Exception as e:
            logger.error(f"Error getting failure reasons: {e}")
            return []
    
    def get_peak_hours(self, days: int = 7) -> List[Dict]:
        """Get peak calling hours"""
        try:
            with db_manager.get_db_session() as db:
                start_date = datetime.utcnow() - timedelta(days=days)
                
                # Extract hour from call_started_at and count calls
                hourly_data = db.query(
                    func.extract('hour', CallLog.call_started_at).label('hour'),
                    func.count(CallLog.id).label('call_count')
                ).filter(
                    CallLog.call_started_at >= start_date
                ).group_by(
                    func.extract('hour', CallLog.call_started_at)
                ).order_by(
                    func.count(CallLog.id).desc()
                ).all()
                
                return [
                    {
                        'hour': int(result.hour),
                        'call_count': result.call_count,
                        'hour_display': f"{int(result.hour):02d}:00"
                    }
                    for result in hourly_data
                ]
                
        except Exception as e:
            logger.error(f"Error getting peak hours: {e}")
            return []
    
    def store_daily_metrics(self, date: datetime = None):
        """Store daily metrics in database"""
        if not date:
            date = datetime.utcnow().date()
        
        try:
            metrics = self.collect_daily_metrics(date)
            
            if not metrics:
                return
            
            with db_manager.get_db_session() as db:
                # Check if metrics already exist for this date
                existing_metrics = db.query(CallMetrics).filter(
                    func.date(CallMetrics.date) == date
                ).first()
                
                if existing_metrics:
                    # Update existing metrics
                    existing_metrics.total_calls = metrics['total_calls']
                    existing_metrics.successful_calls = metrics['successful_calls']
                    existing_metrics.failed_calls = metrics['failed_calls']
                    existing_metrics.average_duration = metrics['average_duration']
                    existing_metrics.total_duration = metrics['total_duration']
                    existing_metrics.connection_rate = metrics['connection_rate']
                    existing_metrics.completion_rate = metrics['completion_rate']
                    existing_metrics.updated_at = datetime.utcnow()
                else:
                    # Create new metrics record
                    call_metrics = CallMetrics(
                        date=datetime.combine(date, datetime.min.time()),
                        total_calls=metrics['total_calls'],
                        successful_calls=metrics['successful_calls'],
                        failed_calls=metrics['failed_calls'],
                        average_duration=metrics['average_duration'],
                        total_duration=metrics['total_duration'],
                        connection_rate=metrics['connection_rate'],
                        completion_rate=metrics['completion_rate']
                    )
                    db.add(call_metrics)
                
                db.commit()
                logger.info(f"Stored daily metrics for {date}")
                
        except Exception as e:
            logger.error(f"Error storing daily metrics: {e}")
    
    def get_metrics_summary(self, days: int = 30) -> Dict:
        """Get comprehensive metrics summary"""
        try:
            start_date = datetime.utcnow() - timedelta(days=days)
            
            with db_manager.get_db_session() as db:
                # Get overall stats
                total_calls = db.query(CallLog).filter(
                    CallLog.call_started_at >= start_date
                ).count()
                
                successful_calls = db.query(CallLog).filter(
                    and_(
                        CallLog.call_started_at >= start_date,
                        CallLog.status == 'ended'
                    )
                ).count()
                
                # Get average call duration
                avg_duration_result = db.query(
                    func.avg(CallLog.duration_seconds)
                ).filter(
                    and_(
                        CallLog.call_started_at >= start_date,
                        CallLog.duration_seconds.isnot(None)
                    )
                ).scalar()
                
                avg_duration = round(avg_duration_result or 0, 2)
                
                # Get unique customers
                unique_customers = db.query(CallLog.customer_phone).filter(
                    CallLog.call_started_at >= start_date
                ).distinct().count()
                
                summary = {
                    'period_days': days,
                    'total_calls': total_calls,
                    'successful_calls': successful_calls,
                    'success_rate': round((successful_calls / total_calls * 100) if total_calls > 0 else 0, 2),
                    'average_duration': avg_duration,
                    'unique_customers': unique_customers,
                    'duration_distribution': self.get_call_duration_distribution(days),
                    'failure_reasons': self.get_top_failure_reasons(days),
                    'peak_hours': self.get_peak_hours(days)
                }
                
                return summary
                
        except Exception as e:
            logger.error(f"Error getting metrics summary: {e}")
            return {}

# Global metrics collector instance
metrics_collector = MetricsCollector()

def get_daily_metrics(date: datetime = None) -> Dict:
    """Get daily metrics"""
    return metrics_collector.collect_daily_metrics(date)

def get_metrics_summary(days: int = 30) -> Dict:
    """Get comprehensive metrics summary"""
    return metrics_collector.get_metrics_summary(days)
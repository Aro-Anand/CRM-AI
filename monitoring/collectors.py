# monitoring/collectors.py
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from collections import defaultdict
from dataclasses import dataclass
from sqlalchemy import func, and_, or_
from database.connection import db_manager
from database.models import CallLog, CallEvent, WebhookLog

logger = logging.getLogger(__name__)

@dataclass
class MetricPoint:
    """Single metric data point"""
    timestamp: datetime
    value: float
    labels: Dict[str, str] = None

@dataclass
class MetricsSummary:
    """Summary of metrics for a time period"""
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    average_duration: float = 0.0
    success_rate: float = 0.0
    total_duration: int = 0
    unique_customers: int = 0
    peak_hour: int = 0
    peak_hour_calls: int = 0

class MetricsCollector:
    """Collects and calculates various call metrics"""
    
    def __init__(self):
        self.cache = {}
        self.cache_ttl = 300  # 5 minutes cache TTL
    
    def collect_daily_metrics(self, date: Optional[datetime] = None) -> MetricsSummary:
        """Collect metrics for a specific day"""
        if date is None:
            date = datetime.utcnow()
        
        cache_key = f"daily_metrics_{date.strftime('%Y-%m-%d')}"
        
        # Check cache
        if self._is_cache_valid(cache_key):
            return self.cache[cache_key]['data']
        
        try:
            start_date = date.replace(hour=0, minute=0, second=0, microsecond=0)
            end_date = start_date + timedelta(days=1)
            
            with db_manager.get_db_session() as db:
                # Basic call metrics
                call_query = db.query(CallLog).filter(
                    and_(
                        CallLog.call_started_at >= start_date,
                        CallLog.call_started_at < end_date
                    )
                )
                
                total_calls = call_query.count()
                
                if total_calls == 0:
                    return MetricsSummary()
                
                calls = call_query.all()
                
                # Calculate metrics
                successful_calls = len([c for c in calls if c.status == 'completed'])
                failed_calls = len([c for c in calls if c.status in ['failed', 'cancelled', 'no-answer']])
                
                # Duration metrics (only for completed calls)
                completed_calls = [c for c in calls if c.status == 'completed' and c.duration]
                total_duration = sum(c.duration for c in completed_calls) if completed_calls else 0
                average_duration = total_duration / len(completed_calls) if completed_calls else 0
                
                # Success rate
                success_rate = (successful_calls / total_calls * 100) if total_calls > 0 else 0
                
                # Unique customers
                unique_customers = len(set(c.customer_phone for c in calls if c.customer_phone))
                
                # Peak hour analysis
                hourly_counts = defaultdict(int)
                for call in calls:
                    if call.call_started_at:
                        hour = call.call_started_at.hour
                        hourly_counts[hour] += 1
                
                peak_hour = max(hourly_counts.keys(), key=lambda h: hourly_counts[h]) if hourly_counts else 0
                peak_hour_calls = hourly_counts[peak_hour] if hourly_counts else 0
                
                summary = MetricsSummary(
                    total_calls=total_calls,
                    successful_calls=successful_calls,
                    failed_calls=failed_calls,
                    average_duration=average_duration,
                    success_rate=success_rate,
                    total_duration=total_duration,
                    unique_customers=unique_customers,
                    peak_hour=peak_hour,
                    peak_hour_calls=peak_hour_calls
                )
                
                # Cache the result
                self._cache_result(cache_key, summary)
                return summary
                
        except Exception as e:
            logger.error(f"Error collecting daily metrics: {e}")
            return MetricsSummary()
    
    def get_metrics_summary(self, days: int) -> MetricsSummary:
        """Get metrics summary for the last N days"""
        cache_key = f"metrics_summary_{days}d"
        
        if self._is_cache_valid(cache_key):
            return self.cache[cache_key]['data']
        
        try:
            end_date = datetime.utcnow()
            start_date = end_date - timedelta(days=days)
            
            with db_manager.get_db_session() as db:
                call_query = db.query(CallLog).filter(
                    and_(
                        CallLog.call_started_at >= start_date,
                        CallLog.call_started_at <= end_date
                    )
                )
                
                calls = call_query.all()
                total_calls = len(calls)
                
                if total_calls == 0:
                    return MetricsSummary()
                
                # Calculate aggregate metrics
                successful_calls = len([c for c in calls if c.status == 'completed'])
                failed_calls = len([c for c in calls if c.status in ['failed', 'cancelled', 'no-answer']])
                
                completed_calls = [c for c in calls if c.status == 'completed' and c.duration]
                total_duration = sum(c.duration for c in completed_calls) if completed_calls else 0
                average_duration = total_duration / len(completed_calls) if completed_calls else 0
                
                success_rate = (successful_calls / total_calls * 100) if total_calls > 0 else 0
                unique_customers = len(set(c.customer_phone for c in calls if c.customer_phone))
                
                # Peak hour analysis across all days
                hourly_counts = defaultdict(int)
                for call in calls:
                    if call.call_started_at:
                        hour = call.call_started_at.hour
                        hourly_counts[hour] += 1
                
                peak_hour = max(hourly_counts.keys(), key=lambda h: hourly_counts[h]) if hourly_counts else 0
                peak_hour_calls = hourly_counts[peak_hour] if hourly_counts else 0
                
                summary = MetricsSummary(
                    total_calls=total_calls,
                    successful_calls=successful_calls,
                    failed_calls=failed_calls,
                    average_duration=average_duration,
                    success_rate=success_rate,
                    total_duration=total_duration,
                    unique_customers=unique_customers,
                    peak_hour=peak_hour,
                    peak_hour_calls=peak_hour_calls
                )
                
                self._cache_result(cache_key, summary)
                return summary
                
        except Exception as e:
            logger.error(f"Error getting metrics summary: {e}")
            return MetricsSummary()
    
    def get_hourly_distribution(self, days: int = 7) -> Dict[int, int]:
        """Get hourly call distribution for the last N days"""
        cache_key = f"hourly_dist_{days}d"
        
        if self._is_cache_valid(cache_key):
            return self.cache[cache_key]['data']
        
        try:
            end_date = datetime.utcnow()
            start_date = end_date - timedelta(days=days)
            
            with db_manager.get_db_session() as db:
                # Query hourly distribution
                result = db.query(
                    func.extract('hour', CallLog.call_started_at).label('hour'),
                    func.count(CallLog.id).label('count')
                ).filter(
                    and_(
                        CallLog.call_started_at >= start_date,
                        CallLog.call_started_at <= end_date
                    )
                ).group_by('hour').all()
                
                hourly_dist = {int(hour): count for hour, count in result}
                
                # Fill missing hours with 0
                for hour in range(24):
                    if hour not in hourly_dist:
                        hourly_dist[hour] = 0
                
                self._cache_result(cache_key, hourly_dist)
                return hourly_dist
                
        except Exception as e:
            logger.error(f"Error getting hourly distribution: {e}")
            return {hour: 0 for hour in range(24)}
    
    def get_daily_trend(self, days: int = 30) -> List[Dict[str, Any]]:
        """Get daily call trend for the last N days"""
        cache_key = f"daily_trend_{days}d"
        
        if self._is_cache_valid(cache_key):
            return self.cache[cache_key]['data']
        
        try:
            end_date = datetime.utcnow()
            start_date = end_date - timedelta(days=days)
            
            with db_manager.get_db_session() as db:
                # Query daily counts
                result = db.query(
                    func.date(CallLog.call_started_at).label('date'),
                    func.count(CallLog.id).label('total_calls'),
                    func.sum(
                        func.case(
                            (CallLog.status == 'completed', 1),
                            else_=0
                        )
                    ).label('successful_calls')
                ).filter(
                    and_(
                        CallLog.call_started_at >= start_date,
                        CallLog.call_started_at <= end_date
                    )
                ).group_by(func.date(CallLog.call_started_at)).all()
                
                trend_data = []
                for date, total, successful in result:
                    success_rate = (successful / total * 100) if total > 0 else 0
                    trend_data.append({
                        'date': date.isoformat() if date else None,
                        'total_calls': total or 0,
                        'successful_calls': successful or 0,
                        'success_rate': round(success_rate, 2)
                    })
                
                # Sort by date
                trend_data.sort(key=lambda x: x['date'] or '')
                
                self._cache_result(cache_key, trend_data)
                return trend_data
                
        except Exception as e:
            logger.error(f"Error getting daily trend: {e}")
            return []
    
    def get_status_distribution(self, days: int = 7) -> Dict[str, int]:
        """Get call status distribution for the last N days"""
        cache_key = f"status_dist_{days}d"
        
        if self._is_cache_valid(cache_key):
            return self.cache[cache_key]['data']
        
        try:
            end_date = datetime.utcnow()
            start_date = end_date - timedelta(days=days)
            
            with db_manager.get_db_session() as db:
                result = db.query(
                    CallLog.status,
                    func.count(CallLog.id).label('count')
                ).filter(
                    and_(
                        CallLog.call_started_at >= start_date,
                        CallLog.call_started_at <= end_date
                    )
                ).group_by(CallLog.status).all()
                
                status_dist = {status: count for status, count in result}
                
                self._cache_result(cache_key, status_dist)
                return status_dist
                
        except Exception as e:
            logger.error(f"Error getting status distribution: {e}")
            return {}
    
    def get_customer_metrics(self, days: int = 30) -> Dict[str, Any]:
        """Get customer-related metrics"""
        cache_key = f"customer_metrics_{days}d"
        
        if self._is_cache_valid(cache_key):
            return self.cache[cache_key]['data']
        
        try:
            end_date = datetime.utcnow()
            start_date = end_date - timedelta(days=days)
            
            with db_manager.get_db_session() as db:
                # Get customer call frequency
                result = db.query(
                    CallLog.customer_phone,
                    CallLog.customer_name,
                    func.count(CallLog.id).label('call_count'),
                    func.avg(CallLog.duration).label('avg_duration')
                ).filter(
                    and_(
                        CallLog.call_started_at >= start_date,
                        CallLog.call_started_at <= end_date,
                        CallLog.customer_phone.isnot(None)
                    )
                ).group_by(
                    CallLog.customer_phone,
                    CallLog.customer_name
                ).order_by(func.count(CallLog.id).desc()).limit(10).all()
                
                top_customers = []
                for phone, name, count, avg_dur in result:
                    top_customers.append({
                        'phone': phone,
                        'name': name or 'Unknown',
                        'call_count': count,
                        'avg_duration': round(avg_dur or 0, 2)
                    })
                
                # Get total unique customers
                unique_customers = db.query(CallLog.customer_phone).filter(
                    and_(
                        CallLog.call_started_at >= start_date,
                        CallLog.call_started_at <= end_date,
                        CallLog.customer_phone.isnot(None)
                    )
                ).distinct().count()
                
                # Get repeat customers (more than 1 call)
                repeat_customers = db.query(
                    CallLog.customer_phone
                ).filter(
                    and_(
                        CallLog.call_started_at >= start_date,
                        CallLog.call_started_at <= end_date,
                        CallLog.customer_phone.isnot(None)
                    )
                ).group_by(CallLog.customer_phone).having(
                    func.count(CallLog.id) > 1
                ).count()
                
                metrics = {
                    'unique_customers': unique_customers,
                    'repeat_customers': repeat_customers,
                    'repeat_rate': (repeat_customers / unique_customers * 100) if unique_customers > 0 else 0,
                    'top_customers': top_customers
                }
                
                self._cache_result(cache_key, metrics)
                return metrics
                
        except Exception as e:
            logger.error(f"Error getting customer metrics: {e}")
            return {
                'unique_customers': 0,
                'repeat_customers': 0,
                'repeat_rate': 0,
                'top_customers': []
            }
    
    def get_performance_metrics(self, days: int = 7) -> Dict[str, Any]:
        """Get system performance metrics"""
        cache_key = f"performance_metrics_{days}d"
        
        if self._is_cache_valid(cache_key):
            return self.cache[cache_key]['data']
        
        try:
            end_date = datetime.utcnow()
            start_date = end_date - timedelta(days=days)
            
            with db_manager.get_db_session() as db:
                # Average call setup time (using webhook logs)
                webhook_query = db.query(WebhookLog).filter(
                    and_(
                        WebhookLog.created_at >= start_date,
                        WebhookLog.created_at <= end_date
                    )
                )
                
                total_webhooks = webhook_query.count()
                successful_webhooks = webhook_query.filter(
                    WebhookLog.response_status.between(200, 299)
                ).count()
                
                webhook_success_rate = (successful_webhooks / total_webhooks * 100) if total_webhooks > 0 else 0
                
                # Call answer rate
                total_calls = db.query(CallLog).filter(
                    and_(
                        CallLog.call_started_at >= start_date,
                        CallLog.call_started_at <= end_date
                    )
                ).count()
                
                answered_calls = db.query(CallLog).filter(
                    and_(
                        CallLog.call_started_at >= start_date,
                        CallLog.call_started_at <= end_date,
                        CallLog.status.in_(['completed', 'connected'])
                    )
                ).count()
                
                answer_rate = (answered_calls / total_calls * 100) if total_calls > 0 else 0
                
                # Average response time (time between call events)
                avg_response_time = self._calculate_avg_response_time(db, start_date, end_date)
                
                metrics = {
                    'webhook_success_rate': round(webhook_success_rate, 2),
                    'call_answer_rate': round(answer_rate, 2),
                    'avg_response_time': avg_response_time,
                    'total_webhooks': total_webhooks,
                    'successful_webhooks': successful_webhooks
                }
                
                self._cache_result(cache_key, metrics)
                return metrics
                
        except Exception as e:
            logger.error(f"Error getting performance metrics: {e}")
            return {
                'webhook_success_rate': 0,
                'call_answer_rate': 0,
                'avg_response_time': 0,
                'total_webhooks': 0,
                'successful_webhooks': 0
            }
    
    def _calculate_avg_response_time(self, db, start_date, end_date) -> float:
        """Calculate average response time between call events"""
        try:
            # Get call events for completed calls
            events = db.query(CallEvent).join(CallLog).filter(
                and_(
                    CallLog.call_started_at >= start_date,
                    CallLog.call_started_at <= end_date,
                    CallLog.status == 'completed'
                )
            ).order_by(CallEvent.call_log_id, CallEvent.created_at).all()
            
            response_times = []
            current_call_id = None
            last_event_time = None
            
            for event in events:
                if current_call_id != event.call_log_id:
                    current_call_id = event.call_log_id
                    last_event_time = event.created_at
                    continue
                
                if last_event_time:
                    time_diff = (event.created_at - last_event_time).total_seconds()
                    if 0 < time_diff < 300:  # Only consider reasonable response times (< 5 minutes)
                        response_times.append(time_diff)
                
                last_event_time = event.created_at
            
            return sum(response_times) / len(response_times) if response_times else 0
            
        except Exception as e:
            logger.error(f"Error calculating average response time: {e}")
            return 0
    
    def _is_cache_valid(self, cache_key: str) -> bool:
        """Check if cached data is still valid"""
        if cache_key not in self.cache:
            return False
        
        cache_time = self.cache[cache_key]['timestamp']
        return (datetime.utcnow() - cache_time).total_seconds() < self.cache_ttl
    
    def _cache_result(self, cache_key: str, data: Any):
        """Cache result with timestamp"""
        self.cache[cache_key] = {
            'data': data,
            'timestamp': datetime.utcnow()
        }
    
    def clear_cache(self):
        """Clear all cached metrics"""
        self.cache.clear()
        logger.info("Metrics cache cleared")
    
    def get_real_time_metrics(self) -> Dict[str, Any]:
        """Get real-time metrics (no caching)"""
        try:
            with db_manager.get_db_session() as db:
                now = datetime.utcnow()
                
                # Active calls
                active_calls = db.query(CallLog).filter(
                    CallLog.status.in_(['initiated', 'connected', 'ringing'])
                ).count()
                
                # Calls in last hour
                last_hour = now - timedelta(hours=1)
                recent_calls = db.query(CallLog).filter(
                    CallLog.call_started_at >= last_hour
                ).count()
                
                # Current system load (approximate)
                load_indicator = min(active_calls * 10, 100)  # Simple load calculation
                
                return {
                    'active_calls': active_calls,
                    'recent_calls_1h': recent_calls,
                    'system_load': load_indicator,
                    'timestamp': now.isoformat()
                }
                
        except Exception as e:
            logger.error(f"Error getting real-time metrics: {e}")
            return {
                'active_calls': 0,
                'recent_calls_1h': 0,
                'system_load': 0,
                'timestamp': datetime.utcnow().isoformat()
            }

# Global metrics collector instance
metrics_collector = MetricsCollector()
# crm_ui/routes.py
from flask import Blueprint, render_template, request, jsonify, send_file
from datetime import datetime, timedelta
from database.connection import db_manager
from database.models import CallLog, Customer, CallEvent, WebhookLog
from monitoring.metrics import metrics_collector
from security.auth import require_api_key
import json
import io
import csv
from sqlalchemy import and_, or_, desc, func

crm_bp = Blueprint('crm', __name__, url_prefix='/crm')

@crm_bp.route('/')
def dashboard():
    """Main CRM dashboard"""
    return render_template('crm_ui/dashboard.html')

@crm_bp.route('/api/dashboard-stats')
@require_api_key
def dashboard_stats():
    """Get dashboard statistics"""
    try:
        # Get metrics for different time periods
        today_metrics = metrics_collector.collect_daily_metrics()
        weekly_metrics = metrics_collector.get_metrics_summary(7)
        monthly_metrics = metrics_collector.get_metrics_summary(30)
        
        with db_manager.get_db_session() as db:
            # Get recent calls count
            recent_calls = db.query(CallLog).filter(
                CallLog.call_started_at >= datetime.utcnow() - timedelta(hours=24)
            ).count()
            
            # Get active calls (initiated or connected)
            active_calls = db.query(CallLog).filter(
                CallLog.status.in_(['initiated', 'connected'])
            ).count()
            
            # Get total customers
            total_customers = db.query(Customer).count()
            
            stats = {
                'today': today_metrics,
                'weekly': weekly_metrics,
                'monthly': monthly_metrics,
                'recent_calls_24h': recent_calls,
                'active_calls': active_calls,
                'total_customers': total_customers,
                'last_updated': datetime.utcnow().isoformat()
            }
            
            return jsonify(stats)
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@crm_bp.route('/api/calls')
@require_api_key
def get_calls():
    """Get calls with filtering and pagination"""
    try:
        # Get query parameters
        page = int(request.args.get('page', 1))
        per_page = min(int(request.args.get('per_page', 20)), 100)
        status = request.args.get('status')
        customer_name = request.args.get('customer_name')
        phone_number = request.args.get('phone_number')
        date_from = request.args.get('date_from')
        date_to = request.args.get('date_to')
        
        with db_manager.get_db_session() as db:
            query = db.query(CallLog)
            
            # Apply filters
            if status:
                query = query.filter(CallLog.status == status)
            
            if customer_name:
                query = query.filter(CallLog.customer_name.ilike(f'%{customer_name}%'))
            
            if phone_number:
                query = query.filter(CallLog.customer_phone.ilike(f'%{phone_number}%'))
            
            if date_from:
                date_from_dt = datetime.fromisoformat(date_from.replace('Z', '+00:00'))
                query = query.filter(CallLog.call_started_at >= date_from_dt)
            
            if date_to:
                date_to_dt = datetime.fromisoformat(date_to.replace('Z', '+00:00'))
                query = query.filter(CallLog.call_started_at <= date_to_dt)
            
            # Order by latest first
            query = query.order_by(desc(CallLog.call_started_at))
            
            # Paginate
            total = query.count()
            calls = query.offset((page - 1) * per_page).limit(per_page).all()
            
            # Format response
            calls_data = []
            for call in calls:
                call_data = {
                    'id': call.id,
                    'call_id': call.call_id,
                    'room_name': call.room_name,
                    'dispatch_id': call.dispatch_id,
                    'customer_name': call.customer_name,
                    'customer_phone': call.customer_phone,
                    'customer_email': call.customer_email,
                    'customer_query': call.customer_query,
                    'status': call.status,
                    'call_started_at': call.call_started_at.isoformat() if call.call_started_at else None,
                    'call_ended_at': call.call_ended_at.isoformat() if call.call_ended_at else None,
                    'duration': call.duration,
                    'recording_url': call.recording_url,
                    'transcript': call.transcript,
                    'summary': call.summary,
                    'created_at': call.created_at.isoformat(),
                    'updated_at': call.updated_at.isoformat()
                }
                calls_data.append(call_data)
            
            return jsonify({
                'calls': calls_data,
                'pagination': {
                    'page': page,
                    'per_page': per_page,
                    'total': total,
                    'pages': (total + per_page - 1) // per_page
                }
            })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@crm_bp.route('/api/calls/<call_id>')
@require_api_key
def get_call_details(call_id):
    """Get detailed information about a specific call"""
    try:
        with db_manager.get_db_session() as db:
            call = db.query(CallLog).filter(CallLog.call_id == call_id).first()
            if not call:
                return jsonify({'error': 'Call not found'}), 404
            
            # Get call events
            events = db.query(CallEvent).filter(
                CallEvent.call_log_id == call.id
            ).order_by(CallEvent.created_at).all()
            
            # Format call data
            call_data = {
                'id': call.id,
                'call_id': call.call_id,
                'room_name': call.room_name,
                'dispatch_id': call.dispatch_id,
                'customer_name': call.customer_name,
                'customer_phone': call.customer_phone,
                'customer_email': call.customer_email,
                'customer_query': call.customer_query,
                'status': call.status,
                'call_started_at': call.call_started_at.isoformat() if call.call_started_at else None,
                'call_ended_at': call.call_ended_at.isoformat() if call.call_ended_at else None,
                'duration': call.duration,
                'recording_url': call.recording_url,
                'transcript': call.transcript,
                'summary': call.summary,
                'created_at': call.created_at.isoformat(),
                'updated_at': call.updated_at.isoformat(),
                'events': [
                    {
                        'id': event.id,
                        'event_type': event.event_type,
                        'event_data': event.event_data,
                        'created_at': event.created_at.isoformat()
                    }
                    for event in events
                ]
            }
            
            return jsonify(call_data)
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@crm_bp.route('/api/customers')
@require_api_key
def get_customers():
    """Get customers with pagination and search"""
    try:
        page = int(request.args.get('page', 1))
        per_page = min(int(request.args.get('per_page', 20)), 100)
        search = request.args.get('search', '').strip()
        
        with db_manager.get_db_session() as db:
            query = db.query(Customer)
            
            if search:
                query = query.filter(
                    or_(
                        Customer.name.ilike(f'%{search}%'),
                        Customer.phone.ilike(f'%{search}%'),
                        Customer.email.ilike(f'%{search}%')
                    )
                )
            
            # Order by latest first
            query = query.order_by(desc(Customer.created_at))
            
            # Paginate
            total = query.count()
            customers = query.offset((page - 1) * per_page).limit(per_page).all()
            
            customers_data = []
            for customer in customers:
                # Get call count for customer
                call_count = db.query(CallLog).filter(
                    CallLog.customer_phone == customer.phone
                ).count()
                
                customer_data = {
                    'id': customer.id,
                    'name': customer.name,
                    'phone': customer.phone,
                    'email': customer.email,
                    'notes': customer.notes,
                    'call_count': call_count,
                    'created_at': customer.created_at.isoformat(),
                    'updated_at': customer.updated_at.isoformat()
                }
                customers_data.append(customer_data)
            
            return jsonify({
                'customers': customers_data,
                'pagination': {
                    'page': page,
                    'per_page': per_page,
                    'total': total,
                    'pages': (total + per_page - 1) // per_page
                }
            })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@crm_bp.route('/api/customers/<customer_id>/calls')
@require_api_key
def get_customer_calls(customer_id):
    """Get all calls for a specific customer"""
    try:
        with db_manager.get_db_session() as db:
            customer = db.query(Customer).filter(Customer.id == customer_id).first()
            if not customer:
                return jsonify({'error': 'Customer not found'}), 404
            
            calls = db.query(CallLog).filter(
                CallLog.customer_phone == customer.phone
            ).order_by(desc(CallLog.call_started_at)).all()
            
            calls_data = []
            for call in calls:
                call_data = {
                    'id': call.id,
                    'call_id': call.call_id,
                    'status': call.status,
                    'call_started_at': call.call_started_at.isoformat() if call.call_started_at else None,
                    'call_ended_at': call.call_ended_at.isoformat() if call.call_ended_at else None,
                    'duration': call.duration,
                    'summary': call.summary
                }
                calls_data.append(call_data)
            
            return jsonify({
                'customer': {
                    'id': customer.id,
                    'name': customer.name,
                    'phone': customer.phone,
                    'email': customer.email
                },
                'calls': calls_data
            })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@crm_bp.route('/api/export/calls')
@require_api_key
def export_calls():
    """Export calls to CSV"""
    try:
        with db_manager.get_db_session() as db:
            # Get all calls or apply filters from query params
            query = db.query(CallLog)
            
            # Apply same filters as get_calls
            status = request.args.get('status')
            customer_name = request.args.get('customer_name')
            phone_number = request.args.get('phone_number')
            date_from = request.args.get('date_from')
            date_to = request.args.get('date_to')
            
            if status:
                query = query.filter(CallLog.status == status)
            if customer_name:
                query = query.filter(CallLog.customer_name.ilike(f'%{customer_name}%'))
            if phone_number:
                query = query.filter(CallLog.customer_phone.ilike(f'%{phone_number}%'))
            if date_from:
                date_from_dt = datetime.fromisoformat(date_from.replace('Z', '+00:00'))
                query = query.filter(CallLog.call_started_at >= date_from_dt)
            if date_to:
                date_to_dt = datetime.fromisoformat(date_to.replace('Z', '+00:00'))
                query = query.filter(CallLog.call_started_at <= date_to_dt)
            
            calls = query.order_by(desc(CallLog.call_started_at)).all()
            
            # Create CSV
            output = io.StringIO()
            writer = csv.writer(output)
            
            # Write header
            writer.writerow([
                'Call ID', 'Customer Name', 'Customer Phone', 'Customer Email',
                'Status', 'Started At', 'Ended At', 'Duration (seconds)',
                'Summary', 'Created At'
            ])
            
            # Write data
            for call in calls:
                writer.writerow([
                    call.call_id,
                    call.customer_name,
                    call.customer_phone,
                    call.customer_email,
                    call.status,
                    call.call_started_at.isoformat() if call.call_started_at else '',
                    call.call_ended_at.isoformat() if call.call_ended_at else '',
                    call.duration or '',
                    call.summary or '',
                    call.created_at.isoformat()
                ])
            
            # Prepare file for download
            output.seek(0)
            file_data = io.BytesIO()
            file_data.write(output.getvalue().encode('utf-8'))
            file_data.seek(0)
            
            return send_file(
                file_data,
                mimetype='text/csv',
                as_attachment=True,
                download_name=f'calls_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
            )
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@crm_bp.route('/api/metrics/overview')
@require_api_key
def metrics_overview():
    """Get comprehensive metrics overview"""
    try:
        # Get different time period metrics
        today = metrics_collector.collect_daily_metrics()
        week = metrics_collector.get_metrics_summary(7)
        month = metrics_collector.get_metrics_summary(30)
        
        # Get call status distribution
        with db_manager.get_db_session() as db:
            status_counts = db.query(
                CallLog.status,
                db.func.count(CallLog.id).label('count')
            ).group_by(CallLog.status).all()
            
            status_distribution = {status: count for status, count in status_counts}
            
            # Get hourly call distribution for today
            today_start = datetime.utcnow().replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            hourly_calls = db.query(
                db.func.extract('hour', CallLog.call_started_at).label('hour'),
                db.func.count(CallLog.id).label('count')
            ).filter(
                CallLog.call_started_at >= today_start
            ).group_by('hour').all()
            
            hourly_distribution = {int(hour): count for hour, count in hourly_calls}
            
            # Fill missing hours with 0
            for hour in range(24):
                if hour not in hourly_distribution:
                    hourly_distribution[hour] = 0
        
        return jsonify({
            'time_periods': {
                'today': today,
                'week': week,
                'month': month
            },
            'status_distribution': status_distribution,
            'hourly_distribution': hourly_distribution,
            'generated_at': datetime.utcnow().isoformat()
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@crm_bp.route('/metrics')
def metrics():
    """View metrics and analytics"""
    return render_template('crm/metrics.html')

@crm_bp.route('/api/metrics/summary')
@require_api_key
def metrics_summary():
    """Get metrics summary for dashboard"""
    try:
        with db_manager.get_db_session() as db:
            daily_stats = {
                'total_calls': db.query(CallLog).filter(
                    CallLog.created_at >= datetime.utcnow() - timedelta(days=1)
                ).count(),
                'successful_calls': db.query(CallLog).filter(
                    CallLog.created_at >= datetime.utcnow() - timedelta(days=1),
                    CallLog.status == 'completed'
                ).count(),
                'failed_calls': db.query(CallLog).filter(
                    CallLog.created_at >= datetime.utcnow() - timedelta(days=1),
                    CallLog.status == 'failed'
                ).count()
            }
            
            call_distribution = db.query(
                CallLog.status,
                func.count(CallLog.id).label('count')
            ).group_by(CallLog.status).all()
            
            customer_growth = db.query(
                func.date_trunc('day', Customer.created_at).label('date'),
                func.count(Customer.id).label('count')
            ).group_by('date').order_by('date').limit(30).all()
            
            return jsonify({
                'daily_stats': daily_stats,
                'call_distribution': [
                    {'status': status, 'count': count}
                    for status, count in call_distribution
                ],
                'customer_growth': [
                    {'date': date.strftime('%Y-%m-%d'), 'count': count}
                    for date, count in customer_growth
                ]
            })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@crm_bp.route('/calls')
def calls_page():
    """Calls management page"""
    return render_template('crm/calls.html')

@crm_bp.route('/customers')
def customers_page():
    """Customers management page"""
    return render_template('crm/customers.html')

@crm_bp.route('/call/<call_id>')
def call_detail_page(call_id):
    """Individual call detail page"""
    return render_template('crm/call_detail.html', call_id=call_id)

@crm_bp.route('/metrics')
def metrics_page():
    """Metrics and analytics page"""
    return render_template('crm/metrics.html')
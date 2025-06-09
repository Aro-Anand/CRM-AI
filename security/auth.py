# security/auth.py
import os
import jwt
import hashlib
import secrets
from datetime import datetime, timedelta
from functools import wraps
from flask import request, jsonify, current_app
import logging

logger = logging.getLogger(__name__)

class AuthManager:
    """Handle authentication and authorization"""
    
    def __init__(self):
        self.secret_key = os.getenv("JWT_SECRET_KEY", self._generate_secret_key())
        self.api_keys = self._load_api_keys()
        self.jwt_expiry_hours = int(os.getenv("JWT_EXPIRY_HOURS", "24"))
    
    def _generate_secret_key(self) -> str:
        """Generate a secret key if not provided"""
        key = secrets.token_urlsafe(32)
        logger.warning("Generated JWT secret key. Set JWT_SECRET_KEY environment variable for production.")
        return key
    
    def _load_api_keys(self) -> dict:
        """Load API keys from environment variables"""
        api_keys = {}
        
        # Master API key
        master_key = os.getenv("API_KEY")
        if master_key:
            api_keys["master"] = self._hash_api_key(master_key)
        
        # Additional API keys (format: API_KEY_NAME1=key1,API_KEY_NAME2=key2)
        additional_keys = os.getenv("API_KEYS", "").strip()
        if additional_keys:
            for key_pair in additional_keys.split(","):
                if "=" in key_pair:
                    name, key = key_pair.strip().split("=", 1)
                    api_keys[name.strip()] = self._hash_api_key(key.strip())
        
        logger.info(f"Loaded {len(api_keys)} API keys")
        return api_keys
    
    def _hash_api_key(self, api_key: str) -> str:
        """Hash API key for secure storage"""
        return hashlib.sha256(api_key.encode()).hexdigest()
    
    def verify_api_key(self, api_key: str) -> bool:
        """Verify API key"""
        if not api_key or not self.api_keys:
            return False
        
        hashed_key = self._hash_api_key(api_key)
        return hashed_key in self.api_keys.values()
    
    def generate_jwt_token(self, user_id: str, role: str = "user") -> str:
        """Generate JWT token"""
        payload = {
            'user_id': user_id,
            'role': role,
            'iat': datetime.utcnow(),
            'exp': datetime.utcnow() + timedelta(hours=self.jwt_expiry_hours)
        }
        
        token = jwt.encode(payload, self.secret_key, algorithm='HS256')
        return token
    
    def verify_jwt_token(self, token: str) -> dict:
        """Verify JWT token"""
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=['HS256'])
            return payload
        except jwt.ExpiredSignatureError:
            logger.warning("JWT token expired")
            return None
        except jwt.InvalidTokenError as e:
            logger.warning(f"Invalid JWT token: {e}")
            return None
    
    def require_api_key(self, f):
        """Decorator to require API key authentication"""
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Check if authentication is disabled
            if os.getenv("DISABLE_AUTH", "false").lower() == "true":
                return f(*args, **kwargs)
            
            # Get API key from header or query parameter
            api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
            
            if not api_key:
                return jsonify({'error': 'API key required'}), 401
            
            if not self.verify_api_key(api_key):
                logger.warning(f"Invalid API key attempt from {request.remote_addr}")
                return jsonify({'error': 'Invalid API key'}), 401
            
            return f(*args, **kwargs)
        
        return decorated_function
    
    def require_jwt_token(self, f):
        """Decorator to require JWT token authentication"""
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Check if authentication is disabled
            if os.getenv("DISABLE_AUTH", "false").lower() == "true":
                return f(*args, **kwargs)
            
            # Get token from Authorization header
            auth_header = request.headers.get('Authorization')
            if not auth_header or not auth_header.startswith('Bearer '):
                return jsonify({'error': 'JWT token required'}), 401
            
            token = auth_header.split(' ')[1]
            payload = self.verify_jwt_token(token)
            
            if not payload:
                return jsonify({'error': 'Invalid or expired token'}), 401
            
            # Add user info to request context
            request.current_user = payload
            return f(*args, **kwargs)
        
        return decorated_function
    
    def require_role(self, required_role: str):
        """Decorator to require specific role"""
        def decorator(f):
            @wraps(f)
            def decorated_function(*args, **kwargs):
                if not hasattr(request, 'current_user'):
                    return jsonify({'error': 'Authentication required'}), 401
                
                user_role = request.current_user.get('role', 'user')
                if user_role != required_role and user_role != 'admin':
                    return jsonify({'error': 'Insufficient permissions'}), 403
                
                return f(*args, **kwargs)
            
            return decorated_function
        return decorator

class RateLimiter:
    """Simple in-memory rate limiter"""
    
    def __init__(self):
        self.requests = {}
        self.cleanup_interval = 300  # 5 minutes
    
    def is_allowed(self, identifier: str, limit: int, window: int) -> tuple[bool, dict]:
        """
        Check if request is allowed
        
        Args:
            identifier: Unique identifier (IP, user_id, etc.)
            limit: Number of requests allowed
            window: Time window in seconds
        
        Returns:
            (is_allowed, rate_limit_info)
        """
        now = datetime.utcnow()
        cutoff = now - timedelta(seconds=window)
        
        # Clean old requests
        if identifier in self.requests:
            self.requests[identifier] = [
                req_time for req_time in self.requests[identifier]
                if req_time > cutoff
            ]
        else:
            self.requests[identifier] = []
        
        current_requests = len(self.requests[identifier])
        
        rate_limit_info = {
            'limit': limit,
            'remaining': max(0, limit - current_requests),
            'reset_time': (now + timedelta(seconds=window)).isoformat(),
            'window': window
        }
        
        if current_requests >= limit:
            return False, rate_limit_info
        
        # Add current request
        self.requests[identifier].append(now)
        rate_limit_info['remaining'] -= 1
        
        return True, rate_limit_info
    
    def rate_limit(self, limit: int = 100, window: int = 3600, per: str = "ip"):
        """
        Rate limiting decorator
        
        Args:
            limit: Number of requests allowed
            window: Time window in seconds
            per: Rate limit per ('ip', 'user', 'api_key')
        """
        def decorator(f):
            @wraps(f)
            def decorated_function(*args, **kwargs):
                # Determine identifier
                if per == "ip":
                    identifier = request.remote_addr
                elif per == "user" and hasattr(request, 'current_user'):
                    identifier = request.current_user.get('user_id', request.remote_addr)
                elif per == "api_key":
                    identifier = request.headers.get('X-API-Key', request.remote_addr)
                else:
                    identifier = request.remote_addr
                
                allowed, rate_info = self.is_allowed(identifier, limit, window)
                
                if not allowed:
                    response = jsonify({
                        'error': 'Rate limit exceeded',
                        'rate_limit': rate_info
                    })
                    response.status_code = 429
                    response.headers['X-RateLimit-Limit'] = str(limit)
                    response.headers['X-RateLimit-Remaining'] = str(rate_info['remaining'])
                    response.headers['X-RateLimit-Reset'] = rate_info['reset_time']
                    return response
                
                # Add rate limit headers
                response = f(*args, **kwargs)
                if hasattr(response, 'headers'):
                    response.headers['X-RateLimit-Limit'] = str(limit)
                    response.headers['X-RateLimit-Remaining'] = str(rate_info['remaining'])
                    response.headers['X-RateLimit-Reset'] = rate_info['reset_time']
                
                return response
            
            return decorated_function
        return decorator

# Global instances
auth_manager = AuthManager()
rate_limiter = RateLimiter()

# Convenience decorators
require_api_key = auth_manager.require_api_key
require_jwt_token = auth_manager.require_jwt_token
require_role = auth_manager.require_role
rate_limit = rate_limiter.rate_limit

def init_auth():
    """Initialize authentication system"""
    logger.info("Authentication system initialized")
    if not auth_manager.api_keys:
        logger.warning("No API keys configured. Set API_KEY environment variable.")
    return auth_manager
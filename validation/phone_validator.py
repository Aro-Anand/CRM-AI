# validation/phone_validator.py
import phonenumbers
from phonenumbers import carrier, geocoder, timezone
from phonenumbers.phonenumberutil import NumberParseException
import logging
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

class PhoneValidator:
    """Enhanced phone number validation using phonenumbers library"""
    
    def __init__(self, default_region: str = "US"):
        self.default_region = default_region
    
    def validate_phone_number(self, phone_number: str, region: str = None) -> Dict:
        """
        Validate and format phone number
        
        Args:
            phone_number: The phone number to validate
            region: The region code (e.g., 'US', 'IN', 'GB')
        
        Returns:
            Dict with validation results and formatted number
        """
        result = {
            'is_valid': False,
            'formatted_national': None,
            'formatted_international': None,
            'formatted_e164': None,
            'country_code': None,
            'region': None,
            'carrier': None,
            'location': None,
            'timezone': None,
            'number_type': None,
            'error': None
        }
        
        try:
            # Parse the phone number
            region_code = region or self.default_region
            parsed_number = phonenumbers.parse(phone_number, region_code)
            
            # Check if the number is valid
            if phonenumbers.is_valid_number(parsed_number):
                result['is_valid'] = True
                
                # Format the number in different ways
                result['formatted_national'] = phonenumbers.format_number(
                    parsed_number, phonenumbers.PhoneNumberFormat.NATIONAL
                )
                result['formatted_international'] = phonenumbers.format_number(
                    parsed_number, phonenumbers.PhoneNumberFormat.INTERNATIONAL
                )
                result['formatted_e164'] = phonenumbers.format_number(
                    parsed_number, phonenumbers.PhoneNumberFormat.E164
                )
                
                # Get additional information
                result['country_code'] = parsed_number.country_code
                result['region'] = phonenumbers.region_code_for_number(parsed_number)
                
                # Get carrier information (if available)
                try:
                    result['carrier'] = carrier.name_for_number(parsed_number, 'en')
                except:
                    result['carrier'] = None
                
                # Get location information
                try:
                    result['location'] = geocoder.description_for_number(parsed_number, 'en')
                except:
                    result['location'] = None
                
                # Get timezone information
                try:
                    timezones = timezone.time_zones_for_number(parsed_number)
                    result['timezone'] = list(timezones) if timezones else None
                except:
                    result['timezone'] = None
                
                # Get number type
                try:
                    number_type = phonenumbers.number_type(parsed_number)
                    result['number_type'] = self._get_number_type_string(number_type)
                except:
                    result['number_type'] = None
                
                logger.info(f"Validated phone number: {phone_number} -> {result['formatted_e164']}")
            
            else:
                result['error'] = "Invalid phone number"
                logger.warning(f"Invalid phone number: {phone_number}")
                
        except NumberParseException as e:
            result['error'] = f"Parse error: {e}"
            logger.error(f"Phone number parse error: {phone_number} - {e}")
        except Exception as e:
            result['error'] = f"Validation error: {e}"
            logger.error(f"Phone number validation error: {phone_number} - {e}")
        
        return result
    
    def _get_number_type_string(self, number_type) -> str:
        """Convert number type enum to string"""
        type_mapping = {
            phonenumbers.PhoneNumberType.FIXED_LINE: "fixed_line",
            phonenumbers.PhoneNumberType.MOBILE: "mobile",
            phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE: "fixed_line_or_mobile",
            phonenumbers.PhoneNumberType.TOLL_FREE: "toll_free",
            phonenumbers.PhoneNumberType.PREMIUM_RATE: "premium_rate",
            phonenumbers.PhoneNumberType.SHARED_COST: "shared_cost",
            phonenumbers.PhoneNumberType.VOIP: "voip",
            phonenumbers.PhoneNumberType.PERSONAL_NUMBER: "personal_number",
            phonenumbers.PhoneNumberType.PAGER: "pager",
            phonenumbers.PhoneNumberType.UAN: "uan",
            phonenumbers.PhoneNumberType.VOICEMAIL: "voicemail",
            phonenumbers.PhoneNumberType.UNKNOWN: "unknown"
        }
        return type_mapping.get(number_type, "unknown")
    
    def is_mobile_number(self, phone_number: str, region: str = None) -> bool:
        """Check if the phone number is a mobile number"""
        try:
            region_code = region or self.default_region
            parsed_number = phonenumbers.parse(phone_number, region_code)
            
            if phonenumbers.is_valid_number(parsed_number):
                number_type = phonenumbers.number_type(parsed_number)
                return number_type in [
                    phonenumbers.PhoneNumberType.MOBILE,
                    phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE
                ]
        except:
            pass
        return False
    
    def format_for_dialing(self, phone_number: str, region: str = None) -> Optional[str]:
        """Format phone number for dialing (E164 format)"""
        validation_result = self.validate_phone_number(phone_number, region)
        if validation_result['is_valid']:
            return validation_result['formatted_e164']
        return None
    
    def get_validation_summary(self, phone_number: str, region: str = None) -> str:
        """Get a human-readable validation summary"""
        result = self.validate_phone_number(phone_number, region)
        
        if result['is_valid']:
            summary_parts = [
                f"✅ Valid phone number: {result['formatted_international']}",
                f"Region: {result['region']}",
                f"Type: {result['number_type'] or 'Unknown'}"
            ]
            
            if result['carrier']:
                summary_parts.append(f"Carrier: {result['carrier']}")
            
            if result['location']:
                summary_parts.append(f"Location: {result['location']}")
            
            return " | ".join(summary_parts)
        else:
            return f"❌ Invalid phone number: {result['error']}"

# Global validator instance
phone_validator = PhoneValidator()

def validate_phone(phone_number: str, region: str = None) -> Dict:
    """Quick validation function - use validate_phone_number instead"""
    return phone_validator.validate_phone_number(phone_number, region)

def validate_phone_number(phone_number: str, region: str = None) -> Dict:
    """
    Validate a phone number and return detailed validation information.
    This is the main validation function that should be used by other modules.
    
    Args:
        phone_number: The phone number to validate
        region: Optional region code (e.g., 'US', 'IN', 'GB')
    
    Returns:
        Dict containing validation results and formatted number
    """
    return phone_validator.validate_phone_number(phone_number, region)

def format_phone_for_dialing(phone_number: str, region: str = None) -> Optional[str]:
    """Quick formatting function for dialing"""
    return phone_validator.format_for_dialing(phone_number, region)

def is_valid_phone(phone_number: str, region: str = None) -> bool:
    """Quick validity check"""
    result = phone_validator.validate_phone_number(phone_number, region)
    return result['is_valid']
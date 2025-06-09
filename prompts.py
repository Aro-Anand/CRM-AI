from datetime import datetime

def get_time_based_greeting():
    hour = datetime.now().hour
    if 5 <= hour < 12:
        return "Good Morning"
    elif 12 <= hour < 17:
        return "Good Afternoon"
    else:
        return "Good Evening"

def get_welcome_message(customer_name: str, query: str = None) -> str:
    greeting = get_time_based_greeting()
    if query:
        return f"Hello {customer_name}, {greeting.lower()}! I understand you have a query about {query}. How can I help you with that?"
    return f"{greeting} {customer_name}! How can I assist you today?"

INSTRUCTIONS = """
You are an AI assistant called Fran-taiger. You are professional, helpful, and friendly.

You're speaking with a customer on a phone call. Your goal is to provide excellent customer service.

Always follow these guidelines:
1. Be respectful and courteous
2. Listen carefully to understand the customer's needs
3. Provide clear and concise information
4. Offer solutions and assistance
5. Maintain a positive and helpful tone
6. If customer say like send "Email me/Email/Mail/send Email/Send Gmail like that then you should call the email function.

When the call starts, you'll receive instructions to greet the customer by name with a time-appropriate greeting. Follow this instruction exactly.

For example:
- "Good Morning John! How can I assist you today?"
- "Hello Sarah, good afternoon! I understand you have a query about IT services. How can I help you with that?"
"""
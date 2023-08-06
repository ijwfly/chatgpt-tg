from app.storage.user_role import UserRole

# ChatGPT system prompts
gpt_mode = {
    'assistant': {
        'system': 'As an advanced chatbot Assistant, your primary goal is to assist users to the best of your ability.'
                  'This may involve answering questions, providing helpful information, or completing tasks based on user input. '
                  'In order to effectively assist users, it is important to be detailed and thorough in your responses. '
                  'Use examples and evidence to support your points and justify your recommendations or solutions. '
                  'Remember to always prioritize the needs and satisfaction of the user. '
                  'Your ultimate goal is to provide a helpful and enjoyable experience for the user.',
    },
    'coach': {
        'system': 'You\'re a business coach, your main task is to conduct high-quality coaching sessions, '
                  'and assist users to the best of your abilities. Listen carefully to what they say, ask questions, '
                  'and help in any way you can. Avoid giving advices, your ultimate goal is to help the user to find the right solution by himself. '
                  'Ask only one question a time.',
    }
}

# Mandatory settings
OPENAI_TOKEN = 'YOUR_TOKEN'
TELEGRAM_BOT_TOKEN = 'YOUR_TOKEN'

# Utility settings
OPENAI_CHAT_COMPLETION_TEMPERATURE = 0.3
MESSAGE_EXPIRATION_WINDOW = 60 * 60  # 1 hour

# User access settings
# You can setup default role for new users and minimum role needed to access bot, see UserRole enum
USER_ROLE_DEFAULT = UserRole.BASIC  # default role assigned to new users
USER_ROLE_BOT_ACCESS = UserRole.BASIC  # minimum role needed to access bot
USER_ROLE_CHOOSE_MODEL = UserRole.BASIC  # minimum role needed to choose model (gpt-3.5/gpt-4)

# Enables chat for user role management
# When enabled sends new user info to USER_ROLE_MANAGER_CHAT_ID with keyboard to choose user role
ENABLE_USER_ROLE_MANAGER_CHAT = False
USER_ROLE_MANAGER_CHAT_ID = -1

# Plugins settings
ENABLE_WOLFRAMALPHA = False
WOLFRAMALPHA_APPID = 'YOUR_TOKEN'

# Database settings
POSTGRES_HOST = 'postgres'
POSTGRES_PORT = 5432
POSTGRES_USER = 'postgres'
POSTGRES_PASSWORD = 'password'
POSTGRES_DATABASE = 'chatgpttg'



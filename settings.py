import pytz

from app.storage.user_role import UserRole

# ChatGPT system prompts
gpt_mode = {
    'assistant': {  # used by default for new users, you shouldn't delete this mode
        'system': 'As an advanced chatbot Assistant, your primary goal is to assist users to the best of your ability.'
                  'This may involve answering questions, providing helpful information, or completing tasks based on user input. '
                  'In order to effectively assist users, it is important to be detailed and thorough in your responses. '
                  'Use examples and evidence to support your points and justify your recommendations or solutions. '
                  'Remember to always prioritize the needs and satisfaction of the user. '
                  'Your ultimate goal is to provide a helpful and enjoyable experience for the user.'
    },
    'coach': {  # free to be deleted, also you can add new ones
        'system': 'You\'re a business coach, your main task is to conduct high-quality coaching sessions, '
                  'and assist users to the best of your abilities. Listen carefully to what they say, ask questions, '
                  'and help in any way you can. Avoid giving advices, your ultimate goal is to help the user to find the right solution by himself. '
                  'Ask only one question a time.',
    },
    'ai dungeon': {  # free to be deleted, also you can add new ones
        'system': 'You are the AI Dungeon game. Your task is to entertain user with role play. User creates a setup and you play role of the world and characters in it.',
     },
}

# Mandatory settings
OPENAI_TOKEN = ''
TELEGRAM_BOT_TOKEN = 'YOUR_TOKEN'
# Image proxy settings
# This proxy is used to send images to openai for GPT-4-Vision
# Image proxy URL is used to construct image url for openai, so you must add your IP here for vision to work
IMAGE_PROXY_URL = 'http://YOUR_IP_HERE'
# Change the port if you know what you're doing
IMAGE_PROXY_PORT = 8321

# Enables chat for user role management
# When enabled sends new user info to USER_ROLE_MANAGER_CHAT_ID with keyboard to choose user role
ENABLE_USER_ROLE_MANAGER_CHAT = False
USER_ROLE_MANAGER_CHAT_ID = -1

# OpenRouter.ai models
OPENROUTER_TOKEN = ''
OPENROUTER_BASE_URL = 'https://openrouter.ai/api/v1'

# User access settings, there are 4 levels of access, each: stranger, basic, advanced, admin
# You can setup default role for new users and minimum role needed to access bot, see UserRole enum
# Also you can setup which functions are available to each user role
USER_ROLE_DEFAULT = UserRole.BASIC  # default role assigned to new users
USER_ROLE_BOT_ACCESS = UserRole.BASIC  # minimum role needed to access bot
USER_ROLE_CHOOSE_MODEL = UserRole.BASIC  # minimum role needed to choose model (gpt-3.5/gpt-4)
USER_ROLE_STREAMING_ANSWERS = UserRole.BASIC  # minimum role needed to use streaming gpt responses
USER_ROLE_IMAGE_GENERATION = UserRole.BASIC  # minimum role needed to generate images
USER_ROLE_TTS = UserRole.BASIC  # minimum role needed to use text-to-speech
USER_ROLE_RAG = UserRole.BASIC  # minimum role needed to use RAG

# Plugins settings
ENABLE_WOLFRAMALPHA = False
WOLFRAMALPHA_APPID = 'YOUR_TOKEN'

# Utility settings
OPENAI_BASE_URL = 'https://api.openai.com/v1'
OPENAI_CHAT_COMPLETION_TEMPERATURE = 0.3
MESSAGE_EXPIRATION_WINDOW = 60 * 60  # 1 hour
POSTGRES_TIMEZONE = pytz.timezone('UTC')
SUCCESSIVE_FUNCTION_CALLS_LIMIT = 12  # limit of successive function calls that model can make

# Database settings
# Change these if you know what you're doing
POSTGRES_HOST = 'postgres'
POSTGRES_PORT = 5432
POSTGRES_USER = 'postgres'
POSTGRES_PASSWORD = 'password'
POSTGRES_DATABASE = 'chatgpttg'

# Additional Image proxy settings
# Change these if you know what you're doing
IMAGE_PROXY_BIND_HOST = '0.0.0.0'
IMAGE_PROXY_BIND_PORT = 8321

# Vectara RAG settings
# this feature is highly experimental and not recommended to be used in it's current state
# currently it even doesn't have instructions on how to setup, use it only if you feel experimenalish
# maybe it will be removed or redone in the future
VECTARA_RAG_ENABLED = False
VECTARA_CUSTOMER_ID = -1
VECTARA_API_KEY = 'YOUR KEY'
VECTARA_CORPUS_ID = -1

# chatgpt-tg

This GitHub repository contains the implementation of a telegram bot, designed to facilitate seamless interaction with GPT-3.5 and GPT-4, state-of-the-art language models by OpenAI.  

🔥 **GPT-4o support (with vision)**  
🔥 **Custom OpenAI API compatible endpoints support (see `app/llm_models.py` for example of using WizardLM-2 8x22b via OpenRouter.ai)**  
🔥 **DALL-E 3 Image generation support**

🔑 **Key Features**

1. **Model Support**: all OpenAI models are supported out of the box. Also you can add OpenAI API compatible endpoints by adding them to `app/llm_models.py`
2. **Image Generation**: You can ask bot to generate images using DALL-E 3 model, use bot just like official chatgpt app.
3. **Dynamic Dialog Management**: The bot automatically manages the context of the conversation, eliminating the need for the user to manually reset the context using the /reset command. You still can reset dialog manually if needed.
4. **Automatic Context Summarization**: In case the context size exceeds the model's maximum limit, the bot automatically summarizes the context to ensure the continuity of the conversation.
5. **Function calling support**: You can embed functions within the bot. This allows the GPT to invoke these functions when needed, based on the context. `app/context/function_manager.py` file for more details.
6. **Sub-dialogue Mechanism**: When you reply to a message, the bot only looks at that specific conversation thread, making it easier to manage multiple discussions at once.
7. **Voice Recognition**: The bot is capable of transcribing voice messages, allowing users to use speech as context or prompt for ChatGPT.
8. **API Usage Tracking**: The bot includes a function that tracks and provides information about the usage of the OpenAI API. This allows users to monitor and manage their API usage costs.
9. **Context Window Size Customization**: You can setup maximum context window size for each model in `app/context/context_manager.py` file. When context size exceeds this limit, bot will automatically summarize context.
10. **Access Control**: The bot includes a feature for access control. Each user is assigned a role (stranger, basic, advanced, admin), and depending on the role, they gain access to the bot. Role management is carried out through a messaging mechanism, with inline buttons sent to the admin for role changes.

🔧 **Installation**

To get this bot up and running, follow these steps:

1. Copy `settings_local.py.example` to `settings_local.py` and fill in your values:
   ```bash
   cp settings_local.py.example settings_local.py
   ```
2. Set `TELEGRAM_BOT_TOKEN` and `OPENAI_TOKEN` in `settings_local.py`.
3. Set `IMAGE_PROXY_URL` to your server IP / hostname in `settings_local.py`.
4. (optional) Set `USER_ROLE_MANAGER_CHAT_ID` and `ENABLE_USER_ROLE_MANAGER_CHAT = True` for access control.
5. (optional) Set `USER_ROLE_*` variables to desired roles.
6. Run `docker-compose up -d` in the root directory of the project.

All settings from `settings.py` can be overridden in `settings_local.py`. This file is gitignored, so your secrets and environment-specific values are never committed. See `settings_local.py.example` for a full list of available options.

**Docker Compose overrides**

For development, copy `docker-compose.override.yml.example` to `docker-compose.override.yml`:
```bash
cp docker-compose.override.yml.example docker-compose.override.yml
```
This adds a sleep-loop entrypoint (instead of running the bot), exposes the postgres port, and starts pgweb. Docker Compose merges the override file automatically.

You can also customize postgres credentials via a `.env` file (see `.env.example`).

**Adding custom LLM models**

You can add extra models without modifying `app/llm_models.py` by setting `EXTRA_MODELS` in `settings_local.py`:
```python
from app.llm_models import LLModel, LLMPrice, LLMContextConfiguration, LLMCapabilities
from app.openai_helpers.llm_client import OpenAISpecificAsyncOpenAIClient

EXTRA_MODELS = [
    LLModel(
        model_name='my-local-model',
        model_readable_name='My Local Model',
        api_key='not-needed',
        base_url='http://localhost:1234/v1',
        context_configuration=LLMContextConfiguration(
            short_term_memory_tokens=8192,
            summary_length=2048,
            hard_max_context_size=13312,
        ),
        capabilities=LLMCapabilities(
            streaming_responses=True,
        ),
    ),
]
```

This gives you full access to all model parameters: `model_price` (with `LLMPrice`), `capabilities` (with `LLMCapabilities`), `api_client` (e.g. `OpenAISpecificAsyncOpenAIClient`, `AnthropicAsyncClient`), `minimum_user_role`, etc.

<details>
<summary>Migrating from dict-based EXTRA_MODELS</summary>

If you used the old dict format, replace dicts with `LLModel(...)` calls and nested dicts with their dataclass equivalents:

```python
# Old format (still works, but deprecated):
EXTRA_MODELS = [
    {
        'model_name': 'my-model',
        'api_key': 'key',
        'context_configuration': {
            'short_term_memory_tokens': 8192,
            'summary_length': 2048,
            'hard_max_context_size': 13312,
        },
    },
]

# New format:
from app.llm_models import LLModel, LLMContextConfiguration

EXTRA_MODELS = [
    LLModel(
        model_name='my-model',
        api_key='key',
        context_configuration=LLMContextConfiguration(
            short_term_memory_tokens=8192,
            summary_length=2048,
            hard_max_context_size=13312,
        ),
    ),
]
```
</details>

If you've done optional steps, when you send your first message to the bot, you will get a management message with your telegram id and info. You can use this message to setup your role as admin.

**HTTP API for external message injection**

The bot includes an optional HTTP API that lets external systems inject messages into conversations. The bot processes them through the full LLM pipeline and sends responses to Telegram.

Use cases: external workers reacting to events (monitoring, CI/CD, webhooks), async tool results from long-running tasks.

To enable, add to `settings_local.py`:
```python
HTTP_API_ENABLED = True
HTTP_API_PORT = 8080
HTTP_API_SECRET = 'your-hmac-secret-for-jwt'
```

Generate a JWT token and send a request:
```bash
TOKEN=$(python -c "import jwt; print(jwt.encode({'user_id': None}, 'your-hmac-secret-for-jwt', algorithm='HS256'))")

# Fire-and-forget
curl -X POST http://localhost:8080/api/v1/inject \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"chat_id": 123456789, "text": "Alert: server CPU at 95%"}'

# Wait for LLM response
curl -X POST http://localhost:8080/api/v1/inject \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"chat_id": 123456789, "text": "Summarize this", "wait_for_response": true}'
```

Supports text, images, and linked mode (subdialog via `reply_to_message_id`). See `specs/HTTP_API.md` for full documentation.

🤖 **Commands**
```
/reset - reset current dialog
/usage - show usage for current month
/models - open models menu
/settings - open settings menu
/text2speech - generate voice message from message (last message or replied)
/usage_all - show usage for all users
```
These commands will provide additional interaction control for the bot users. You can find most settings in settings menu, commands are just shortcuts for them.


🧪 **Running Tests**

The project has e2e tests that exercise the full message pipeline with real PostgreSQL but mocked LLM and Telegram APIs.

**Local (recommended for development):**

```bash
./scripts/test.sh -v
```

This starts a test PostgreSQL container, runs pytest on the host, and stops the container when done. All pytest arguments are forwarded — for example:

```bash
./scripts/test.sh -v -k "test_reset"         # run a specific test
./scripts/test.sh -v --tb=long               # verbose tracebacks
```

**Fully in Docker:**

```bash
./scripts/test_docker.sh
```

Builds the app image, starts PostgreSQL + test runner in Docker, and tears everything down after. Useful for CI or clean-room runs.

**Manual setup (if you need persistent postgres for debugging):**

```bash
docker compose -f docker-compose.test.yml up -d postgres_test
POSTGRES_HOST=localhost POSTGRES_PORT=15432 pytest tests/ -v
docker compose -f docker-compose.test.yml down
```

See `specs/E2E_TESTS.md` for details on test architecture and covered scenarios.

🔄 **Upgrading from previous versions**

<details>
<summary>Migrating from settings.py to settings_local.py</summary>

Previously all configuration was edited directly in `settings.py`, which caused merge conflicts on every `git pull`. Now your overrides live in `settings_local.py` (gitignored).

**Quick migration:**

```bash
# 1. Your current settings.py becomes your local config
cp settings.py settings_local.py

# 2. Reset settings.py to defaults — no more merge conflicts
git checkout settings.py

# Done! The bot works exactly the same.
```

Optionally, clean up `settings_local.py` by removing unchanged defaults — you can see what you actually changed with:
```bash
git diff HEAD -- settings_local.py settings.py
```

</details>

<details>
<summary>Migrating custom models from llm_models.py</summary>

If you added custom models directly in `app/llm_models.py`, move them to `EXTRA_MODELS` in `settings_local.py`. The `LLModel(...)` syntax is identical:

```bash
# See what you changed in llm_models.py
git diff HEAD -- app/llm_models.py
```

Copy your `LLModel(...)` blocks into `settings_local.py`:
```python
from app.llm_models import LLModel, LLMPrice, LLMContextConfiguration, LLMCapabilities
from app.openai_helpers.llm_client import OpenAISpecificAsyncOpenAIClient

EXTRA_MODELS = [
    # paste your LLModel(...) entries here — same syntax as in llm_models.py
]
```

Then reset the file:
```bash
git checkout app/llm_models.py
```

</details>

<details>
<summary>Model list changes</summary>

Deprecated models (gpt-4, gpt-4-turbo, gpt-4o, gpt-4o-mini) were removed from the built-in list. GPT-4.1 is now the default model — a migration will automatically switch all users to it.

If you need any of the removed models, add them back via `EXTRA_MODELS` in `settings_local.py` (see "Adding custom LLM models" above). All conversations and usage history are preserved.

</details>

⚠️ **Troubleshooting**

If you have any issues with the bot, please create an issue in this repository. I will try to help you as soon as possible.  

Here are some typical issues and solutions:  
- ```Error code: 400 - {'error': {'message': 'Invalid image.', 'type': 'invalid_request_error' ...}}``` - This error usually occurs when openai cannot access the image. Make sure you set up the `IMAGE_PROXY_URL` variable correctly with your server IP / hostname. 
You can try to open this url in your browser to check if it works. Also you can debug the setup by looking at `chatgpttg.message` table in postgres, there will be message with image url. You can try to open this url in your browser to check if it works.
- ```Error code: 400 - {'error': {'message': 'Invalid content type. image_url is only supported by certain models.', 'type': 'invalid_request_error' ...}}``` - This error usually occurs when you have image in your context, but current model doesn't support vision. You can try to change model to gpt-4-vision-preview or reset your context with /reset command.

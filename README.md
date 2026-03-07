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
EXTRA_MODELS = [
    {
        'model_name': 'my-local-model',
        'model_readable_name': 'My Local Model',
        'api_key': 'not-needed',
        'base_url': 'http://localhost:1234/v1',
        'context_configuration': {
            'short_term_memory_tokens': 8192,
            'summary_length': 2048,
            'hard_max_context_size': 13312,
        },
        'capabilities': {
            'streaming_responses': True,
        },
    },
]
```

If you've done optional steps, when you send your first message to the bot, you will get a management message with your telegram id and info. You can use this message to setup your role as admin.

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

⚠️ **Troubleshooting**

If you have any issues with the bot, please create an issue in this repository. I will try to help you as soon as possible.  

Here are some typical issues and solutions:  
- ```Error code: 400 - {'error': {'message': 'Invalid image.', 'type': 'invalid_request_error' ...}}``` - This error usually occurs when openai cannot access the image. Make sure you set up the `IMAGE_PROXY_URL` variable correctly with your server IP / hostname. 
You can try to open this url in your browser to check if it works. Also you can debug the setup by looking at `chatgpttg.message` table in postgres, there will be message with image url. You can try to open this url in your browser to check if it works.
- ```Error code: 400 - {'error': {'message': 'Invalid content type. image_url is only supported by certain models.', 'type': 'invalid_request_error' ...}}``` - This error usually occurs when you have image in your context, but current model doesn't support vision. You can try to change model to gpt-4-vision-preview or reset your context with /reset command.

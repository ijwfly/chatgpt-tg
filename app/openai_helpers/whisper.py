from app.openai_helpers.utils import OpenAIAsync


async def get_audio_speech_to_text(filename, temperature=0):
    with open(filename, 'rb') as audio_file:
        transcript = await OpenAIAsync.instance().audio.transcriptions.create(
            file=audio_file, model="whisper-1", temperature=temperature
        )
    return transcript.text

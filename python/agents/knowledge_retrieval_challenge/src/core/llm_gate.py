import base64
import io
import logging
from PIL import Image

# Import existing clients
from src.core.client import client as google_client
import anthropic
from google import genai
from src.config import ANTHROPIC_API_KEY, DEEPSEEK_API_KEY

logger = logging.getLogger(__name__)

if ANTHROPIC_API_KEY:
    anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
else:
    anthropic_client = None

if DEEPSEEK_API_KEY:
    deepseek_client = anthropic.Anthropic(
        api_key=DEEPSEEK_API_KEY,
        base_url="https://api.deepseek.com/anthropic"
    )
else:
    deepseek_client = None

def encode_image_base64(image: Image.Image) -> str:
    buffered = io.BytesIO()
    # Convert image to RGB if it has an alpha channel to avoid saving issues with JPEG, or just save as PNG
    if image.mode in ('RGBA', 'LA') or (image.mode == 'P' and 'transparency' in image.info):
        image = image.convert('RGB')
    image.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode('utf-8')

def generate_content_with_gate(provider: str, model_id: str, text_prompt: str, images: list = None, system_instruction: str = None, temperature: float = 0.0) -> str:
    """
    Unified LLM Gate for routing requests to Google or Anthropic.
    Handles multimodal payload translation automatically.
    """
    if provider.lower() == "google":
        contents = []
        if images:
            contents.extend(images)
        contents.append(text_prompt)
        
        config_kwargs = {"temperature": temperature}
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction
            
        response = google_client.models.generate_content(
            model=model_id,
            contents=contents,
            config=genai.types.GenerateContentConfig(**config_kwargs)
        )
        return response.text.strip()
        
    elif provider.lower() == "anthropic":
        if not anthropic_client:
            raise ValueError("Anthropic client is not initialized. Check ANTHROPIC_API_KEY.")
            
        content_block = []
        if images:
            for img in images:
                base64_image = encode_image_base64(img)
                content_block.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": base64_image
                    }
                })
        
        content_block.append({
            "type": "text",
            "text": text_prompt
        })
        
        kwargs = {
            "model": model_id,
            "max_tokens": 8192,
            "temperature": temperature,
            "messages": [
                {"role": "user", "content": content_block}
            ]
        }
        
        if system_instruction:
            kwargs["system"] = system_instruction
            
        response = anthropic_client.messages.create(**kwargs)
        text_block = next((b for b in response.content if b.type == "text"), None)
        return text_block.text.strip() if text_block else ""
        
    elif provider.lower() == "deepseek":
        if not deepseek_client:
            raise ValueError("DeepSeek client is not initialized. Check DEEPSEEK_API_KEY.")

        content_block = []
        if images:
            for img in images:
                base64_image = encode_image_base64(img)
                content_block.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": base64_image
                    }
                })

        content_block.append({
            "type": "text",
            "text": text_prompt
        })

        kwargs = {
            "model": model_id,
            "max_tokens": 8192,
            "temperature": temperature,
            "messages": [
                {"role": "user", "content": content_block}
            ]
        }

        if system_instruction:
            kwargs["system"] = system_instruction

        response = deepseek_client.messages.create(**kwargs)
        text_block = next((b for b in response.content if b.type == "text"), None)
        return text_block.text.strip() if text_block else ""

    else:
        raise ValueError(f"Unknown LLM provider: {provider}")

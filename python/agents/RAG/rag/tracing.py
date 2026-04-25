import os
import warnings

from arize.otel import register
from dotenv import load_dotenv
from openinference.instrumentation.google_adk import GoogleADKInstrumentor
from opentelemetry import trace

load_dotenv()


def instrument_adk_with_arize() -> trace.Tracer | None:
    """Instrument the ADK with Arize Phoenix tracing.

    Returns a Tracer if tracing was set up successfully, or None if either
    the required credentials are missing or setup fails.  Tracing is
    optional — the agent works normally without it.

    Required .env variables:
        ARIZE_SPACE_ID  — base64-encoded space ID from the Arize UI URL,
                          e.g. app.arize.com/organizations/ORG/spaces/ID
        ARIZE_API_KEY   — Arize API key
    Optional:
        ARIZE_PROJECT_NAME — display name in the Arize UI (default: adk-rag-agent)
    """
    space_id = os.getenv("ARIZE_SPACE_ID", "").strip()
    api_key = os.getenv("ARIZE_API_KEY", "").strip()

    if not space_id:
        warnings.warn(
            "ARIZE_SPACE_ID is not set — tracing disabled. "
            "Set it to the base64-encoded space ID from your Arize account URL.",
            stacklevel=2,
        )
        return None

    if not api_key:
        warnings.warn("ARIZE_API_KEY is not set — tracing disabled.", stacklevel=2)
        return None

    try:
        tracer_provider = register(
            space_id=space_id,
            api_key=api_key,
            project_name=os.getenv("ARIZE_PROJECT_NAME", "adk-rag-agent"),
        )
        GoogleADKInstrumentor().instrument(tracer_provider=tracer_provider)
        return tracer_provider.get_tracer(__name__)
    except Exception as e:
        warnings.warn(
            f"Arize tracing setup failed ({e}) — continuing without tracing.",
            stacklevel=2,
        )
        return None

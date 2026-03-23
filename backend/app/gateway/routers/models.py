from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.gateway.route_cache import cache_get, cache_set
from deerflow.config import get_app_config

router = APIRouter(prefix="/api", tags=["models"])


class ModelResponse(BaseModel):
    """Response model for model information."""

    name: str = Field(..., description="Unique identifier for the model")
    display_name: str | None = Field(None, description="Human-readable name")
    description: str | None = Field(None, description="Model description")
    supports_thinking: bool = Field(default=False, description="Whether model supports thinking mode")
    supports_reasoning_effort: bool = Field(default=False, description="Whether model supports reasoning effort")


class ModelsListResponse(BaseModel):
    """Response model for listing all models."""

    models: list[ModelResponse]


@router.get(
    "/models",
    response_model=ModelsListResponse,
    summary="List All Models",
    description="Retrieve a list of all available AI models configured in the system.",
)
async def list_models() -> ModelsListResponse | dict:
    """List all available models from configuration."""
    cached = await cache_get("/api/models")
    if cached is not None:
        return cached

    config = get_app_config()
    models = [
        ModelResponse(
            name=model.name,
            display_name=model.display_name,
            description=model.description,
            supports_thinking=model.supports_thinking,
            supports_reasoning_effort=model.supports_reasoning_effort,
        )
        for model in config.models
    ]
    result = ModelsListResponse(models=models)
    await cache_set("/api/models", result.model_dump())
    return result


@router.get(
    "/models/{model_name}",
    response_model=ModelResponse,
    summary="Get Model Details",
    description="Retrieve detailed information about a specific AI model by its name.",
)
async def get_model(model_name: str) -> ModelResponse | dict:
    """Get a specific model by name."""
    cache_path = f"/api/models/{model_name}"
    cached = await cache_get(cache_path)
    if cached is not None:
        return cached

    config = get_app_config()
    model = config.get_model_config(model_name)
    if model is None:
        raise HTTPException(status_code=404, detail=f"Model '{model_name}' not found")

    result = ModelResponse(
        name=model.name,
        display_name=model.display_name,
        description=model.description,
        supports_thinking=model.supports_thinking,
        supports_reasoning_effort=model.supports_reasoning_effort,
    )
    await cache_set(cache_path, result.model_dump())
    return result

"""
Clean base agent interface for Sirpi.
All agents inherit from this for consistency.
"""

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Type, TypeVar
from pydantic import BaseModel
from google import genai
from google.genai import types as genai_types
import os


T = TypeVar('T', bound=BaseModel)


class AgentResult(BaseModel):
    """Standard result wrapper for all agents."""
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = {}


class BaseAgent(ABC):
    """
    Abstract base class for all Sirpi agents.
    Enforces consistent interface and provides shared utilities.
    
    Note: Subclasses should implement their own specific methods
    (e.g., analyze(), generate()) rather than execute(), as each
    agent has different input/output requirements.
    """
    
    def __init__(self, model: str = "gemini-2.5-flash", temperature: float = 0.2):
        """
        Initialize agent with Gemini model.
        
        Args:
            model: Gemini model identifier
            temperature: Sampling temperature (0.0 = deterministic, 1.0 = creative)
        """
        self.logger = logging.getLogger(self.__class__.__name__)
        self.model = model
        self.temperature = temperature
        self._client = None
    
    @property
    def client(self) -> genai.Client:
        """Lazy-loaded Gemini client (shared across agent lifecycle)."""
        if self._client is None:
            self._client = genai.Client(
                vertexai=os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "TRUE") == "TRUE",
                project=os.getenv("GOOGLE_CLOUD_PROJECT"),
                location=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
            )
        return self._client
    
    @abstractmethod
    def get_system_instruction(self) -> str:
        """
        Return the system instruction for this agent.
        Must be implemented by subclasses.
        """
        pass
    
    async def _generate_structured(
        self,
        prompt: str,
        response_schema: Type[T],
        temperature: Optional[float] = None
    ) -> T:
        """
        Generate structured output using Pydantic schema.
        
        Args:
            prompt: User prompt
            response_schema: Pydantic model class for response validation
            temperature: Override default temperature
            
        Returns:
            Validated Pydantic model instance
        """
        temp = temperature if temperature is not None else self.temperature
        
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    temperature=temp,
                    response_mime_type="application/json",
                    response_schema=response_schema,
                    system_instruction=self.get_system_instruction()
                )
            )
            
            # Parse and validate
            import json
            data = json.loads(response.text)
            return response_schema(**data)
            
        except Exception as e:
            self.logger.error(f"Structured generation failed: {e}", exc_info=True)
            raise ValueError(f"{self.__class__.__name__} failed: {str(e)}")
    
    async def _generate_text(
        self,
        prompt: str,
        temperature: Optional[float] = None
    ) -> str:
        """
        Generate raw text output.
        
        Args:
            prompt: User prompt
            temperature: Override default temperature
            
        Returns:
            Raw text response
        """
        temp = temperature if temperature is not None else self.temperature
        
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    temperature=temp,
                    system_instruction=self.get_system_instruction()
                )
            )
            
            return response.text.strip()
            
        except Exception as e:
            self.logger.error(f"Text generation failed: {e}", exc_info=True)
            raise ValueError(f"{self.__class__.__name__} failed: {str(e)}")
    
    def _log_execution(self, stage: str, message: str):
        """Helper for consistent logging format."""
        self.logger.info(f"[{stage.upper()}] {message}")

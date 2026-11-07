"""Request validation and public state models for the gateway."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import settings

Language = Literal["auto", "pt", "pt-PT", "pt-BR", "en", "fr"]
OutputLanguage = Literal["en", "pt-PT", "pt-BR", "fr"]
SummaryLanguage = Literal["en", "pt-PT", "pt-BR"]


class InputConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["pdf", "text"] = "pdf"
    document_id: str | None = Field(default=None, max_length=64)
    text: str | None = None
    language: Language = "auto"
    start_page: int | None = Field(default=None, ge=1)
    end_page: int | None = Field(default=None, ge=1)
    selected_pages: list[int] | None = Field(default=None, max_length=2000)
    ocr_enabled: bool = False
    ignore_notes: bool = False
    ignore_figure_descriptions: bool = False

    @model_validator(mode="after")
    def validate_input(self) -> "InputConfig":
        if self.type == "pdf":
            if not self.document_id:
                raise ValueError("A document_id is required for PDF input.")
            if self.text and self.text.strip():
                raise ValueError("Direct text cannot be combined with PDF input.")
        else:
            if not self.text or not self.text.strip():
                raise ValueError("Text input requires non-empty text.")
            if len(self.text) > settings.max_text_chars:
                raise ValueError(
                    f"Direct text exceeds the {settings.max_text_chars}-character limit."
                )
            if self.document_id or self.start_page or self.end_page or self.selected_pages:
                raise ValueError("Page selection is only available for PDF input.")
            if self.ignore_notes or self.ignore_figure_descriptions:
                raise ValueError(
                    "Note and figure filtering is only available for PDF input."
                )
        if self.selected_pages is not None:
            if not self.selected_pages:
                raise ValueError("selected_pages cannot be empty.")
            if any(page < 1 for page in self.selected_pages):
                raise ValueError("Page numbers must be 1-based.")
            if len(set(self.selected_pages)) > 2000:
                raise ValueError("At most 2000 pages can be selected.")
        if (
            self.start_page is not None
            and self.end_page is not None
            and self.end_page < self.start_page
        ):
            raise ValueError("end_page must be greater than or equal to start_page.")
        if (
            self.start_page is not None
            and self.end_page is not None
            and self.end_page - self.start_page > 2000
        ):
            raise ValueError("The page range is too large.")
        return self

    def resolved_pages(self, page_count: int) -> list[int]:
        if self.type == "text":
            return []
        if self.selected_pages:
            pages = sorted({page for page in self.selected_pages})
        elif self.start_page is not None and self.end_page is not None:
            pages = list(range(self.start_page, self.end_page + 1))
        elif self.start_page is not None:
            pages = [self.start_page]
        else:
            pages = list(range(1, page_count + 1))
        if not pages:
            raise ValueError("At least one page must be selected.")
        if any(page > page_count for page in pages):
            raise ValueError(f"Page numbers must be between 1 and {page_count}.")
        return pages


class TranslationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    source_language: Language = "auto"
    target_language: OutputLanguage = "en"


class SummaryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    length: Literal["short", "medium", "long"] = "medium"
    # None follows the language of the text being summarized.
    language: SummaryLanguage | None = None


class TTSConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    voice: str = Field(default="pt-PT-tugao", min_length=1, max_length=80)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    format: Literal["mp3", "wav"] = "mp3"
    normalize_audio: bool = False
    preserve_phrases: bool = True
    ignore_leading_numbers: bool = False
    text_source: Literal["content", "summary"] = "content"


class JobCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="", max_length=200)
    input: InputConfig
    translation: TranslationConfig = Field(default_factory=TranslationConfig)
    summary: SummaryConfig = Field(default_factory=SummaryConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)

    @model_validator(mode="after")
    def validate_pipeline(self) -> "JobCreateRequest":
        if self.tts.enabled and self.tts.text_source == "summary" and not self.summary.enabled:
            raise ValueError("TTS cannot narrate a summary when summarization is disabled.")
        if self.summary.enabled:
            if self.translation.enabled and self.translation.target_language == "fr":
                raise ValueError(
                    "Summarization is available only for English and Portuguese text. "
                    "Disable summarization or choose an English/Portuguese translation target."
                )
            if not self.translation.enabled and self.input.language == "fr":
                raise ValueError(
                    "Summarization is available only for English and Portuguese text. "
                    "Enable translation or disable summarization for French input."
                )
        if not self.translation.enabled and not self.summary.enabled and not self.tts.enabled:
            # Extraction alone is still a valid result, but it is usually a
            # mistake when the request comes from the guided workflow.
            pass
        return self

    def public_config(self) -> dict[str, Any]:
        return self.model_dump()


class TextUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=settings.max_text_chars)

    @field_validator("text")
    @classmethod
    def require_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("The edited text cannot be empty.")
        return value


class DirectTranslateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=settings.max_text_chars)
    source_language: Language = "auto"
    target_language: OutputLanguage = "en"


class DirectSummaryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=settings.max_text_chars)
    language: SummaryLanguage = "en"
    source_language: Language = "auto"
    length: Literal["short", "medium", "long"] = "medium"


class DirectTTSRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=settings.max_text_chars)
    voice: str = Field(min_length=1, max_length=80)
    language: str = Field(default="", max_length=20)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    format: Literal["mp3", "wav"] = "mp3"
    normalize_audio: bool = False
    preserve_phrases: bool = True
    ignore_leading_numbers: bool = False

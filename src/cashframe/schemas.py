import base64
import binascii
from io import BytesIO
import math
from typing import Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator


class ExactModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class EvidenceFrame(ExactModel):
    timestamp_sec: float = Field(ge=0, allow_inf_nan=False)
    image_base64: str

    @field_validator('image_base64')
    @classmethod
    def decodable_jpeg(cls, value):
        try:
            raw = base64.b64decode(value, validate=True)
            with Image.open(BytesIO(raw)) as image:
                if image.format != 'JPEG':
                    raise ValueError('Evidence must be a JPEG image')
                image.load()
        except (ValueError, binascii.Error, OSError, Image.DecompressionBombError) as exc:
            raise ValueError('Evidence must contain a complete base64-encoded JPEG') from exc
        return value


class Violation(ExactModel):
    type: str
    timecode_sec: float = Field(ge=0, allow_inf_nan=False)
    description: str
    evidence_frames: list[EvidenceFrame] = Field(min_length=1,max_length=3)

    @field_validator('type', 'description')
    @classmethod
    def nonblank_event_text(cls, value):
        if not value.strip():
            raise ValueError('Event type and description must contain text')
        return value


class CompletedResult(ExactModel):
    job_id: str
    status: Literal['done'] = 'done'
    verdict: Literal['no_violation','violation']
    violations: list[Violation]

    @model_validator(mode='after')
    def consistent_verdict(self, info: ValidationInfo):
        if bool(self.violations) != (self.verdict == 'violation'):
            raise ValueError('Verdict must agree with the event list')
        if info.context and 'duration_sec' in info.context:
            duration = info.context['duration_sec']
            if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
                raise ValueError('Source duration must be finite and positive')
            for event in self.violations:
                if event.timecode_sec >= duration or any(frame.timestamp_sec >= duration for frame in event.evidence_frames):
                    raise ValueError('Event and evidence timestamps must be within the source video')
        return self

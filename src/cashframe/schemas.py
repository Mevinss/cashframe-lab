from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExactModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class EvidenceFrame(ExactModel):
    timestamp_sec: float = Field(ge=0, allow_inf_nan=False)
    image_base64: str


class Violation(ExactModel):
    type: str
    timecode_sec: float = Field(ge=0, allow_inf_nan=False)
    description: str
    evidence_frames: list[EvidenceFrame] = Field(min_length=1,max_length=3)


class CompletedResult(ExactModel):
    job_id: str
    status: Literal['done'] = 'done'
    verdict: Literal['no_violation','violation']
    violations: list[Violation]

    @model_validator(mode='after')
    def consistent_verdict(self):
        if bool(self.violations) != (self.verdict == 'violation'):
            raise ValueError('Verdict must agree with the event list')
        return self

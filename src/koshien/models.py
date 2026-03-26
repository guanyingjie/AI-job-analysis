"""Pydantic schemas for the six structured information modules."""

from __future__ import annotations

from pydantic import BaseModel, Field


class BasicProfile(BaseModel):
    full_name: str = ""
    short_name: str = ""
    prefecture: str = ""
    city: str = ""
    founded_year: int | None = None
    baseball_club_year: int | None = None
    motto: str | None = None


class KoshienRecord(BaseModel):
    spring_appearances: int | None = None
    spring_wins: list[str] = Field(default_factory=list)
    spring_runners_up: list[str] = Field(default_factory=list)
    summer_appearances: int | None = None
    summer_wins: list[str] = Field(default_factory=list)
    summer_runners_up: list[str] = Field(default_factory=list)
    other_titles: list[str] = Field(default_factory=list)
    special_achievements: list[str] = Field(default_factory=list)


class ManagerInfo(BaseModel):
    current_manager: str | None = None
    current_tenure: str | None = None
    philosophy: str | None = None
    play_style_tags: list[str] = Field(default_factory=list)
    notable_past_managers: list[dict] = Field(default_factory=list)


class CultureAndRivals(BaseModel):
    famous_cheer_songs: list[str] = Field(default_factory=list)
    cheer_description: str | None = None
    region_difficulty: str | None = None
    rivals: list[dict] = Field(default_factory=list)


class FamousAlumni(BaseModel):
    active_pros: list[dict] = Field(default_factory=list)
    retired_legends: list[dict] = Field(default_factory=list)
    other_notable: list[dict] = Field(default_factory=list)


class CurrentFocus(BaseModel):
    last_koshien: str | None = None
    last_result: str | None = None
    tournament_in_progress: bool = False
    draft_prospects: list[dict] = Field(default_factory=list)


class SchoolReport(BaseModel):
    """Complete Koshien school scouting report."""

    profile: BasicProfile = Field(default_factory=BasicProfile)
    records: KoshienRecord = Field(default_factory=KoshienRecord)
    manager: ManagerInfo = Field(default_factory=ManagerInfo)
    culture: CultureAndRivals = Field(default_factory=CultureAndRivals)
    alumni: FamousAlumni = Field(default_factory=FamousAlumni)
    current: CurrentFocus = Field(default_factory=CurrentFocus)
    generated_at: str = ""
    sources_used: list[str] = Field(default_factory=list)

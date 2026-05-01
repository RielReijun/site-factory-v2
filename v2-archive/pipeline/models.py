from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ContactInfo:
    phone: str = ""
    email: str = ""
    address: str = ""
    website: str = ""


@dataclass
class Theme:
    primary: str = "#2563eb"
    secondary: str = "#0f172a"
    accent: str = "#f59e0b"
    background: str = "#f8fafc"
    text: str = "#111827"
    font_heading: str = "Inter"
    font_body: str = "Inter"


@dataclass
class Page:
    slug: str
    title: str
    description: str = ""


@dataclass
class Section:
    type: str
    variant: str = "default"
    content: dict[str, Any] = field(default_factory=dict)


@dataclass
class SitePlan:
    slug: str
    company_name: str
    source_url: str = ""
    contact: ContactInfo = field(default_factory=ContactInfo)
    theme: Theme = field(default_factory=Theme)
    pages: list[Page] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

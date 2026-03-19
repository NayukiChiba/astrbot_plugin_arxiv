"""ArXiv API async client.

Provides search and latest-paper fetching via the arXiv Atom API using
aiohttp + feedparser.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime

import aiohttp
import feedparser

ARXIV_API_URL = "http://export.arxiv.org/api/query"

# Courtesy delay between API calls (arXiv policy).
_API_DELAY_SECONDS = 3.0

# Common arXiv categories with human-readable labels.
ARXIV_CATEGORIES: dict[str, str] = {
    # Computer Science
    "cs.AI": "Artificial Intelligence",
    "cs.CL": "Computation and Language (NLP)",
    "cs.CV": "Computer Vision",
    "cs.LG": "Machine Learning",
    "cs.CR": "Cryptography and Security",
    "cs.DB": "Databases",
    "cs.DS": "Data Structures and Algorithms",
    "cs.IR": "Information Retrieval",
    "cs.NE": "Neural and Evolutionary Computing",
    "cs.RO": "Robotics",
    "cs.SE": "Software Engineering",
    "cs.SI": "Social and Information Networks",
    # Electrical Engineering / Systems
    "eess.AS": "Audio and Speech Processing",
    "eess.IV": "Image and Video Processing",
    "eess.SP": "Signal Processing",
    # Mathematics
    "math.CO": "Combinatorics",
    "math.OC": "Optimization and Control",
    "math.PR": "Probability",
    "math.ST": "Statistics Theory",
    # Statistics
    "stat.ML": "Machine Learning (Statistics)",
    "stat.ME": "Methodology",
    # Physics
    "physics.comp-ph": "Computational Physics",
    "quant-ph": "Quantum Physics",
    "hep-th": "High Energy Physics - Theory",
    "cond-mat.stat-mech": "Statistical Mechanics",
    # Quantitative Biology
    "q-bio.BM": "Biomolecules",
    "q-bio.GN": "Genomics",
    # Quantitative Finance
    "q-fin.ST": "Statistical Finance",
}


@dataclass
class ArxivPaper:
    """Represents a single arXiv paper."""

    arxiv_id: str = ""
    title: str = ""
    authors: list[str] = field(default_factory=list)
    abstract: str = ""
    categories: list[str] = field(default_factory=list)
    published: str = ""
    updated: str = ""
    pdf_url: str = ""
    abs_url: str = ""

    @property
    def published_date(self) -> str:
        """Return a human-readable published date string."""
        try:
            dt = datetime.fromisoformat(self.published.replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d")
        except (ValueError, AttributeError):
            return self.published


def _build_search_query(
    *,
    query: str = "",
    categories: list[str] | None = None,
    tags: list[str] | None = None,
) -> str:
    """Build an arXiv API search query string.

    Combines free-text query, category filters, and keyword tags into a single
    query string accepted by the arXiv API.
    """
    parts: list[str] = []

    if query:
        parts.append(f"all:{query}")

    if categories:
        cat_parts = [f"cat:{cat}" for cat in categories]
        if len(cat_parts) == 1:
            parts.append(cat_parts[0])
        else:
            parts.append("(" + "+OR+".join(cat_parts) + ")")

    if tags:
        tag_parts = [f"all:{tag}" for tag in tags]
        if len(tag_parts) == 1:
            parts.append(tag_parts[0])
        else:
            parts.append("(" + "+OR+".join(tag_parts) + ")")

    return "+AND+".join(parts) if parts else "all:*"


def _parse_feed_entry(entry: dict) -> ArxivPaper:
    """Parse a single feedparser entry into an ArxivPaper."""
    # Extract arXiv ID from the entry id URL
    arxiv_id = entry.get("id", "")
    if "/abs/" in arxiv_id:
        arxiv_id = arxiv_id.split("/abs/")[-1]

    # Extract PDF link
    pdf_url = ""
    for link in entry.get("links", []):
        if link.get("type") == "application/pdf":
            pdf_url = link.get("href", "")
            break
    if not pdf_url and arxiv_id:
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"

    # Extract authors
    authors = [a.get("name", "") for a in entry.get("authors", [])]

    # Extract categories
    categories = [t.get("term", "") for t in entry.get("tags", [])]

    return ArxivPaper(
        arxiv_id=arxiv_id,
        title=entry.get("title", "").replace("\n", " ").strip(),
        authors=authors,
        abstract=entry.get("summary", "").strip(),
        categories=categories,
        published=entry.get("published", ""),
        updated=entry.get("updated", ""),
        pdf_url=pdf_url,
        abs_url=entry.get("id", ""),
    )


async def search_papers(
    query: str,
    *,
    max_results: int = 5,
    timeout: int = 30,
) -> list[ArxivPaper]:
    """Search arXiv by free-text query.

    Args:
        query: Search keywords.
        max_results: Maximum number of results to return.
        timeout: HTTP request timeout in seconds.

    Returns:
        List of ArxivPaper objects.
    """
    search_query = _build_search_query(query=query)
    return await _fetch_papers(
        search_query=search_query,
        max_results=max_results,
        sort_by="relevance",
        timeout=timeout,
    )


async def get_latest_papers(
    *,
    categories: list[str] | None = None,
    tags: list[str] | None = None,
    max_results: int = 5,
    timeout: int = 30,
) -> list[ArxivPaper]:
    """Fetch the latest papers matching categories and/or tags.

    Args:
        categories: ArXiv category codes, e.g. ["cs.AI", "cs.LG"].
        tags: Additional keyword tags for fuzzy matching.
        max_results: Maximum number of results.
        timeout: HTTP request timeout in seconds.

    Returns:
        List of ArxivPaper objects, sorted by submission date descending.
    """
    search_query = _build_search_query(categories=categories, tags=tags)
    return await _fetch_papers(
        search_query=search_query,
        max_results=max_results,
        sort_by="submittedDate",
        timeout=timeout,
    )


async def _fetch_papers(
    *,
    search_query: str,
    max_results: int,
    sort_by: str,
    timeout: int,
) -> list[ArxivPaper]:
    """Low-level fetch from the arXiv API."""
    params = {
        "search_query": search_query,
        "start": 0,
        "max_results": max_results,
        "sortBy": sort_by,
        "sortOrder": "descending",
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(
            ARXIV_API_URL,
            params=params,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            resp.raise_for_status()
            text = await resp.text()

    # Courtesy delay for arXiv API
    await asyncio.sleep(_API_DELAY_SECONDS)

    feed = feedparser.parse(text)
    papers: list[ArxivPaper] = []
    for entry in feed.entries:
        papers.append(_parse_feed_entry(entry))

    return papers

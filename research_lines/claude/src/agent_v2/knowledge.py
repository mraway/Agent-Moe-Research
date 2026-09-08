"""Deterministic local knowledge retrieval for the Atlas v2 support agent."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_TOKEN = re.compile(r"[a-z0-9]+")
_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "can",
    "do",
    "does",
    "for",
    "how",
    "i",
    "is",
    "it",
    "my",
    "of",
    "the",
    "to",
    "what",
    "when",
    "will",
}


def _terms(text: str) -> set[str]:
    return {token for token in _TOKEN.findall(text.lower()) if token not in _STOP_WORDS}


def _nonempty_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class KnowledgeArticle:
    article_id: str
    title: str
    revision: str
    topics: tuple[str, ...]
    content: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "KnowledgeArticle":
        topics = value.get("topics")
        if not isinstance(topics, list) or not topics:
            raise ValueError("article topics must be a non-empty list")
        normalized_topics = tuple(_nonempty_string(topic, "article topic") for topic in topics)
        return cls(
            article_id=_nonempty_string(value.get("article_id"), "article_id"),
            title=_nonempty_string(value.get("title"), "article title"),
            revision=_nonempty_string(value.get("revision"), "article revision"),
            topics=normalized_topics,
            content=_nonempty_string(value.get("content"), "article content"),
        )


@dataclass(frozen=True)
class KnowledgeHit:
    article: KnowledgeArticle
    score: int

    def as_dict(self) -> dict[str, object]:
        return {
            "article_id": self.article.article_id,
            "title": self.article.title,
            "revision": self.article.revision,
            "topics": list(self.article.topics),
            "content": self.article.content,
            "score": self.score,
        }


class SupportKnowledgeBase:
    """A frozen lexical retriever with deterministic ranking and provenance."""

    def __init__(self, *, kb_version: str, articles: tuple[KnowledgeArticle, ...]) -> None:
        self.kb_version = _nonempty_string(kb_version, "kb_version")
        if not articles:
            raise ValueError("knowledge base must contain at least one article")
        article_ids = [article.article_id for article in articles]
        if len(article_ids) != len(set(article_ids)):
            raise ValueError("knowledge article IDs must be unique")
        self.articles = articles

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SupportKnowledgeBase":
        if value.get("schema_version") != 1:
            raise ValueError("knowledge base schema_version must be 1")
        raw_articles = value.get("articles")
        if not isinstance(raw_articles, list):
            raise ValueError("knowledge base articles must be a list")
        return cls(
            kb_version=_nonempty_string(value.get("kb_version"), "kb_version"),
            articles=tuple(KnowledgeArticle.from_dict(article) for article in raw_articles),
        )

    @classmethod
    def load(cls, path: Path) -> "SupportKnowledgeBase":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def search(self, query: str, *, limit: int = 3) -> tuple[KnowledgeHit, ...]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("knowledge query must be a non-empty string")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 3:
            raise ValueError("knowledge search limit must be an integer from 1 to 3")
        query_terms = _terms(query)
        if not query_terms:
            return ()

        ranked: list[KnowledgeHit] = []
        normalized_query = " ".join(_TOKEN.findall(query.lower()))
        for article in self.articles:
            title_terms = _terms(article.title)
            topic_terms = _terms(" ".join(article.topics))
            content_terms = _terms(article.content)
            score = (
                5 * len(query_terms & topic_terms)
                + 3 * len(query_terms & title_terms)
                + len(query_terms & content_terms)
            )
            normalized_document = " ".join(
                _TOKEN.findall(f"{article.title} {' '.join(article.topics)} {article.content}".lower())
            )
            if normalized_query and normalized_query in normalized_document:
                score += 3
            if score > 0:
                ranked.append(KnowledgeHit(article=article, score=score))

        ranked.sort(key=lambda hit: (-hit.score, hit.article.article_id))
        return tuple(ranked[:limit])

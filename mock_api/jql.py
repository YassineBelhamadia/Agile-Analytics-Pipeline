"""A deliberately small JQL subset for the mock search endpoints.

Supported, and nothing else:

    project = KEY [AND issuetype = X] [AND status = X] [ORDER BY created [ASC|DESC]]

Clauses can appear in any order and repeat (repeats are ANDed, so
conflicting values simply match nothing). Field names, keywords and
values are case-insensitive, as in Jira. Values are a bare word or a
single- or double-quoted string, so `status = "In Progress"` works.

Anything outside the subset — OR, NOT, IN, !=, functions, other fields,
other sort keys — is rejected with a JqlError naming what was not
understood, rather than being half-interpreted. A query must contain at
least one restriction: Jira refuses unbounded queries on the enhanced
search endpoint, and so does this mock.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from simulator.issues import Issue

SUPPORTED = (
    "Supported JQL in this mock: project = KEY, issuetype = X and status = X, "
    "joined with AND, optionally followed by ORDER BY created [ASC|DESC]."
)

FIELDS = ("project", "issuetype", "status")

_TOKEN = re.compile(
    r"""\s*(?:
        (?P<string>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')
      | (?P<op>!=|!~|>=|<=|=|~|<|>|\(|\)|,)
      | (?P<word>[^\s=!~<>(),"']+)
    )""",
    re.VERBOSE,
)


class JqlError(ValueError):
    """The query is outside the supported subset, or names unknown values."""


@dataclass(frozen=True)
class JqlQuery:
    """A parsed query: equality restrictions plus an optional sort.

    Attributes:
        restrictions: (field, canonical value) pairs, all of which must hold.
        descending: Sort direction for `created`.
        ordered: Whether the query had an ORDER BY clause at all.
    """

    restrictions: tuple[tuple[str, str], ...]
    descending: bool
    ordered: bool = False

    def canonical(self) -> str:
        """A normalized rendering, identical for equivalent queries.

        Used to bind page tokens to the query they were issued for, so
        `status = done` and `STATUS = "Done"` share tokens.
        """
        where = " AND ".join(f"{f} = {v}" for f, v in sorted(self.restrictions))
        return f"{where} ORDER BY created {'DESC' if self.descending else 'ASC'}"


def _tokens(jql: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    position = 0
    while position < len(jql):
        if jql[position:].strip() == "":
            break
        match = _TOKEN.match(jql, position)
        if match is None or match.end() == position:
            raise JqlError(f"Could not parse the JQL near '{jql[position:].strip()}'. {SUPPORTED}")
        kind = match.lastgroup
        text = match.group(kind)
        if kind == "string":
            text = re.sub(r"\\(.)", r"\1", text[1:-1])
        tokens.append((kind, text))
        position = match.end()
    return tokens


def parse(
    jql: str | None,
    allowed_values: dict[str, Iterable[str]],
    require_restriction: bool = True,
) -> JqlQuery:
    """Parse a query in the supported subset.

    Args:
        jql: The raw JQL string.
        allowed_values: For each supported field, the values that exist.
            Values are matched case-insensitively and returned in the
            spelling given here.
        require_restriction: Reject queries with no restriction. True
            for the search endpoint; False where the result set is
            already bounded, as for a sprint's issues.

    Returns:
        The parsed query.

    Raises:
        JqlError: With a message suitable for Jira's errorMessages.
    """
    tokens = _tokens(jql or "")
    canonical_values = {
        field: {value.lower(): value for value in values} for field, values in allowed_values.items()
    }
    restrictions: list[tuple[str, str]] = []
    descending = False
    ordered = False
    i = 0

    def peek_word(offset: int = 0) -> str | None:
        if i + offset < len(tokens) and tokens[i + offset][0] == "word":
            return tokens[i + offset][1].lower()
        return None

    while i < len(tokens):
        if peek_word() == "order":
            break
        if restrictions:
            if peek_word() != "and":
                raise JqlError(
                    f"Unsupported JQL: expected AND or ORDER BY but found '{tokens[i][1]}'. {SUPPORTED}"
                )
            i += 1
        if i + 2 >= len(tokens):
            raise JqlError(f"Incomplete JQL clause at the end of the query. {SUPPORTED}")
        field_token, op_token, value_token = tokens[i], tokens[i + 1], tokens[i + 2]
        field = field_token[1].lower()
        if field_token[0] != "word" or field not in FIELDS:
            raise JqlError(f"Unsupported JQL field '{field_token[1]}'. {SUPPORTED}")
        if op_token != ("op", "="):
            raise JqlError(f"Unsupported JQL operator '{op_token[1]}' for field '{field}'. {SUPPORTED}")
        if value_token[0] not in ("word", "string"):
            raise JqlError(f"Unsupported JQL value '{value_token[1]}' for field '{field}'. {SUPPORTED}")
        value = value_token[1]
        if value.lower() in ("and", "or", "not", "order"):
            raise JqlError(f"Missing value for field '{field}'. {SUPPORTED}")
        canonical = canonical_values[field].get(value.lower())
        if canonical is None:
            raise JqlError(f"The value '{value}' does not exist for the field '{field}'.")
        restrictions.append((field, canonical))
        i += 3

    if i < len(tokens):
        # ORDER BY created [ASC|DESC], and nothing after it.
        if peek_word(1) != "by":
            raise JqlError(f"Unsupported JQL: expected BY after ORDER. {SUPPORTED}")
        if peek_word(2) != "created":
            found = tokens[i + 2][1] if i + 2 < len(tokens) else "nothing"
            raise JqlError(f"Unsupported ORDER BY field '{found}'; only 'created' is supported. {SUPPORTED}")
        i += 3
        ordered = True
        if peek_word() in ("asc", "desc"):
            descending = peek_word() == "desc"
            i += 1
        if i < len(tokens):
            raise JqlError(f"Unsupported JQL after ORDER BY: '{tokens[i][1]}'. {SUPPORTED}")

    if require_restriction and not restrictions:
        raise JqlError(
            "Unbounded JQL queries are not allowed here. Please add a search restriction to your query."
        )
    return JqlQuery(tuple(restrictions), descending, ordered)


def matches(query: JqlQuery, issue: Issue, project_key: str) -> bool:
    """Whether an issue satisfies every restriction in the query.

    Args:
        query: The parsed query.
        issue: A ground-truth issue.
        project_key: The key of the issue's team (its Jira project).
    """
    actual = {"project": project_key, "issuetype": issue.issue_type, "status": issue.status}
    return all(actual[field] == value for field, value in query.restrictions)


def sort(query: JqlQuery, issues: list[Issue]) -> list[Issue]:
    """Order issues by created, ties broken by key so the order is total."""
    return sorted(issues, key=lambda i: (i.created, i.key), reverse=query.descending)

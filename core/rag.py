"""RAG (Retrieval-Augmented Generation): finds the hiring-guideline clauses that matter for a task.

Why this file exists:
- The agents must follow the company's hiring guidelines. Instead of pasting the
  whole document into every prompt, we split it into numbered clauses (§2.3, §4.4...)
  and send only the most relevant ones. The agents then cite those clause ids.
- Search uses BM25, a classic keyword-ranking formula, written here in plain Python.
  It needs no extra AI calls, works offline, and every result can be explained.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

# BM25 settings from spec section 7 (standard values).
BM25_K1: float = 1.5  # how quickly repeating a word stops adding to the score
BM25_B: float = 0.75  # how much long clauses are penalised
DEFAULT_TOP_K: int = 5

# Common words that carry no meaning for search.
STOP_WORDS: set[str] = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does", "for", "from",
    "has", "have", "if", "in", "into", "is", "it", "its", "may", "must", "no", "not", "of",
    "on", "or", "should", "so", "such", "than", "that", "the", "their", "them", "then",
    "there", "these", "they", "this", "to", "was", "were", "what", "when", "which", "who",
    "will", "with", "you", "your", "any", "all", "every", "each", "only", "also",
}


@dataclass
class Clause:
    """One numbered piece of the guidelines, e.g. §4.4 in section "Scoring rubric"."""

    id: str  # e.g. "§4.4"
    section_title: str  # e.g. "Scoring rubric"
    text: str


# ===========================================================================
# Splitting the guidelines into clauses
# ===========================================================================

SECTION_HEADING = re.compile(r"^#{2,}\s*(\d+)\.?\s+(.*)$")  # "## 4. Scoring rubric"
TITLE_HEADING = re.compile(r"^#\s+(.*)$")  # "# Corallia Living: Hiring Guidelines"
NUMBERED_CLAUSE = re.compile(r"^(\d+\.\d+)\.?\s+(.*)$")  # "4.4 Years of experience..."


def split_into_clauses(markdown: str) -> list[Clause]:
    """Split the guidelines into one clause per numbered item.

    Text under a section heading that has no number of its own (like section 1,
    "Purpose") becomes a section-level clause, e.g. §1. Text before the first
    section becomes §0, titled with the document title.
    """
    clauses: list[Clause] = []
    document_title = "Introduction"
    section_number = "0"
    section_title = document_title
    loose_lines: list[str] = []  # unnumbered text in the current section
    current: Clause | None = None

    def finish_section() -> None:
        """Save the section's unnumbered text (if any) as its own clause."""
        if loose_lines:
            clauses.append(Clause(f"§{section_number}", section_title, " ".join(loose_lines)))
        loose_lines.clear()

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if line == "":
            current = None  # a blank line ends a clause
            continue

        title_match = TITLE_HEADING.match(line)
        section_match = SECTION_HEADING.match(line)
        clause_match = NUMBERED_CLAUSE.match(line)

        if title_match and not line.startswith("##"):
            document_title = title_match.group(1).strip()
            section_title = document_title
        elif section_match:
            finish_section()
            section_number = section_match.group(1)
            section_title = section_match.group(2).strip()
            current = None
        elif clause_match:
            current = Clause(f"§{clause_match.group(1)}", section_title, clause_match.group(2).strip())
            clauses.append(current)
        elif current is not None:
            current.text += " " + line  # the clause continues on the next line
        else:
            loose_lines.append(line)

    finish_section()
    return clauses


# ===========================================================================
# Tokenizing: turning text into searchable words
# ===========================================================================


def simplify_word(word: str) -> str:
    """Strip simple plurals so "gaps" matches "gap" and "salaries" matches "salary"."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def tokenize(text: str) -> list[str]:
    """Lowercase, split into words, drop stop-words and strip plurals."""
    words = re.findall(r"[a-z0-9%]+", text.lower())
    tokens: list[str] = []
    for word in words:
        if word in STOP_WORDS:
            continue
        tokens.append(simplify_word(word))
    return tokens


# ===========================================================================
# BM25 search
# ===========================================================================


class GuidelineIndex:
    """A BM25 search index over the guideline clauses.

    Build it once with the guidelines text; rebuild it whenever the guidelines change.
    """

    def __init__(self, clauses: list[Clause]) -> None:
        self.clauses = clauses
        # Each clause is indexed with its section title, so a search for
        # "salary" also finds clauses in the "Salary and work authorisation" section.
        self.clause_tokens: list[list[str]] = []
        for clause in clauses:
            self.clause_tokens.append(tokenize(clause.section_title + " " + clause.text))

        self.average_length = self.calculate_average_length()
        self.document_frequency = self.count_document_frequency()

    def calculate_average_length(self) -> float:
        if not self.clause_tokens:
            return 0.0
        total = sum(len(tokens) for tokens in self.clause_tokens)
        return total / len(self.clause_tokens)

    def count_document_frequency(self) -> Counter:
        """For every word, how many clauses contain it at least once."""
        frequency: Counter = Counter()
        for tokens in self.clause_tokens:
            frequency.update(set(tokens))
        return frequency

    def inverse_document_frequency(self, word: str) -> float:
        """Rare words score higher than common ones (standard BM25 formula)."""
        clause_count = len(self.clause_tokens)
        containing = self.document_frequency.get(word, 0)
        return math.log((clause_count - containing + 0.5) / (containing + 0.5) + 1)

    def score_clause(self, query_tokens: list[str], clause_position: int) -> float:
        """The BM25 score of one clause for the query."""
        tokens = self.clause_tokens[clause_position]
        word_counts = Counter(tokens)
        length_ratio = len(tokens) / self.average_length if self.average_length else 1.0

        score = 0.0
        for word in query_tokens:
            count = word_counts.get(word, 0)
            if count == 0:
                continue
            top = count * (BM25_K1 + 1)
            bottom = count + BM25_K1 * (1 - BM25_B + BM25_B * length_ratio)
            score += self.inverse_document_frequency(word) * top / bottom
        return score

    def search(self, query: str, top_k: int = DEFAULT_TOP_K) -> list[Clause]:
        """Return the best-matching clauses for the query, best first.

        Clauses that share no words with the query are never returned.
        """
        query_tokens = tokenize(query)
        scored: list[tuple[float, int]] = []
        for position in range(len(self.clauses)):
            score = self.score_clause(query_tokens, position)
            if score > 0:
                scored.append((score, position))
        # Highest score first; on a tie, keep the document order.
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        return [self.clauses[position] for _score, position in scored[:top_k]]

    def get(self, clause_id: str) -> Clause | None:
        """Find a clause by its id, e.g. "§4.4". Used to show citation chips."""
        for clause in self.clauses:
            if clause.id == clause_id:
                return clause
        return None


def build_index(guidelines_markdown: str) -> GuidelineIndex:
    """Split the guidelines and build the search index in one step."""
    return GuidelineIndex(split_into_clauses(guidelines_markdown))


# ===========================================================================
# Formatting clauses for prompts and the trace
# ===========================================================================


def format_clause(clause: Clause) -> str:
    """e.g. "[§4.4] (Scoring rubric) A candidate below the minimum years..." """
    return f"[{clause.id}] ({clause.section_title}) {clause.text}"


def format_clauses_for_prompt(clauses: list[Clause]) -> str:
    """All retrieved clauses, one per line, ready to go inside <guidelines> tags."""
    return "\n".join(format_clause(clause) for clause in clauses)


def clause_ids(clauses: list[Clause]) -> list[str]:
    """Just the ids, e.g. ["§3.2", "§4.4"], for the trace ("retrieved §3.2, §4.4")."""
    return [clause.id for clause in clauses]

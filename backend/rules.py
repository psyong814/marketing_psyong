"""config/keyword_rules.yaml 로더."""

from dataclasses import dataclass

import yaml

from backend.settings import ROOT

RULES_FILE = ROOT / "config" / "keyword_rules.yaml"


@dataclass
class KeywordRules:
    blacklist: list[str]
    commercial_hints: list[str]

    def blacklisted(self, keyword: str) -> str | None:
        """금칙어가 들어 있으면 그 단어를 돌려준다."""
        text = keyword.replace(" ", "")
        for word in self.blacklist:
            if word.replace(" ", "") in text:
                return word
        return None

    def hint(self, keyword: str) -> str | None:
        for word in self.commercial_hints:
            if word in keyword:
                return word
        return None


def load_rules() -> KeywordRules:
    data = yaml.safe_load(RULES_FILE.read_text(encoding="utf-8")) or {}
    return KeywordRules(
        blacklist=[str(w) for w in data.get("blacklist", [])],
        commercial_hints=[str(w) for w in data.get("commercial_hints", [])],
    )

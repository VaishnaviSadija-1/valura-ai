import re
from typing import List, Tuple
from src.models.response import SafetyResult


class SafetyGuard:
    # All patterns compiled at class level for speed (not per-call)
    HARMFUL_CATEGORIES: List[Tuple[str, re.Pattern, str]] = [
        (
            "insider_trading",
            re.compile(
                r'\b(?:'
                r'insider\s+trad'
                r'|non[.\s-]?public\s+(?:info|data|material)'
                r'|material\s+non[.\s-]?public'
                r'|trade\s+on\s+(?:\w+\s+)?(?:tip|info|data|inside)\b'
                r'|i\s+have\s+(?:insider|confidential|non[.\s-]?public)'
                r'|tip\s+from\s+(?:an?\s+)?(?:insider|exec|executive|employee)'
                r')\b',
                re.IGNORECASE,
            ),
            "We can't assist with trading on material non-public information. This constitutes insider trading and is illegal in most jurisdictions.",
        ),
        (
            "market_manipulation",
            re.compile(
                r'\b(pump\s+and\s+dump|short\s+squeeze\s+(scheme|coordin)|spread\s+(false|fake)\s+(rumou?r|info|news)|'
                r'manipulat\s*(e|ing)?\s+the\s+(market|stock|price)|coordinate\s+(buying|selling)|front.?run(ning)?)\b',
                re.IGNORECASE,
            ),
            "We can't assist with market manipulation. This causes real harm to other investors and carries severe legal penalties.",
        ),
        (
            "money_laundering",
            re.compile(
                r'\b(launder|money\s+launder|wash\s+the\s+(money|funds|cash)|clean\s+the\s+(money|funds|cash)|'
                r'hide\s+(the\s+)?(money|funds|proceeds|origin)|conceal\s+(the\s+)?(source|origin|funds)|'
                r'structur(e|ing)\s+(the\s+)?(deposit|transaction|payment)|smurfing|shell\s+compan)\b',
                re.IGNORECASE,
            ),
            "We can't assist with structuring or concealing financial transactions. This is a serious criminal offence.",
        ),
        (
            "guaranteed_returns",
            re.compile(
                r'(?:'
                r'\bguarantee[d]?\b.{0,40}?\b(?:return|profit|gain|yield|income)s?\b'
                r'|\brisk[.\s-]free\s+(?:return|profit|investment|gain)'
                r'|\bzero[.\s-]risk\b'
                r'|\bcannot\s+lose\b|\bnever\s+lose\b|\balways\s+profit\b'
                r'|\b100%\s+(?:safe|guaranteed|certain)\s+(?:return|investment)'
                r')',
                re.IGNORECASE,
            ),
            "No investment can guarantee returns. Anyone claiming otherwise is misrepresenting the nature of financial markets.",
        ),
        (
            "reckless_leverage",
            re.compile(
                r'\b((borrow|leverage|margin).{0,20}(10x|20x|50x|100x|ten\s+times|twenty\s+times)|'
                r'all.?in\s+on\s+(margin|leverage|debt|loan)|max(imum)?\s+leverage.{0,20}(single|one\s+stock|bet)|'
                r'bet\s+everything\s+on)\b',
                re.IGNORECASE,
            ),
            "This level of leverage on a concentrated position creates catastrophic downside risk. We won't recommend this.",
        ),
    ]

    EDUCATIONAL_PATTERNS = re.compile(
        r'\b(explain|how\s+does|what\s+is|what\s+are|tell\s+me\s+about|teach\s+me|'
        r'i.?m\s+trying\s+to\s+understand|academically|historically|theoretically|'
        r'in\s+general|for\s+educational|why\s+is\s+it\s+(illegal|wrong|banned)|'
        r'how\s+do\s+regulators|what\s+are\s+the\s+(penalties|consequences|laws)|'
        r'documentary|research\s+(paper|purposes?)|learn\s+about|study)\b',
        re.IGNORECASE,
    )

    def check(self, query: str) -> SafetyResult:
        lowered = query.lower()

        for category, pattern, response_text in self.HARMFUL_CATEGORIES:
            if pattern.search(lowered):
                # Check for educational intent — if present, allow through
                if self.EDUCATIONAL_PATTERNS.search(query):
                    return SafetyResult(blocked=False)
                return SafetyResult(
                    blocked=True,
                    category=category,
                    response=response_text,
                )

        return SafetyResult(blocked=False)

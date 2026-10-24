"""Does the request text actually ask for deletion?  (multilingual, deterministic)

An optional LLM fallback is wired in llm.py for texts this classifier cannot decide;
by default the deterministic rules below decide every request.
"""
import re

DELETE_PATTERNS = [
    r'\bdelet', r'\berase', r'\berasure', r'\bpermanent(ly)?\b.*\b(account|deletion|delete)',
    r'\bclose (my|this|the) account', r'\bclosed\b', r'\bremove (my|this|the) (account|profile|name|personal)',
    r'\bremove (records|data|my data)', r'\bclean my personal', r'\bclose this account',
    r'\bwould like it closed', r'\bhave (it|its|the account)\b.*\b(removed|deleted)',
    # Spanish / Portuguese / French
    r'\beliminar\b', r'\beliminaci[oó]n', r'\bexclus[aã]o', r'\bexcluir\b', r'\bsuppression\b', r'\bsupprim',
    r'\bborrar', r'\bcerrar (mi|la) cuenta', r'\bapagar\b',
    # Japanese / Korean / Chinese
    r'削除', r'消去', r'退会', r'삭제', r'탈퇴', r'删除', r'注销',
]
NEGATED = [
    r"not asking for (account )?(deletion|erasure)", r"do not (close|delete|erase|remove)[^.]*", r"don't (delete|erase|remove|close)[^.]*",
    r"not a request to (erase|delete)[^.]*", r"without (removing|deleting|erasing)[^.]*", r"leave the account (active|open)",
    r"keep the account", r"(i )?(still|continue) (need|want|use)[^.]*",
]
NON_DELETE_INTENT = [
    r'out of date', r'promotional', r'download my', r'resetting the login', r"can't access", r'misspelled', r'copy of',
    r'what personal information', r'change the email', r'marketing lists only', r'replace it with',
]


def _strip_negations(t):
    for p in NEGATED:
        t = re.sub(p, ' ', t)
    return t


def classify(text):
    """Return (authorized: bool, rule: str).  Requests are treated as deletion audits unless they
    carry an explicit non-deletion intent and no un-negated deletion language."""
    t = (text or '').lower()
    if not t.strip():
        return False, 'empty'
    stripped = _strip_negations(t)
    positive = any(re.search(p, stripped) for p in DELETE_PATTERNS)
    intent = [p for p in NON_DELETE_INTENT if re.search(p, t)]
    if intent and not positive:
        return False, 'non-deletion:' + intent[0]
    if positive:
        return True, 'deletion-keyword'
    return True, 'audit-of-deletion-state'

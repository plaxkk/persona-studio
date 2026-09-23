"""Non-persona safety rules. These never impose vocabulary or tone preferences."""

import re
from scripts.x_signal import is_prompt_injection, strip_urls

# Boundary-match English OD; the upstream bare 'od' also matched model, code, today.
RISK = re.compile(
    r"(自杀|自残|自伤|轻生|遗书|想死|去死|安眠药|佐匹克隆|药物过量|开盒|人肉搜索|裸照|色情交易|未成年裸|杀人|制造炸弹|制作炸药|购买枪支|转发扩散|网暴|举报他全家|\bod\b|\bsuicid(?:e|al)\b|\bself[- ]harm\b|\bdoxx?ing\b)",
    re.I,
)


def is_high_risk(text: str) -> bool:
    return bool(RISK.search(strip_urls(text)))


def skip_source(text: str) -> bool:
    return is_prompt_injection(text) or is_high_risk(text)

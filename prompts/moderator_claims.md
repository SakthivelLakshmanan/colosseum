# Moderator: check one claim

Code has already confirmed that the claim cites evidence, that the evidence exists and isn't superseded, and that any number matches. Your job is the judgement code can't make: **does the cited evidence actually support the claim as worded?**

Return one of:
- **KEEP**: the evidence supports the claim as worded.
- **FLAG**: partly supported. For example it over-generalises (evidence covers mobile only; claim says all channels), relies only on a low-reliability source, or ignores a conflicting fact you can see. The claim stays, with your warning.
Only treat another fact as a conflict if it measures the same thing (same entity and same metric). Different measures, such as merchant NPS and borrower satisfaction, are not conflicts.
- **STRIKE**: the evidence doesn't support the claim, or contradicts it.

Never strike a blocking finding raised by an adviser allowed to block. If you doubt it, FLAG it and explain.

Claim:
{claim}

Cited evidence:
{evidence}

Other facts that may conflict:
{related_facts}

Reply as JSON: {{"claim_id": "...", "status": "KEEP|FLAG|STRIKE", "check": "supported|overgeneralised|contradicted|low_reliability|conflict", "reason": "one sentence"}}

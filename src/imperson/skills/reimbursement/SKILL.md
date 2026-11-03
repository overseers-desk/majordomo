---
name: reimbursement
description: Someone sends an invoice or receipt (photo or PDF) to be paid back, or asks about their reimbursements, expenses or weekly allowance.
---

This skill handles expense claims. The person is always the sender of the message; the tools act on them and no one else.

The business's rules for what qualifies are in `.imperson/reimbursement/rubric.md`, inside the folder of the business's files named in your system prompt. Read the attached file with Read, read the rubric, and judge the invoice against it.

Tools, served in-process as `mcp__imperson__*`:
- `allowance_left`: what the sender has left of this week's allowance.
- `find_duplicate(invoice_date, vendor, amount)`: whether this invoice was already claimed.
- `record_reimbursement(vendor, invoice_date, amount, currency, file)`: records the claim, works out whether it is within the allowance, overdrawn or over the limit, and posts a payment task with the file attached to the business's task space. It refuses duplicates and repeats.
- `record_not_reimbursed(vendor, reason)`: records a rejected invoice so it is not handled twice.

For every invoice, call `allowance_left` and read the rubric again, even when this conversation has done so before: both change. It says whether this person's invoices within the allowance are accepted by default or scrutinised. Accepted by default: an invoice the rubric counts as a business expense, judged from the invoice itself, is recorded without asking the person to explain it; one the rubric does not count is declined with the reason, not queried. Scrutinised: be satisfied from the rubric, and from the person where needed, before recording it. An invoice beyond what is left is scrutinised either way.

A qualifying invoice: check for a duplicate, record it, then tell the person the outcome and what is left of the allowance.

One photo or file may hold several invoices or receipts. Judge each on its own against the rubric, and record each with its own call: `record_reimbursement` for every one that qualifies, one at a time, so each is set against what the ones before it left of the allowance, and `record_not_reimbursed` for every one that does not. Then answer in one reply that lists each invoice with its vendor, date and amount and whether it was accepted or declined (with the reason), followed by the total accepted and what is left of the allowance.

An invoice that does not qualify: say why, in terms of the rubric, and what would qualify. Record it with `record_not_reimbursed`.

If the total or the date cannot be read with confidence, ask the person to confirm it; prefer a question to a guess, since the figure goes to accounts. Where the receipt shows both a total and a smaller amount actually charged, the amount charged is what was paid.

A file that is not an invoice (a screenshot, a photo sent for another reason) is not for this skill; answer whatever the person actually asked.

A question about allowance or past claims needs only `allowance_left`.

Reply with `reply`, in the person's own language and register, and keep it short: outcome first, then the balance.

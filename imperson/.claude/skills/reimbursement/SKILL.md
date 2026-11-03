---
name: reimbursement
description: Someone sends an invoice or receipt (photo or PDF) to be paid back, or asks about their reimbursements, expenses or weekly allowance.
---

This skill handles expense claims. The person is always the sender of the message; the tools act on them and no one else.

The business's rules for what qualifies are in the tenant's rubric file at `.imperson/reimbursement/rubric.md` (relative to the tenant path in your system prompt). Read the attached file with Read, read the rubric, and judge the invoice against it.

Tools, served in-process as `mcp__imperson__*`:
- `allowance_left`: what the sender has left of this week's allowance.
- `find_duplicate(invoice_date, vendor, amount)`: whether this invoice was already claimed.
- `record_reimbursement(vendor, invoice_date, amount, currency, file)`: records the claim, works out whether it is within the allowance, overdrawn or over the limit, and tasks someone in the accounts space with the file attached. It refuses duplicates and repeats.
- `record_not_reimbursed(vendor, reason)`: records a rejected invoice so it is not handled twice.

A qualifying invoice: check for a duplicate, record it, then tell the person the outcome and what is left of the allowance.

An invoice that does not qualify: say why, in terms of the rubric, and what would qualify. Record it with `record_not_reimbursed`.

If the total or the date cannot be read with confidence, ask the person to confirm it; prefer a question to a guess, since the figure goes to accounts.

A file that is not an invoice (a screenshot, a photo sent for another reason) is not for this skill; answer whatever the person actually asked.

A question about allowance or past claims needs only `allowance_left`.

Reply with `reply`, in the person's own language and register, and keep it short: outcome first, then the balance.

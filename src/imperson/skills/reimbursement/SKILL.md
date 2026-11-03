---
name: reimbursement
description: Someone sends an invoice or receipt (photo or PDF) to be paid back, or asks about their reimbursements, expenses or weekly allowance.
---

An expense is reimbursed if it is justifiable as a business expense. What is justifiable for this business is in the Justifiable section of `.imperson/reimbursement/rubric.md`, inside the folder of the business's files named in your system prompt. Read the file the person sent, and read the rubric again for every invoice: it changes.

`allowance_left` says whether the sender has a weekly allowance. A person with one is not expected to give a reason. A person without one is expected to say what each expense was for.

For each invoice:
- Justifiable: check `find_duplicate`, then `record_reimbursement`. For a person without an allowance, ask the reason first and pass it.
- Not justifiable under the rubric: tell them why and record it with `record_not_justifiable`.
- You cannot tell whether it is justifiable: ask the person. Do not decline it.

One photo may hold several invoices. Handle each on its own, one call each, and answer in one reply listing every invoice with its vendor, date, amount and what happened to it, then what is left of the allowance.

If a total or a date cannot be read, ask rather than guess. Where a receipt shows a total and a smaller amount charged, the amount charged is what was paid.

A file that is not an invoice is not for this skill: answer what the person asked.

Reply with `reply`, in the person's own language and register.

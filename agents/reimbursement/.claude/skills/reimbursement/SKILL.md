---
name: reimbursement
description: Read one invoice or receipt (a photo or a PDF) an employee sent for reimbursement, and judge it against the business's rubric of qualifying expenses. Answers in one JSON object.
---

The prompt names the file an employee sent, the text they sent with it, and the rubric file. Read the file and the rubric with `Read`.

- `is_invoice`: true when the file is an invoice or a receipt for a purchase. People also send screenshots, reports and photos for other reasons; for those it is false, and the other keys may be null.

Take from the invoice:

- `vendor`: the business that issued it, as printed.
- `invoice_date`: the date of purchase, `YYYY-MM-DD`; null when the document shows none.
- `amount`: the total paid, as a number, tax included.
- `currency`: the three-letter code; `AUD` when the document shows only a dollar sign.

Then judge it against the rubric, reading the employee's text only for what the purchase was for:

- `qualified`: true when the purchase is spending the rubric counts as the business's.
- `reason`: one sentence saying which part of the rubric decided it.
- `hint`: when not qualified, one sentence telling the employee what does qualify; otherwise null.

An invoice too unclear to read the total from is not qualified, and the reason says so.

Answer with the JSON object alone, holding exactly these eight keys, and no other text.

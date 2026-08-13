# Operating Instructions

## 1. Receipt Processing Workflow
When you receive a receipt image (via attachment):
1. **Analyze and Extract**: Extract Date, Vendor Name, Itemized Purchases, Cost/Total (Original Currency), Cost/Total (USD), Payment Method, and Category (Restaurant, Personal Care, Travel, Entertainment, etc.).
2. **Save Immediately**: Write or Append the extracted data to the monthly ledger (`pkm/vault/finance/YYYY-MM.md`).
    - *Fallback Note*: The `append` action fails if the file doesn't exist. If it's a new month, use `write` first. If `write` fails because the file exists, use `append`.
3. **Notify & Ask**: Reply to the user in the channel showing the exact data block that was saved.
4. **Follow Up (If needed)**: If critical data (Payment Method, Date, Category) was missing and saved as "Unknown (Pending Review)", explicitly ask the user for the missing information.

## 2. Ledger Format Requirements
Your appended data block MUST match this exact format:

```markdown
### YYYY-MM-DD: Vendor Name
**Category:** [Category] | **Payment Method:** [Payment Method]

| Item | Original Price ([Currency]) | USD Price |
| :--- | :--- | :--- |
| [Item 1] | [Original Price] | [USD Price] |
| **Total** | **[Original Total]** | **[USD Total]** |
```

## 3. Data Correction Workflow
If the user replies with missing information (e.g., "It was Visa" or "Category is Travel"):
1. Read the current month's ledger file using `obsidian`'s `read` action.
2. Locate the specific entry that says "Unknown (Pending Review)".
3. Update the entry with the new information.
4. Overwrite the file using the `obsidian`'s `overwrite` action.

## 4. Continuous Learning (Memory Trigger)
- You MUST immediately execute the `memory` skill upon concluding a task to log completion and user preferences.
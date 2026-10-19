# Operating Instructions

## 1. Receipt Processing Workflow
When you receive a receipt image (via attachment):
1. **Analyze and Extract**: Extract Date, Vendor Name, Itemized Purchases (discounts, coupons and returns as negative amounts), taxes and fees, tip, the printed Total, Currency, Payment Method, and Category (Restaurant, Personal Care, Travel, Entertainment, etc.).
2. **Save Immediately**: In the same step, call `ledger_append` with those fields. It converts to USD at the receipt date, checks the total, and appends the block to the monthly ledger (`pkm/wallet/finance/YYYY-MM.md`).
    - If it says the lines don't add up, re-read the receipt and call it again.
3. **Notify & Ask**: Reply to the user in the channel showing the exact data block that was saved.
4. **Follow Up (If needed)**: If critical data (Payment Method, Date, Category) was missing and saved as "Unknown (Pending Review)", explicitly ask the user for the missing information.

## 2. Data Correction Workflow
If the user replies with missing information (e.g., "It was Visa" or "Category is Travel"):
1. Locate the entry that says "Unknown (Pending Review)": in the block you saved, or with `filesystem`'s `read` of the current month's ledger.
2. Update it with one `filesystem` `replace_block`, putting the entry's `### date: vendor` header line in `old_block` so it matches only that entry.
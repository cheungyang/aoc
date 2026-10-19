# Operating Rules for Rio

1.  **One Workflow**: Every property evaluation and listing scan, scheduled or on request, follows the `real_estate_scout` skill. It owns your rules for screening, verdicts, archiving and dry weeks; the tools own the math. Apply them exactly.
2.  **Criteria Changes**: criteria.md is the user's file. When the user changes a constraint for good, or agrees to fix a line the screen couldn't read, edit `pkm/wallet/real-estate/criteria.md` one confirmed change at a time, and keep its "Label: value" lines (e.g. `- **Max HOA**: $200`); `property_screen` reads them on every call.

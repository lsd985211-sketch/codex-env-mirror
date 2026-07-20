# Test Plan

## Unit and Smoke Coverage

- Import package modules.
- Verify CLI help.
- Verify JSON status command returns a valid JSON object.
- Verify guarded send refuses to run without `--confirm-send SEND`.
- Verify guarded draft smoke refuses to run without `--confirm-smoke DRAFT`.
- Verify invalid chat row index is rejected before any click.
- Verify empty search query is rejected.
- Verify emoji and file picker smoke require explicit confirmation.
- Verify message prepare/send require explicit confirmation and non-empty text.

## E2E Boundary

The full GUI workflow requires a logged-in Windows Weixin desktop window. Tests
must not send messages automatically.

Manual smoke command when Weixin is open:

```powershell
cli-anything-weixin --json draft smoke "CLI_WEIXIN_DRAFT_TEST_DO_NOT_SEND" --confirm-smoke DRAFT
```

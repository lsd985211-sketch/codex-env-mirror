# Draft Area

This is the canonical storage area for draft artifacts. It is an artifact
store, not a task queue.

Every draft records three independent fields:

- `Content maturity`: whether the artifact is `draft` or `final`.
- `Workflow status`: such as `retained_reference` or `pending_review`.
- `Pending action`: the concrete next action, or `none`.

Directory presence never creates a closeout Review Card. A draft enters
closeout only through the existing Review Queue, whose item references the
artifact with `artifact_ref`.

When the user says to keep or leave a draft untouched, use
`Workflow status: retained_reference` and `Pending action: none`. When the user
asks for review, create a Review Queue item that references the draft; do not
copy the draft body into the queue.

Do not infer workflow state from a file name or from the word `draft`. Active
incidents and follow-up records belong to their owning queue or
`unresolved-items`, even if an old file name contains `draft`.

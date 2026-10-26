# Chat context and expression repair — 2026-10-26

Long chats could retain old assistant wording indefinitely. The old estimator
divided every language's character count by four, ignored the provider's saved
prompt measurement when deciding to compact, and counted the archived transcript
again after compaction. A production session had roughly 118k measured prompt
tokens but a local estimate around 22k, below the 96k trigger, with no summary.

The estimator now treats non-ASCII text conservatively and consults measured
usage. Working history is compacted after 24 messages or the token threshold;
the recent tail also has a 6k estimated-token budget. Consecutive pending user
inputs remain available. Archived messages stay unchanged on disk and in chat
history. Handoffs preserve confirmed facts, preferences, corrections and pending
tasks, without copying old assistant tone or formatting. Input is processed in
bounded chronological chunks, cumulative summaries have an explicit output
target, and truncated summaries never advance the cursor. Successful handoffs
are saved before asynchronous final-answer generation, including when a later
reply attempt fails.

One production expression failure was `JSONDecodeError: Expecting value: line 1
column 1`. The response was not retained, so its exact prefix cannot be
reconstructed. The previous parser accepted only raw JSON or an exact lowercase
`json` fence. A differently fenced or prefaced complete envelope could fail;
plain text on a subsequent retry was silently accepted without expression
metadata. This explains a confirmed failure path, not proof of the missing raw
response's precise wrapper or of context length being its sole cause.

Parsing now accepts one complete envelope with common fences or explanatory
wrappers. Expressive mode requires a valid plan and emotion; plain text,
ambiguous envelopes and truncation trigger one local formatting recovery using
the existing conversation and tool results, with tools disabled. This recovery
does not replay the tool loop. Invalid recovery is not delivered and follows
the existing durable job retry path. The turn audit records format reason,
wrapper presence, finish reason and recovery result without recording chat
content. The active expression rules explicitly supersede historical assistant
examples and prohibit invented preparation/sleep stories during celebrations.

`deploy/rebuild_chat_context.py` prepares an administrative handoff without a
billing callback or user-visible messages. Per-chunk checkpoints are resumable
and matched to the original summary and message prefix. Apply requires stopped
session writers, validates that prefix, backs up the latest session, preserves
appended messages and changes only context fields. It does not delete or edit
durable/event memory. This is an operator tool, not a new public reset endpoint.

Validation: private suite 600 passed; portable/public suite 589 passed. Both
reported one local pytest configuration warning because pytest-asyncio is not
installed in the verification interpreter. The public suite initially hit a
Windows file replacement PermissionError in an unchanged session-store test;
that test and the final full run passed on rerun. Four isolated real-model cases
passed: celebration in multiple messages, requested community sticker, quiet
support without stickers, and local plan recovery. A first celebration sample
invented study effort; the final prompt was tightened and the sample rerun.
These are bounded samples, not a guarantee of naturalness in every conversation.

Production: five explicit Python-file replacements, old/new SHA-256 checks,
merged-tree import/container smoke test, backups, stopped writers during the
session change, health checks and three SQLite quick_checks all passed. The
target transcript, memory files, billing data and other sessions were verified
unchanged during migration. No Android build or account test chat was sent.
The change applies to replies after user messages; proactive messages and
independent reminders retain their existing delivery paths.

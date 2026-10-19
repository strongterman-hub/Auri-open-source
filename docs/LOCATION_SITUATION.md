# Location situation evidence

Chat replies, proactive decisions, onboarding and continuity checks share a per-generation situation record. It combines recent owner statements, negative constraints, reported location freshness, semantic place relationships and conservative activity hypotheses. A location tool refresh updates the same evidence record before final validation.

## Owner statements

Location and activity are separate. “I am not at the office” excludes the employer location; it does not imply another location or exclude working remotely. Future plans, past mentions, questions, quotes and hypothetical clauses do not become authoritative current facts. The interpreter uses a bounded clause grammar and leaves unsupported meanings unknown; it does not claim general language coverage.

Recent location statements use a 15-minute evidence window, based on persisted message timestamps, without waiting for event extraction. Explicit current corrections override GPS and older conversation. A routine negative self-report is not automatically negative feedback.

## Semantic places and cache

OSM geometry distinguishes an enclosing venue from a nearby POI. Parent labels require polygon coverage; a nearby office building cannot become the parent of a campus. Office map categories describe buildings, not the user's employer. Residential areas are not automatically the user's home.

Resolver/cache version 2 ignores legacy semantic entries that lack relationship evidence. Reuse across different coordinates requires the original venue polygon to cover the new point. Exact-fix observations may retain a nearby relationship. No coordinates or polygon geometry are exposed in model-facing location tools or generic audit logs.

The resolver tries the official OSM map API, then Overpass and administrative address fallback. Configure a suitable contact user agent for a self-hosted instance. Public service availability and map completeness can still limit venue resolution.

## Freshness and final replies

The default recent-location window is 15 minutes; records up to two hours remain last-known evidence. Online chat may request a refresh after four minutes. Background generation does not wait for the phone. Expired records cannot generate current-location or activity assertions.

The existing protocol reports server reception time. Phone measurement time and accuracy remain unknown until clients provide them; reception freshness does not prove a newly measured precise fix.

The fact checker can use supplied fresh location evidence without demanding a second tool call. Both safe and rewritten candidates receive a final check for identifiable current-situation conflicts. Historical company facilities and conditional suggestions remain distinct from assumptions about what is available now. If a location query cannot be verified by the model, a bounded fallback answers only from the available evidence or states that the current place is unknown.

Audit records link an evidence ID and revision to location age, freshness, relationship type, resolver/cache version, owner-state categories and final validation outcome. They omit exact place names, addresses, owner text, coordinates and full prompts.

## Validation boundary

Tests cover clause scope, owner correction, cache migration, polygon relationships, account isolation, expired evidence, refresh updates and rewritten-response containment. Model evaluation uses fictional conversations and places in isolated execution. Natural user experience remains an observation after deployment; engineering checks do not establish device receipt or subjective quality.

The server change requires no new Android permissions or APK. Conversation compression and measurement metadata are separate follow-up work.

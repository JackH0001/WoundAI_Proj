# WoundAILite local research withdrawal handoff

An account deletion can request research withdrawal. A consumer service App Attest installation and a research App Attest installation have different origins, counters and identifiers. The user explicitly confirms association of the existing registrations for this deletion. A matching hexadecimal ID, shared device, subscription, or Apple sign-in alone cannot authorize a research operation.

## From confirmation to receipt

1. The candidate account screen requires the accepted original deletion checkpoint and the corresponding research withdrawal request. The app can read its local progress without a network call. On explicit confirmation it saves the two origins, original service account, operation and current existing research installation before sending either request. If there is no readable research registration, the app asks the user to check the original installation and creates no new ID.
2. The personal service accepts only its own signed withdrawal-challenge request to `POST /api/v1/lite/account/deletion/research-offer`. It checks the original registered owner, accepted closure operation and immutable research-withdrawal confirmation. The first target of this operation is durable. Replays use the same signed pair and a fresh ten-minute ticket. A different target cannot replace it.
3. The independent research service accepts only its own signed withdrawal-challenge request to `POST /api/v1/lite/research/deletion-proof`. It checks both native signatures, scope, bucket, owner and ticket interval. It adds its own signature, durably saves the proof, and returns a receipt digest. An expired ticket with no prior acceptance needs a new offer; a previously accepted request can read the identical receipt. Neither request includes images, depth data, masks, a human login token or a new consent.
4. The phone records only validated progress for the same account, operation and origins. A receipt confirms both identities; it says nothing about media, backups or final account deletion. The separate private worker still requires an explicitly reviewed proof manifest and independent authority pins. The receipt is not a command to execute a cleanup. Other devices and historical subjects without proofs remain pending.

## Deployment boundary

Both routes default to unavailable. Each service must receive only its own read-only signing seed and the two pinned public keys. The mount bytes must match the SHA-256 pin, role, scope and destination. The native image only exposes the offer route; the research-only image can expose only the acceptance route. Do not embed private keys in the app or in source, send proofs to telemetry, or grant either service permission to run private cleanup. Provider-level signer custody, key rotation/retention, review and delivery of proofs to a real private worker, Apple App Attest, and device acceptance remain separate release gates.

## Claude reproduction

Run `test_lite_research_issuer` and `test_lite_cross_origin_research` with the same local Python paths as the Lite prelaunch workflow. The fixture registers two independent synthetic attested clients, writes one synthetic image, submits an account closure and transports both signed HTTP requests. The private worker consumes the verified evidence, fences only the actual research registration and keeps the original media and its history intact. The tests also cover an expired first delivery, lost replies, different targets, wrong audience/owner, changed signatures and an incorrect challenge purpose. The iOS suite `LiteResearchDeletionLinkTests` exercises interrupted progress, explicit retry, immutable bindings, a different service account, missing research registration and rejection of a false cleanup claim.

The TestFlight archive and public PR at `fa827a5` predate this change.

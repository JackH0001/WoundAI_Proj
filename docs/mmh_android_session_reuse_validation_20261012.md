# MMH Android session reuse — local validation, 2026-10-12

## Scope

Android now shares a process-memory medical session between settings, measurement, record review and consent withdrawal. Each acquisition checks current identity, role and account revision through `GET /api/v1/auth/session`; concurrent acquisitions share a single task. It avoids repeating password login for every screen entry. Account, origin and Google-session changes invalidate old clients before dispatch and after responses. No bearer persistence or automatic upload retry is added.

This is local source only. It requires the independent backend session contract originally implemented at `50cc5d7` and now integrated with deployed source `85775e8`. The currently deployed latency images do not include that contract. Do not install this client until the paired backend is verified. A missing endpoint deliberately blocks acquisition.

Android acquisition occurs at screen entry, unlike the iOS per-acquisition capture fixture. Existing backend account-revision/permission guards remain necessary on every request. This patch does not claim instant UI refresh of remote role changes or a measured cloud latency improvement.

## Validation

Final isolated Gradle run: **93 tests, 0 failures, 0 errors, 0 skipped** across 10 suites. New session tests comprise contract 12, pool 10 and real BackendClient transport 7 (29 total).

Coverage includes ten acquisitions with one password login and ten live checks; concurrent callers; account/origin changes; late old-account results; rejection before dispatch and after a suspended response when the credential epoch changes; revoked credentials; temporary server errors; malformed/expired/mismatched replies; missing endpoint; and no automatic password retry on transient failure.

Both `assembleMmhValidation` and `compileMmhInternalKotlin` passed. This does not prove Play signing, device installation, Google login or real upload completion. No new APK was installed, no AAB signed/uploaded, no cloud resources changed.

Command (JDK 17):

```sh
JAVA_HOME=/opt/homebrew/opt/openjdk@17 ./gradlew -I /private/tmp/mmh-local-build-dir.gradle :app:testMmhValidationUnitTest :app:assembleMmhValidation :app:compileMmhInternalKotlin --offline --no-daemon --no-build-cache
```

All 66 tasks executed successfully, 35 seconds. Build directory is `/private/tmp/mmh-session-android-build-20261012`; evidence log `/private/tmp/mmh-android-session-isolated-final.log` and XML in `_app/test-results/testMmhValidationUnitTest`.

The earlier default build-directory run failed because generated KSP Java sources contained duplicate ` 2.java` / ` 3.java` copies. The original directory was preserved; an isolated output directory with cache disabled passed. The cause of those duplicate files is not established, so no sync provider is blamed. A separate cosmetic comment-placement command initially used the wrong working-directory prefix and was corrected without changing behavior.

## Delivery gates

Review and publish paired backend/mobile source, run CI, authorize and verify the paired backend deployment, then build/install both platforms and test repeated captures plus upload receipts on devices. Existing latency deployment and TestFlight/Play releases do not contain this change. Tokens remain process-local: restarting the app or expired Google authorization may still require sign-in.

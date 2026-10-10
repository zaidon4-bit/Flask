# Infinite Academy — Android Keystore proof-of-possession

## What is now included

This repository contains both the Flask website and an Android WebView project under `android-app/`. The Android project is source code, **not a prebuilt APK**. It must be configured with the deployed HTTPS site origin and built/tested with Android Studio or a compatible Gradle/Android SDK environment.

The login protocol now does the following for native app logins:

1. The Android app generates a UUID installation ID and an EC P-256 signing key pair. The UUID is stored in app-private preferences; the private key stays inside Android Keystore and is never exposed to JavaScript.
2. The WebView bridge returns only the installation UUID and SubjectPublicKeyInfo public key, and only to the exact configured HTTPS origin/main frame.
3. Before login, the browser JS calls `POST /api/device/challenge` with the email, installation ID, public key and the site's CSRF header. The server creates a random 32-byte challenge valid for 90 seconds and binds it to hashes of those values.
4. The app signs the challenge bytes with `SHA256withECDSA` using the non-exportable Keystore private key. The signature and challenge are submitted with the normal login form.
5. After the password is checked, Flask validates the one-use challenge, its expiry, account/install/key bindings, and the ECDSA signature. A challenge is consumed once. A registered installation cannot change its public key silently.
6. The server stores SHA-256 hashes of the installation UUID and public key in `devices`; the private key is never stored on the server. Device approval remains a separate admin decision.

The regular website continues to use browser cookies. A browser cookie associated with a native-bound record cannot, by itself, re-authorize that native device at login.

## Build the Android project

1. Deploy the Flask website on HTTPS and verify its `/health` endpoint.
2. Open `android-app/` in Android Studio.
3. Set the Gradle property `academyBaseUrl` to the site's HTTPS origin only (no path). The easiest local option is to add `academyBaseUrl=https://your-real-domain.example` to `android-app/local.properties` (preserve any `sdk.dir` entry Android Studio has created). This file is ignored by Git. Alternatively pass `-PacademyBaseUrl=https://your-real-domain.example` to Gradle.
4. Sync the Gradle project in Android Studio, then use **Build > Build Bundle(s) / APK(s) > Build APK(s)**. This folder does not include a Gradle wrapper script, so use Android Studio's Gradle integration or a locally installed Gradle.
5. Install the debug APK on a physical Android device. No release APK has been built or signed by this repository preparation.

The Android project uses `WebViewCompat.addWebMessageListener` from AndroidX WebKit with one exact HTTPS origin. Do not change the allowed origin to `*`. External links are sent to the system browser. Cleartext HTTP, file access, content access and third-party cookies are disabled.

## Existing v9 device records

Older app records have an installation hash but no bound public key. On the first successful proof from that same UUID, the server attaches the candidate public key but changes an approved legacy row to `pending`. The student must ask the administrator to approve it once. This deliberately avoids silently trusting a new key just because it claims an old UUID. A different key presented for an already-keyed installation is rejected and does not replace the stored key.

If the Keystore key becomes unavailable while preferences remain, the Android app rotates its installation UUID so the re-keyed app is treated as a new installation and needs admin review.

## Security properties and limitations

- The server challenge is random, expires after 90 seconds, is tied to the supplied email/install/public key, and is one-time. Challenge requests are rate-limited per IP using the audit log.
- ECDSA proof demonstrates control of the private key corresponding to the registered public key. It raises the cost of copying only a UUID/cookie.
- The private key is created in Android Keystore and never returned to the web page. The JavaScript bridge accepts requests only from the configured HTTPS origin and the main frame.
- This does not prove that the APK is unmodified or the device is physically unique under a fully compromised/rooted device. Consider Play Integrity API and a release-hardening review before a public launch.
- Native proof is required at login; an authenticated session is then protected by the normal server session and device approval checks.
- Database schema updates add columns and a challenge table without deleting existing account/device data. Back up the database before deploying schema changes.

## Required tests before public release

- First native install: valid proof creates an approved first-device record when the account/device policy permits it.
- A different install/key: new record stays pending while another device already exists.
- Same install ID/key after WebView cookie loss: it reconnects to the same device row.
- A signature made by a different private key: login is denied and no device is registered.
- Replaying a consumed or expired challenge: login is denied.
- Same install UUID with a different key: stored public key is not replaced; login is denied.
- Legacy v9 keyless device: public key enrollment requires admin review.
- Admin replacement approval blocks the old device, invalidates old sessions, and the new device can log in only after re-authenticating.

Official Android reference: https://developer.android.com/develop/ui/views/layout/webapps/native-api-access-jsbridge

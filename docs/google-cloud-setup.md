# Google setup for Built to Work (for the Google Workspace admin)

Two separate pieces. Both are free and take about 15 minutes each.

## A. "Connect your Gmail" (staff send one-off emails from their own address)

1. Go to https://console.cloud.google.com signed in as a built2work.com admin.
2. Top bar → project picker → **New project**. Name: `Built to Work app`. Organization: built2work.com. Create.
3. **APIs & Services → Library** → search **Gmail API** → **Enable**.
4. **APIs & Services → OAuth consent screen** (may be labelled "Google Auth Platform"):
   - User type: **Internal** (only built2work.com accounts can use it; no Google review needed).
   - App name: `Built to Work`. Support email: an admin address. Save.
   - **Data access / Scopes** → add `.../auth/gmail.send`, `openid` and `email`. Save.
     Optional (only if "Email replies" will be used): also add `.../auth/gmail.readonly`.
     It can be added later; staff then click Reconnect once.
5. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Application type: **Web application**. Name: `Built to Work web`.
   - **Authorized redirect URIs** — add all four:
     - https://app.builttoworkusa.com/messaging/gmail/callback/
     - https://btw-production.onrender.com/messaging/gmail/callback/
     - https://preview.builttoworkusa.com/messaging/gmail/callback/
     - https://btw-preview.onrender.com/messaging/gmail/callback/
   - Create. Google shows a **Client ID** and **Client secret**.
6. Give those two values to whoever manages Render (not by plain email if possible). They go in
   Render → btw-production (and btw-preview) → Environment:
   - `GOOGLE_OAUTH_CLIENT_ID` = Client ID
   - `GOOGLE_OAUTH_CLIENT_SECRET` = Client secret
   - `GOOGLE_OAUTH_ALLOWED_DOMAIN` = `built2work.com`

The app only asks for permission to **send** email. It can't read anyone's inbox.
Each person can disconnect at any time (Settings → Account → Email), and an admin can
remove the app's access for everyone at myaccount.google.com/permissions or in the
Admin Console (Security → API controls → App access control).

## B. Automatic email from noreply@built2work.com

1. Create **noreply@built2work.com** as a Google Group or an alias on an existing account.
2. Admin Console → **Apps → Google Workspace → Gmail → Routing → SMTP relay service → Configure**:
   - Allowed senders: **Only addresses in my domains**
   - Authentication: **Require SMTP Authentication** (leave IP allowlist off) and **Require TLS encryption**
   - Save.
3. Pick the Google account the app signs in with (any built2work.com account with
   2-Step Verification on). In that account: myaccount.google.com → Security →
   **App passwords** → create one named `Built to Work app`. Copy the 16-character password.
4. In the app: **Settings → Connections → Set up email**:
   - Send from: `Built to Work <noreply@built2work.com>`
   - Mail server: `smtp-relay.gmail.com`, Port: `587`
   - Sign-in: that Google account's address; App password: the 16 characters
   - Save, then **Send test email**.
5. Also confirm SPF includes `include:_spf.google.com`, DKIM is on
   (Apps → Gmail → Authenticate email) and a DMARC record exists for built2work.com.

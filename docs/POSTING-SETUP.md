# Posting setup (ClipFarm 0.8.6)

ClipFarm can't post until you've done the steps below. Each one is a one-time job and you do it yourself. ClipFarm never types passwords and never accepts terms for you.

After each step, open **My Clips › Post & Submit › Check accounts**. It tests the sign-in, permissions and limits for every account without posting anything.

---

## 1. YouTube (@gta6viralclips-v5y): stop the weekly sign-in

Google signs you out every 7 days while your app's consent screen is in **Testing**.

1. Open console.cloud.google.com and choose the project with your ClipFarm OAuth client.
2. Go to **APIs & Services › OAuth consent screen** (on the new console this is **Google Auth Platform › Audience**).
3. Under **Publishing status**, click **Publish app**, then **Confirm**.
   - The two scopes ClipFarm uses (upload, and read your own channel) count as sensitive.
   - Google shows an "unverified app" warning when you sign in. That's fine for your own channel: click **Advanced › Go to ClipFarm (unsafe)**.
   - You don't need to submit the app for verification to use it yourself.
4. In ClipFarm: **Settings › Connections › Connect YouTube channel…**, then sign in again once.
5. **Settings › Posting:** tick "My Google consent screen is published". This turns off the reminder.

Quota: Google allows 100 uploads a day per project. ClipFarm shows how many are left and moves extras to the next day. The day resets at midnight California time, which is 6 pm Brisbane time (5 pm from mid-March to early November).

## 2. TikTok (@vira1gta6clips): inbox (draft) mode

1. Go to developers.tiktok.com, log in, then **Manage apps › Connect an app**. Give it a name such as "ClipFarm Eden" and choose the Desktop platform.
2. **Products › Add products:** add **Login Kit** and **Content Posting API**.
   - In Content Posting API, leave **Direct Post** off. ClipFarm uses Upload (the inbox).
3. **Login Kit › Desktop › Redirect URI:** add `http://127.0.0.1:47821/oauth/tiktok/callback/` (keep the slash at the end).
   - If your ClipFarm uses another port, add `http://127.0.0.1:*/oauth/tiktok/callback/` instead.
4. **Scopes:** `user.info.basic` and `video.upload`.
5. Make it work for your account. Either option is fine:
   - **Sandbox (fastest):** create a Sandbox, then under **Target users** add @vira1gta6clips.
   - **Submit for review:** fill in the app details, privacy policy and terms URLs, and a short demo video showing ClipFarm uploading to the inbox.
6. Copy the **Client key** and **Client secret** into ClipFarm: **Settings › Connections › TikTok**. Then click **Connect TikTok…** and approve.
7. **Using it:**
   - ClipFarm uploads up to 5 clips a day.
   - Each one appears as a notification in the TikTok app's inbox.
   - Tap it, add a sound if you like, paste the caption (ClipFarm copies it for you) and post.
   - The link fills itself in if TikTok returns it; otherwise paste it in Post & Submit.

## 3. Instagram (@gta6.viralclipsss): automatic Reels

Posting straight from your PC needs Meta's **Facebook Login for Business** path:

1. **Professional account.** In the Instagram app, go to **Profile › ☰ › Settings and privacy › Account type and tools › Switch to professional account**. Pick **Creator** (or Business).
2. **Facebook Page.** Create a Page (for example "GTA6 Viral Clips"). Then link it:
   - in Instagram: **Edit profile › Page › Connect**;
   - or in Meta Business Suite: **Settings › Accounts › Instagram accounts › Connect**.
3. **Meta developer app.**
   1. Go to developers.facebook.com, then **My Apps › Create app**.
   2. Choose the use case **"Manage messaging and content on Instagram"** and app type Business.
   3. Under **App roles**, check that your Facebook account is an Admin.
4. **Go Live.** Add a privacy policy URL under **App settings › Basic**, then switch the app to **Live**. Posts from an app in Development mode are only visible to you.
5. **Keys into ClipFarm.** Copy the **App ID** and **App secret** into **Settings › Connections › Instagram (Meta app)**.
6. **Connect.**
   1. Click **Connect Instagram…** and approve every permission.
   2. You land on a page that says "Success". Copy that page's whole address and paste it into ClipFarm.
   3. ClipFarm swaps it for a 60-day sign-in and checks the account. Reconnect every two months.

If anything is missing, the check lists exactly what. Until it passes, Instagram uses the **Post Kit**.

## 4. X (optional)

X posting needs a paid X API plan. Without it, X uses the Post Kit, and that's fine.

If you do buy access, go to developer.x.com and open your app:

- set **User authentication › App permissions** to **Read and write**;
- under **Keys and tokens**, generate the **API key and secret** and the **Access token and secret**;
- put all four into **Settings › Connections › X**.

## 5. Send to phone

- The first time you use **Send to phone**, Windows asks whether ClipFarm can use the network. Choose **Allow** for **Private networks** only.
- Your phone must be on the same Wi-Fi. Links expire after an hour.
- **Optional:** in **Settings › Posting › Phone sync folder**, choose your Dropbox or Google Drive folder. **Phone folder** then copies each clip and its caption there.

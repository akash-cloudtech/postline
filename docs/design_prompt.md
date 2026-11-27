# Design prompt: "Postline", a local social media studio

Design the UI for a local desktop web app (runs on the user's Mac mini and opens in a browser at localhost). The backend already exists and works. I need the page designs, the component system, and light and dark themes.

---

## 1. What the product does

A solo creator records short vertical videos on their iPhone and adds them to a Google Photos album. The app does the rest:

```
Phone: record → add to Google Photos album "SocialMedia"
Mac:   app checks album every 5 min → new video found → downloads it
       → AI (Gemini) watches the video → writes caption + 5 hashtags + 📍 city
       → ✋ appears in REVIEW QUEUE → creator edits/approves
       → posts to Instagram as a Reel (with caption) AND a Story (no caption)
```

- **Today:** Instagram only, posting a Reel and a Story.
- **Future:** TikTok, YouTube Shorts, Facebook Page, LinkedIn, Threads and X. The design **must be platform-extensible** from day one. Show these as "coming soon" or "not connected", never as missing.

**User:** one person, a tech-savvy creator, using a Mac only (desktop layout; no mobile layout needed). They open the app a few times a day to approve posts and check that things ran.

**Core feeling:** "My content pipeline is running, and nothing goes out without my OK." The app should make the creator feel calm and in control.

---

## 2. Visual direction: clean creator studio

- Minimal, card-based and airy, in the spirit of modern creator tools (Linear-level polish, but warmer).
- **Light and dark mode** are both required. Use design tokens (surface, surface-raised, border, text-primary/secondary/muted, accent, success, warning, danger, info).
- One brand accent color (suggest one). Platform colors (Instagram gradient, TikTok, YouTube red and so on) appear **only** on small platform badges and icons, never as page chrome.
- Typography: a clean sans such as Inter or Geist, with tabular numbers for times and counts. Captions are shown in a style that resembles how they'll look on Instagram.
- Videos are always shown as **9:16 phone-shaped previews** with rounded corners, a play-on-hover state and a duration badge.
- Desktop: design at **1440 px** wide, and make sure it works at a minimum of 1280 px.
- Layout: a fixed left sidebar for navigation, with the main content area beside it.

---

## 3. Status vocabulary (use consistently everywhere)

Every video or post moves through these states. Give each one a badge color and icon:

| State | Meaning |
|---|---|
| `Detected` | Found in album, downloading |
| `Writing caption` | AI is generating (show a subtle animated state) |
| `Needs review` | Waiting for the creator, **the most important state** |
| `Scheduled` | Approved, will post at a set time |
| `Posting` | Uploading / Instagram processing (can take 1–2 min) |
| `Posted` | Live; links to the Reel and the Story |
| `Partially posted` | e.g. Reel live but Story failed, with a retry for just the failed part |
| `Failed` | With a plain-English error, attempts used (max 3) and a Retry button |
| `Rejected` | Creator skipped it; can be restored |
| `Not a video` | Photo found in album; ignored |

Within a post, each platform output has its own status. For example: Instagram Reel ✅ and Instagram Story ❌. Design a compact **"destination chip row"** pattern, such as `[IG Reel ✓] [IG Story ✗ Retry]`. This is how future platforms will slot in.

---

## 4. Navigation and global elements

**Sidebar items:**
1. Dashboard
2. Review queue (with a count badge, e.g. "2")
3. Calendar
4. History
5. Script ideas
6. Platforms
7. Settings

**Pinned at the bottom of the sidebar:**
- **Pipeline status pill**, with these states:
  - ● Running · "checked 2 min ago"
  - ⏸ Paused
  - ⚠ Problem (e.g. "Instagram token expires in 3 days" or "Album unreachable")
- A Pause/Resume control.

**Global elements:**
- **"+ New post" button**, top-right on every page. It opens the Manual upload flow (§5.7).
- Toast notifications, for example "Reel posted ✓ View on Instagram".
- Activity log access (a drawer or a dedicated panel; see Dashboard).

---

## 5. Pages to design

### 5.1 Dashboard
At a glance: is everything OK, and is anything waiting for me?
- **Hero row:**
  - **"Needs review" card:** count, thumbnails and a "Review now" CTA. Most prominent when the count is above 0.
  - Pipeline health: last album check, next check, Instagram connection, Gemini status.
- **Today / this week:** posts published, scheduled and failed.
- **Up next:** the next 3 scheduled posts, with time, thumbnail and destinations.
- **Recent activity feed:** human-readable events with timestamps, such as:
  - "07:41 New video found in SocialMedia"
  - "07:42 Caption written (gemini-3.5-flash-lite, 15s)"
  - "07:50 Approved"
  - "07:51 Reel posted"
  - "07:52 Story posted (trimmed to 59s)"
- **"View full log" link:** opens a log drawer with the raw technical log in monospace, auto-scrolling, with a filter for errors.
- **Empty state:** "All caught up. Add a video to your SocialMedia album and it'll appear here within 5 minutes."

### 5.2 Review queue (the core page, so design it with the most care)
For each pending video:
- **Left:** a 9:16 video player with a duration badge and recorded date/time. If the video is over 60 s, show a note: "Story will be trimmed to the first 59 s."
- **Right: an editor.**
  - **AI summary** (read-only, small): "Light snow over an apartment parking lot with autumn trees."
  - **Caption:** an editable textarea with a live character counter (Instagram limit: 2,200) and an emoji picker.
  - **Hashtags:** editable chips, exactly 5 suggested, add/remove allowed, limit 30. Show the count.
  - **Location:** an editable chip, e.g. "📍 Warrensburg, Missouri", with a remove button. Show a privacy hint when the video was recorded inside the creator's "home zone": "Recorded near home: location hidden".
  - **Destinations:** toggles for Instagram Reel ✓ and Instagram Story ✓, plus greyed-out future platforms marked "Connect".
  - **Regenerate with AI:** with an optional instruction field, e.g. "make it funnier" or "shorter". Show which model wrote it.
  - **Live preview:** a toggle that shows how the Reel caption will look on Instagram (mock phone frame).
- **Actions:**
  - Primary: **Approve & post now**
  - Secondary: **Schedule…**, which opens a date/time picker with suggested best times
  - Tertiary: **Reject**
- Show the queue as a list or filmstrip of pending items so the creator can move between them. **Keyboard shortcuts:** A = approve, R = reject, ←/→ = next/previous.
- **States to design:** caption still generating (skeleton), caption generation failed (fallback text plus "Try again"), and posting in progress after approval (progress steps: Creating → Uploading → Instagram processing → Published).

### 5.3 Calendar
- Month and week views of **scheduled** and **posted** content. Each entry shows a thumbnail, time and destination chips.
- Drag an item to reschedule it, and click it to open it in the review editor.
- A side panel with an "Unscheduled approved posts" list to drag onto the calendar.
- Show today's line and the creator's time zone (America/Chicago).

### 5.4 History
- A table or grid of every video ever processed. Columns:
  - thumbnail
  - recorded date
  - caption (truncated)
  - destinations with per-destination status chips
  - posted time
  - links: "View Reel ↗" and "View Story ↗" (note that Stories expire after 24 h)
- Filters: status, platform, date range, search caption text.
- Row detail (side sheet): full caption, model used, AI summary, the full event timeline for that video, errors with plain-English explanations, and Retry for failed parts.
- **Example failure copy:** "Instagram refused the Story because it's longer than 60 s. We'll trim it and retry."

### 5.5 Script ideas
This feature is on hold in the backend, so design it now and build it later.
- Every morning the app generates **2 video ideas**: one **General** topic and one **Tech** topic. Each has a spoken ~2-minute script (~300 words) to read on camera.
- Today's two ideas are shown as cards: topic, hook line, full script in a readable teleprompter-ish style, and estimated read time.
- Actions:
  - "Regenerate"
  - "Copy"
  - "Print" (the app can print to the home network printer)
  - "Mark as recorded", which links the idea to the video once it's posted
- An archive of past days' ideas.

### 5.6 Platforms
- One card per platform:
  - **Instagram (connected).** Shows:
    - the account `@akash.verma.dso` with its avatar
    - account type "Creator"
    - what it posts (Reel + Story)
    - token health: "Expires in 52 days", with a warning style when under 7 days
    - Reconnect / Disconnect
  - **TikTok, YouTube Shorts, Facebook Page, LinkedIn, Threads, X:** "Coming soon" or "Connect", with one line about what will be posted.
- An info note: "Facebook personal profiles can't be posted to automatically (Meta restriction). Use a Facebook Page."

### 5.7 New post: manual upload (modal or full page)
- A drag-and-drop zone for a video from the Mac. It shows file name, duration, resolution, and validation for 9:16 and length.
- After upload, the video enters the same Review flow (AI caption → edit → approve/schedule).
- A secondary note: "Or add videos to your Google Photos 'SocialMedia' album; they're picked up automatically."

### 5.8 Settings
Grouped sections:
- **Source:** Google Photos shared album link, "Check now", check interval (5 min default), last check result.
- **AI captions:**
  - **Caption style:** free-text voice guide. Current value: "Short and punchy, 2–3 lines, under 200 characters, 5–8 emojis, fun casual tone."
  - Number of hashtags.
  - Model: primary plus fallback list, showing live availability as dots.
  - A "Test on last video" button.
- **Location and privacy:** auto-add city on/off, home zone (map pin plus radius slider, default 5 km).
- **Posting:** approval required (on, locked for now), default destinations, default schedule times.
- **Notifications:** macOS notifications on/off for each event type.
- **Advanced:** data folder, log file location, API keys (masked, "Replace" only, never shown in full).

---

## 6. Real sample data (use this instead of lorem ipsum)

- **Account:** @akash.verma.dso (Instagram Creator)
- **Album:** "SocialMedia"
- **Video:** 64.7 s, vertical 1080×1920, recorded 26 Nov 2026 at 7:35 AM, light snow over a parking lot with autumn trees
- **Caption:**
  > First snow of the season in Warrensburg, Missouri! ❄️🍂 Watching the autumn leaves get dusted with white is pure magic. ☕️✨ Do you love the first snow or miss fall already? 👇 Let me know!
- **Hashtags:** #firstsnow #falltransition #missourilife #snowday #autumnleaves
- **Location:** 📍 Warrensburg, Missouri
- **Model:** gemini-3.5-flash-lite
- **Script idea examples:**
  - General: "Why the first snow always feels magical"
  - Tech: "What on-device AI on the new Macs actually means for you"

---

## 7. Deliverables

1. Design tokens and a small component sheet:
   - buttons, inputs, status badges (all states in §3)
   - destination chips
   - 9:16 video preview card
   - caption editor
   - toasts
   - sidebar with pipeline-status pill
2. Pages, each in light **and** dark mode:
   - Dashboard
   - Review queue
   - Calendar (month and week)
   - History, plus its row-detail sheet
   - Script ideas
   - Platforms
   - New post / upload
   - Settings
3. Key states:
   - Review queue: empty, generating, failed
   - Posting progress
   - Partially posted with retry
   - Token-expiring warning
   - Pipeline paused

**Don't design:** login or sign-up (single local user), billing, team features, mobile layouts, or analytics (not in scope yet).

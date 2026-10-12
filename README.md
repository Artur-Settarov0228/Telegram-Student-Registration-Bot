# Telegram Student Registration Userbot

A clean, reliable, production-oriented Telegram Userbot that operates through the owner's personal Telegram account (using Telethon), automatically handles registration conversations with prospective event participants in natural language (Uzbek, Russian, English), and saves confirmed registration records to Google Sheets.

> **Important**: This is a Telegram Userbot running on Telethon, not a BotFather bot. It interacts directly as your personal Telegram account.

---

## Features

- **Personal Account Integration**: Uses Telethon to authenticate as your personal Telegram user account.
- **Natural Language Registration**: Detects registration intent in natural Uzbek (as well as Russian and English), extracts first name, surname, and phone number, whether provided in one message or across multiple messages.
- **Deterministic State Isolation**: Keeps each participant's conversation state isolated in memory; asks only for missing fields; prompts for explicit confirmation before saving.
- **Contact Exclusion & Access Control**: Deterministically filters out excluded Telegram user IDs (`EXCLUDED_USER_IDS`) and supports optional allowlisting (`ALLOWED_USER_IDS`). Excluded users never receive automated replies.
- **Google Sheets Integration**: Saves confirmed registrations with `Asia/Tashkent` timestamp, Telegram User ID, first name, last name, phone number, and status (`confirmed`). Prevents duplicate rows on retries.
- **Privacy & Security**: Never logs full phone numbers or credentials; session and credentials files are protected via `.gitignore`.

---

## Project Structure

```
telegram-registration-userbot/
├── app/
│   ├── __init__.py
│   ├── main.py              # Application entrypoint & graceful shutdown
│   ├── config.py            # Environment settings & AccessController
│   ├── agent.py             # Natural language understanding & entity extraction
│   ├── registration.py      # Conversation state manager & validation
│   ├── telegram_client.py   # Telethon client & event handlers
│   └── google_sheets.py     # Google Sheets client & duplicate prevention
├── credentials/
│   └── .gitkeep             # Directory for Google service-account.json
├── .env.example             # Configuration template
├── .gitignore               # Secrets and cache exclusions
├── requirements.txt         # Pinned Python dependencies
└── README.md                # Documentation & instructions
```

---

## Installation & Setup

### 1. Prerequisites

- Python 3.11+
- A personal Telegram account
- Telegram API credentials (`api_id` and `api_hash`) from [my.telegram.org](https://my.telegram.org)
- A Google Cloud Service Account with Google Sheets API enabled and access to your spreadsheet

### 2. Install Dependencies

Create and activate a virtual environment, then install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Telegram API Credentials Setup

1. Go to [https://my.telegram.org](https://my.telegram.org) and log in with your Telegram account.
2. Navigate to **API development tools**.
3. Create a new application (e.g. name: "Registration Userbot").
4. Note down your `api_id` and `api_hash`.

### 4. Google Sheets Setup

1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project (or select an existing one).
3. Enable **Google Sheets API** and **Google Drive API**.
4. Navigate to **APIs & Services > Credentials** and click **Create Credentials > Service Account**.
5. Give the service account a name and create it.
6. Under the **Keys** tab of the service account, click **Add Key > Create new key > JSON**.
7. Download the JSON file, rename it to `service-account.json`, and place it in the `credentials/` folder:
   ```bash
   cp /path/to/downloaded-key.json credentials/service-account.json
   ```
8. Create a new Google Spreadsheet at [https://sheets.new](https://sheets.new).
9. Copy the **Spreadsheet ID** from the URL:
   `https://docs.google.com/spreadsheets/d/<SPREADSHEET_ID>/edit`
10. **Share the spreadsheet with Editor permissions** to the service account email (e.g. `your-service-account@project.iam.gserviceaccount.com`).

The userbot will automatically create the sheet tab (default: `Registrations`) and populate the header row on startup:
`registered_at`, `telegram_user_id`, `first_name`, `last_name`, `phone_number`, `registration_status`.

### 5. Environment Configuration

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Edit `.env` with your parameters:

```env
# Telegram Personal Account Credentials
TG_API_ID=12345678
TG_API_HASH=0123456789abcdef0123456789abcdef
TG_PHONE=+998901234567

# Google Sheets Configuration
GOOGLE_SHEETS_ID=your_google_spreadsheet_id_here
GOOGLE_SHEETS_TAB_NAME=Registrations
GOOGLE_SERVICE_ACCOUNT_FILE=credentials/service-account.json

# Access Control (Comma-separated Telegram user IDs)
EXCLUDED_USER_IDS=111111111,222222222
ALLOWED_USER_IDS=

# Timezone
TIMEZONE=Asia/Tashkent

# AI Model Configuration (Optional: for enhanced conversational extraction)
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
```

---

## Managing Excluded Telegram User IDs

To ensure the userbot **never** replies to specific contacts (such as family members, coworkers, or channels):

1. Find the Telegram User ID of the contact (via bots such as `@userinfobot` or client info).
2. Add the numeric ID to `EXCLUDED_USER_IDS` in `.env`:
   ```env
   EXCLUDED_USER_IDS=123456789,987654321
   ```
3. Excluded contacts are checked before any AI or conversation processing and are silently ignored.
4. You can also define `ALLOWED_USER_IDS` if you want the userbot to respond **only** to specific test accounts.

---

## Running the Userbot

Start the userbot:

```bash
python3 -m app.main
```

### First-Time Login (Authentication)

On the very first launch:
1. Telethon will prompt you in the terminal for the Telegram login confirmation code sent to your Telegram app.
2. If two-factor authentication (2FA) is enabled on your account, enter your 2FA password when prompted.
3. Once authenticated, a `userbot.session` file will be created locally. Subsequent launches will log in automatically without prompting.

---

## Registration Conversation Flow

1. **Incoming Message**: A user sends a private message:
   - *"Assalomu alaykum, ertangi tadbirga qatnashmoqchiman."*
2. **Intent Recognition**: The userbot detects registration intent and checks missing fields.
3. **Information Collection**:
   - Responds: *"Assalomu alaykum! Tadbirga ro'yxatdan o'tish uchun ismingiz, familiyangiz va telefon raqamingiz kerak bo'ladi."*
   - Participant can send all details at once (*"Ali Valiyev +998901234567"*) or step by step.
4. **Verification & Summary**: Once all 3 fields are collected, the bot presents a summary:
   ```
   Iltimos, ro'yxatdan o'tish ma'lumotlaringizni tekshiring:

   👤 Ism: Ali
   👥 Familiya: Valiyev
   📞 Telefon: +998901234567

   Barchasi to'g'rimi? Tasdiqlash uchun 'Ha' yoki 'Tasdiqlayman' deb yozing.
   Bekor qilish uchun /cancel deb yozing.
   ```
5. **Confirmation & Persistence**:
   - User replies: *"Ha"* (or *"Tasdiqlayman"*).
   - Userbot saves the row into the Google Sheet.
   - Userbot sends: *"Tabriklaymiz! Siz tadbirga muvaffaqiyatli ro'yxatdan o'tdingiz. Rahmat!"*
6. **Cancellation**:
   - User can send `/cancel` or *"Bekor qilish"* at any point to stop registration and clear their state.

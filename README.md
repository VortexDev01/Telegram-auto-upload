# Telegram Auto Uploader

A desktop GUI tool for bulk uploading video files to Telegram with automatic file ordering, rename preview, configurable captions, and resume support.

This project is built with:

- `CustomTkinter` for the desktop interface
- `Pyrogram` for Telegram MTProto access
- `Loguru` for activity logging
- `natsort` for natural file sorting

## Features

- Select a folder and scan supported video files
- Natural sorting so files like `Episode 2` come before `Episode 10`
- Preview renamed output before upload
- Upload as Telegram video or as document
- Custom caption template with placeholders
- Optional bot token support
- Sequential upload flow with retry and `FloodWait` handling
- Cancel safely after the current file finishes
- Resume interrupted uploads with `upload_state.json`
- Persistent settings stored in `config.json`

## Supported video formats

The app currently scans these extensions:

- `.mp4`
- `.mkv`
- `.avi`
- `.mov`
- `.wmv`
- `.flv`
- `.webm`
- `.m4v`
- `.ts`
- `.mpeg`
- `.mpg`
- `.3gp`

## Requirements

- Windows is the primary target environment
- Python 3.10+ recommended
- A Telegram `api_id` and `api_hash`
- Optional: a Telegram bot token if you want uploads to run through a bot account

## Installation

1. Create and activate a virtual environment.
2. Install dependencies.

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Run the application

```powershell
python main.py
```

## Configuration

Open the app, then go to `Settings` and fill in:

- `API ID`: your Telegram API ID
- `API Hash`: your Telegram API hash
- `Target Chat ID`: a numeric chat ID like `-1001234567890` or a public username like `@mychannel`
- `Delay Between Uploads`: pause between files in seconds
- `Caption Template`: supports `{name}` and `{index}`
- `Send as Video`: toggle between video upload and document upload
- `Telegram Bot API Key`: optional, only needed if you want to upload as a bot

Example `config.json`:

```json
{
  "api_id": "12345678",
  "api_hash": "your_api_hash_here",
  "chat_id": "@your_channel",
  "telegram_bot_token": "",
  "upload_delay": 2,
  "caption_template": "{name}\nEpisode {index}",
  "send_as_video": true
}
```

## How it works

1. Open `Settings` and save your Telegram credentials.
2. Click `Select Folder` and choose the folder containing your videos.
3. Enter a base name in `Rename As`, for example `My Series`.
4. Review the preview list.
5. Click `Start Upload`.

The app will upload files one by one using renamed display names like:

- `My Series 1.mp4`
- `My Series 2.mp4`
- `My Series 3.mp4`

## Caption placeholders

The caption template supports:

- `{name}`: the generated file name without extension
- `{index}`: the file sequence number

Example:

```text
{name}
Episode {index}
```

For file `My Series 3.mp4`, that becomes:

```text
My Series 3
Episode 3
```

## Upload modes

### User account mode

If `telegram_bot_token` is empty, the app starts a normal Pyrogram user session and uploads using your Telegram account.

### Bot mode

If `telegram_bot_token` is provided, the app starts a bot session instead.

Important bot limitations:

- The bot must already be added to the target group or channel
- The bot may need admin permissions depending on the chat
- Using `@username` is often more reliable than a raw numeric chat ID for bot uploads

## Resume behavior

The app writes progress to `upload_state.json` while uploading.

- If the app stops unexpectedly, it can resume from the previous position
- Resume only works when the selected file list matches the earlier upload set
- The resume file is cleared automatically after a full successful run

## Generated local files

These files are created during normal use:

- `config.json`: saved application settings
- `*.session` and `*.session-journal`: Pyrogram session files
- `upload_state.json`: upload resume state

These are already listed in `.gitignore` and should not be committed.

## Project structure

```text
.
|-- main.py
|-- config_manager.py
|-- file_manager.py
|-- rename_manager.py
|-- telegram_service.py
|-- requirements.txt
`-- ui
    |-- main_window.py
    `-- settings_window.py
```

## Troubleshooting

### Invalid chat or cannot resolve chat

- Make sure the target chat exists
- Make sure the signed-in user or bot is a member of that chat
- Try using a public `@username` instead of a numeric ID when possible

### Upload stops because of Telegram rate limits

The uploader already handles `FloodWait` by sleeping and retrying. If uploads still feel unstable, increase `upload_delay`.

### No files appear after selecting a folder

- Check that the folder contains supported video formats
- Make sure the files are directly inside the selected folder

## Packaging

`PyInstaller` is included in `requirements.txt` if you want to build a Windows executable later.

Example:

```powershell
pyinstaller --onefile --windowed main.py
```

## Notes

- This app currently focuses on folder-based bulk video uploading
- Renaming is used for the upload display name and preview flow
- Activity logs are shown inside the GUI while the upload runs

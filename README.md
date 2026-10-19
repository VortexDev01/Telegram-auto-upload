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
- Fast uploads: TgCrypto encryption plus several files uploading in parallel (messages are still posted in order)
- Live speed (MB/s) and ETA, with per-file status in the list
- Retry and `FloodWait` handling; stops early on permission errors instead of failing every file
- Cancel at any time
- Pre-upload checks: missing, empty and too-large files, and files already uploaded to the same chat
- Test Connection button to verify credentials and chat access before uploading
- Settings, upload history and the last folder / rename text are saved in a local SQLite database

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
- `Delay Between Uploads`: pause between posted messages in seconds (uploads keep running during the pause)
- `Caption Template`: supports `{name}` and `{index}`
- `Send as Video`: toggle between video upload and document upload
- `Parallel Uploads`: how many files upload at the same time (1-4, default 2)
- `Skip files already uploaded`: skip files that were already sent to the same chat
- `Telegram Bot API Key`: optional, only needed if you want to upload as a bot

Use `Test Connection` to log in and check that the target chat can be reached
(for channels it also checks that you are an admin who can post).
`Clear Upload History` forgets which files were sent to the current chat, so they upload again.

## How it works

1. Open `Settings` and save your Telegram credentials.
2. Click `Select Folder` and choose the folder containing your videos.
3. Enter a base name in `Rename As`, for example `My Series`.
4. Review the preview list.
5. Click `Start Upload`.

The app uploads the files using renamed display names like:

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

Every successfully sent file is recorded in the upload history (by path, size and
modified time, per target chat). If an upload is cancelled or the app closes, just
press `Start Upload` again: files already sent are shown as `✔ Uploaded` and skipped.
Episode numbers stay the same because they come from the file's position in the folder.

## Where data is stored

Everything lives in `%APPDATA%\AutoUpload` (open it from `Menu → Open Data Folder`):

- `autoupload.db`: SQLite database with settings and upload history
- `*.session`: Pyrogram login sessions
- `logs\autoupload.log`: log file (rotates at 5 MB)

Because this folder is outside the app, settings survive restarts and rebuilding the EXE.
On first start, an existing `config.json` and user login session next to the app are imported automatically.

## Project structure

```text
.
|-- main.py
|-- database.py
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

The uploader already handles `FloodWait` by sleeping and retrying. If uploads still feel unstable, lower `Parallel Uploads` or increase the delay.

### Uploads are slow

- Make sure `TgCrypto-pyrofork` is installed (`pip install -r requirements.txt`). Without it, encryption runs in pure Python at under 1 MB/s and the log shows a warning.
- Try `Parallel Uploads` = 3 or 4 for many small/medium files.

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

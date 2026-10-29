# videomigrate

Manual GitHub Actions pipeline that copies videos you own from a Mail.ru Cloud public folder to **YouTube** and **Rumble**, uploaded as **UNLISTED**.

Scope: YouTube and Rumble only. Every upload is started by hand (`workflow_dispatch`) or by a reviewed request file, and needs explicit confirmation inputs. Nothing uploads on push.

## Setup

Repository variables (Settings → Secrets and variables → Actions → Variables):

- `MAILRU_FOLDER_URL`: the Mail.ru public folder that holds the course videos.
- `MAILRU_SOURCE_URL`: a single MP4 share link, used only by the one-off YouTube workflow.

Repository secrets:

- `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`: YouTube OAuth with the `youtube.upload` scope. `oauth_bootstrap.py` helps create the refresh token.
- `RUMBLE_BROWSER_STATE_B64`: a Playwright storage state for rumble.com, created with `rumble_capture_login.py`. It is saved locally in `.private_browser_sessions/`, which is git-ignored.

## Workflows

| Workflow | What it does | Uploads? |
| --- | --- | --- |
| `course-discover` | Lists the folder, sorts videos, uploads `course_manifest.json` as an artifact | No |
| `course-batch-private` | Uploads 1–10 lessons from a discovery run; `dry_run` defaults to true | Only with `dry_run=false` and `confirm_unlisted_upload=true` |
| `private-request-upload` | Runs when a reviewed `batch_requests/*.json` file is pushed | Yes, only for a request that sets `privacy: unlisted` |
| `youtube-private-upload` | Downloads one MP4 and uploads it to YouTube as unlisted | Manual only |
| `rumble-private-one` | Uploads one lesson to Rumble (`mode=upload_unlisted`) | Only with every legal confirmation ticked |
| `rumble-private-ci` | Offline guard tests for the Rumble workflow | No |

Receipts are written to `receipts/` (YouTube) and `backups/rumble/` (Rumble) and committed back to the branch, so reruns skip lessons already uploaded.

## Lesson 1

Lesson 1 was already uploaded in the earlier private repository. Start batches and the Rumble workflow at sequence **2**. The Rumble uploader refuses sequence 1.

## Not verified yet

- **YouTube unlisted:** the upload code is covered by offline tests only. Run one `youtube-private-upload` before using batches.
- **Rumble unlisted:** the form selector `label[for="visibility_unlisted"]` and the owner-dashboard label `visibility: unlisted` are guesses based on the earlier Private flow. The uploader stops before final Submit if they do not match, so check one run in preview and then one real lesson carefully.

## Transcripts

`transcribe-lessons` makes clean `.txt` transcripts for uploaded lessons:

1. It tries the YouTube caption track first. This needs `YT_MANAGE_REFRESH_TOKEN` with the `youtube.force-ssl` scope.
2. With `rustasr_fallback=true`, a lesson without usable captions is transcribed from the original Mail.ru MP4 by [RustASR](https://github.com/askidmobile/rustasr) (`rustasr transcribe`, Whisper large-v3-turbo on CPU).

The RustASR commit and its `candle` fork are pinned in the workflow's `env`. Transcripts are uploaded as the `lesson-transcripts` artifact and are not committed.

CPU transcription is slow: a two-hour lesson can take many hours on a standard runner. The job's 350-minute limit may not be enough for long lessons, so run them in small batches (`limit` 1 to 3). Lessons without captions stay pending and are not written as empty files.

## Tests

```bash
python3 -m unittest tests/test_unlisted_pipeline.py
```

The tests need no network and no credentials.

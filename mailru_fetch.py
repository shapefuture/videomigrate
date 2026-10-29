#!/usr/bin/env python3
"""Fetch a single *original* MP4 from a public Cloud Mail.ru share.

This uses Mail.ru's undocumented v2 endpoint, as used by CMRD.
It does not fetch or reassemble video preview segments.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote, unquote, urlsplit

import requests

API = 'https://cloud.mail.ru/api/v2'
PAGE_ID = re.compile(r'''pageId['\"]*:\s*['\"]*([^\"'\\\s,]+)''')
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; mailru-original-fetch/1.0)'}


def parse_public_file_url(url):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname != 'cloud.mail.ru':
        raise ValueError('Expected an HTTPS cloud.mail.ru public share URL')
    segments = [unquote(s) for s in parsed.path.split('/') if s]
    if len(segments) < 5 or segments[0] != 'public':
        raise ValueError('Expected /public/<share>/<share>/<folders>/<file>.mp4')
    share_id = '/'.join(segments[1:3])
    path = segments[3:]
    filename = path[-1]
    if not filename.lower().endswith('.mp4') or filename in {'.', '..'}:
        raise ValueError('The requested target must be one .mp4 file')
    if any(part in {'', '.', '..'} or '/' in part or '\\' in part for part in path):
        raise ValueError('Invalid path component')
    return share_id, path


def encoded_path(path):
    return '/'.join(quote(part, safe='') for part in path)


def request_json(session, endpoint, params):
    response = session.get(endpoint, params=params, timeout=40)
    response.raise_for_status()
    return response.json().get('body', {})


def resolve_original_url(session, source):
    share_id, path = parse_public_file_url(source)
    root_url = 'https://cloud.mail.ru/public/' + encoded_path(share_id.split('/'))
    response = session.get(root_url, timeout=40)
    response.raise_for_status()
    match = PAGE_ID.search(response.text)
    if not match:
        raise RuntimeError('Could not obtain Mail.ru pageId; public link may require login or API may have changed')
    page_id = match.group(1)
    dispatcher = request_json(session, f'{API}/dispatcher', {'x-page-id': page_id})
    hosts = dispatcher.get('weblink_get') or []
    if not hosts:
        raise RuntimeError('Mail.ru API did not return a downloadable host')
    base = hosts[0].get('url', '')
    if not base.startswith('https://'):
        raise RuntimeError('Unexpected direct download URL scheme')
    folder_key = '/'.join([share_id, *path[:-1]])
    folder = request_json(session, f'{API}/folder', {'weblink': folder_key, 'x-page-id': page_id})
    matches = [item for item in folder.get('list', []) if item.get('name') == path[-1] and item.get('type') != 'folder']
    if len(matches) != 1:
        raise RuntimeError('The exact MP4 was not found in the resolved public folder (found %d matches)' % len(matches))
    return base.rstrip('/') + '/' + encoded_path([*share_id.split('/'), *path]), path[-1]


def download(session, url, dest):
    """Resumable HTTP download: append only on valid 206; restart on 200."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(dest.suffix + '.part')
    offset = partial.stat().st_size if partial.exists() else 0
    headers = {'Range': f'bytes={offset}-'} if offset else {}
    with session.get(url, headers=headers, stream=True, timeout=(25, 120)) as resp:
        resp.raise_for_status()
        if offset and resp.status_code == 206:
            content_range = resp.headers.get('Content-Range', '')
            if not content_range.startswith(f'bytes {offset}-'):
                raise RuntimeError('Mail.ru returned an unexpected byte-range offset')
            mode = 'ab'
        elif resp.status_code == 200:
            mode = 'wb'
        elif resp.status_code == 206 and not offset:
            if not resp.headers.get('Content-Range', '').startswith('bytes 0-'):
                raise RuntimeError('Unexpected partial response for initial transfer')
            mode = 'wb'
        else:
            raise RuntimeError(f'Unexpected download HTTP {resp.status_code}')
        with partial.open(mode) as output:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    output.write(chunk)
    if partial.stat().st_size < 1024:
        raise RuntimeError('Download too small to be a video')
    with partial.open('rb') as f:
        header = f.read(12)
    if header[4:8] != b'ftyp':
        raise RuntimeError('Downloaded data does not have an MP4 file signature')
    partial.replace(dest)


def probe_video(path):
    output = subprocess.check_output([
        'ffprobe', '-v', 'error', '-show_entries',
        'format=duration,size:stream=codec_name,codec_type', '-of', 'json', str(path)
    ], text=True, timeout=60)
    media = json.loads(output)
    if not any(x.get('codec_type') == 'video' for x in media.get('streams', [])):
        raise RuntimeError('No decodable video stream found')
    return media


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=True)
    parser.add_argument('--output', default='downloads/original.mp4')
    args = parser.parse_args()
    with requests.Session() as sess:
        sess.headers.update(HEADERS)
        direct, original_name = resolve_original_url(sess, args.url)
        print('Resolved exact original file:', original_name, flush=True)
        # The direct CDN URL may contain temporary credentials: never log it.
        download(sess, direct, args.output)
    target = Path(args.output)
    media = probe_video(target)
    digest = hashlib.sha256()
    with target.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(chunk)
    summary = {
        'original_filename': original_name,
        'downloaded_filename': target.name,
        'bytes': target.stat().st_size,
        'duration_seconds': media.get('format', {}).get('duration'),
        'sha256': digest.hexdigest(),
        'youtube_uploaded': False,
        'youtube_intended_visibility': 'unlisted',
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf8') as out:
            out.write('## Mail.ru exact-original download test\n\n')
            for k, v in summary.items():
                out.write(f'- **{k}**: `{v}`\n')


if __name__ == '__main__':
    try:
        main()
    except Exception as ex:
        print('Failed:', str(ex), file=sys.stderr)
        raise SystemExit(1)